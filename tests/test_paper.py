from app import paper


def test_paper_trade_closes_at_take_profit_and_updates_equity(monkeypatch):
    monkeypatch.setattr(paper, "_trades", [])
    monkeypatch.setattr(paper, "_equity", paper.STARTING_BALANCE)
    trade = paper.open_trade({
        "signal": "BUY",
        "symbol": "TESTUSDT",
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 102.0,
        "tp2": 103.0,
        "tp3": 104.0,
    })

    closed = paper.update_prices({"TESTUSDT": 102.0})

    assert closed == 1
    updated = paper.list_trades()[0]
    assert updated["id"] == trade["id"]
    assert updated["status"] == "CLOSED"
    assert updated["exit_reason"] == "TP1"
    assert updated["exit"] == 102.0
    assert updated["pnl"] > 0
    assert paper.account()["equity"] > paper.STARTING_BALANCE


def test_paper_trade_closes_at_stop_loss_and_updates_equity(monkeypatch):
    monkeypatch.setattr(paper, "_trades", [])
    monkeypatch.setattr(paper, "_equity", paper.STARTING_BALANCE)
    paper.open_trade({
        "signal": "BUY",
        "symbol": "TESTUSDT",
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 102.0,
        "tp2": 103.0,
        "tp3": 104.0,
    })

    closed = paper.update_prices({"TESTUSDT": 98.5})

    assert closed == 1
    updated = paper.list_trades()[0]
    assert updated["status"] == "CLOSED"
    assert updated["exit_reason"] == "STOP_LOSS"
    assert updated["exit"] == 98.5
    assert updated["pnl"] < 0
    assert paper.account()["equity"] < paper.STARTING_BALANCE


def test_paper_trade_remains_open_between_stop_and_target(monkeypatch):
    monkeypatch.setattr(paper, "_trades", [])
    monkeypatch.setattr(paper, "_equity", paper.STARTING_BALANCE)
    paper.open_trade({
        "signal": "BUY",
        "symbol": "TESTUSDT",
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 102.0,
        "tp2": 103.0,
        "tp3": 104.0,
    })

    assert paper.update_prices({"TESTUSDT": 100.5}) == 0
    assert paper.list_trades()[0]["status"] == "OPEN"

def test_paper_position_size_cannot_exceed_available_cash(monkeypatch):
    monkeypatch.setattr(paper, "_trades", [])
    monkeypatch.setattr(paper, "_equity", paper.STARTING_BALANCE)
    trade = paper.open_trade({
        "signal": "BUY",
        "symbol": "TESTUSDT",
        "entry": 100.0,
        "sl": 99.9,
        "tp1": 100.1,
        "tp2": 100.2,
        "tp3": 100.3,
    })
    assert trade["quantity"] * trade["entry"] <= paper.STARTING_BALANCE


def test_paper_prevents_opening_beyond_available_cash(monkeypatch):
    monkeypatch.setattr(paper, "_trades", [])
    monkeypatch.setattr(paper, "_equity", paper.STARTING_BALANCE)
    signal = {
        "signal": "BUY",
        "symbol": "TESTUSDT",
        "entry": 100.0,
        "sl": 99.9,
        "tp1": 100.1,
        "tp2": 100.2,
        "tp3": 100.3,
    }
    paper.open_trade(signal)
    try:
        paper.open_trade({**signal, "symbol": "OTHERUSDT"})
    except ValueError as exc:
        assert "Insufficient virtual balance" in str(exc)
    else:
        raise AssertionError("Expected insufficient virtual balance to reject trade")



def test_paper_records_entry_and_exit_fees_and_net_pnl(monkeypatch):
    monkeypatch.setattr(paper, "_trades", [])
    monkeypatch.setattr(paper, "_equity", paper.STARTING_BALANCE)
    trade = paper.open_trade({
        "signal": "BUY",
        "symbol": "FEEUSDT",
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 102.0,
        "tp2": 103.0,
        "tp3": 104.0,
    })
    assert trade["entry_fee"] > 0
    assert trade["fees"] == trade["entry_fee"]

    paper.update_prices({"FEEUSDT": 102.0})
    closed = paper.list_trades()[0]

    assert closed["gross_pnl"] > 0
    assert closed["exit_fee"] > 0
    assert closed["fees"] > closed["entry_fee"]
    assert closed["pnl"] < closed["gross_pnl"]
    assert abs(paper.account()["fees_paid"] - closed["fees"]) < 1e-3


def test_paper_prevents_duplicate_open_position_for_same_symbol(monkeypatch):
    monkeypatch.setattr(paper, "_trades", [])
    monkeypatch.setattr(paper, "_equity", paper.STARTING_BALANCE)
    signal = {
        "signal": "BUY",
        "symbol": "DUPEUSDT",
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 102.0,
        "tp2": 103.0,
        "tp3": 104.0,
    }
    paper.open_trade(signal)
    try:
        paper.open_trade({**signal, "entry": 100.5})
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("Expected duplicate open position to be rejected")


def test_paper_enforces_configurable_maximum_open_trades(monkeypatch):
    monkeypatch.setattr(paper, "_trades", [])
    monkeypatch.setattr(paper, "_equity", 1_000_000.0)
    monkeypatch.setattr(paper, "MAX_OPEN_TRADES", 2)
    signal = {
        "signal": "BUY",
        "entry": 10.0,
        "sl": 9.0,
        "tp1": 10.2,
        "tp2": 10.3,
        "tp3": 10.4,
    }
    paper.open_trade({**signal, "symbol": "MAXAUSDT"})
    paper.open_trade({**signal, "symbol": "MAXBUSDT"})
    try:
        paper.open_trade({**signal, "symbol": "MAXCUSDT"})
    except ValueError as exc:
        assert "Maximum of 2" in str(exc)
    else:
        raise AssertionError("Expected maximum open-trade limit to reject trade")


def test_paper_account_reports_exposure_and_cost_configuration(monkeypatch):
    monkeypatch.setattr(paper, "_trades", [])
    monkeypatch.setattr(paper, "_equity", paper.STARTING_BALANCE)
    paper.open_trade({
        "signal": "BUY",
        "symbol": "EXPOUSDT",
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 102.0,
        "tp2": 103.0,
        "tp3": 104.0,
    })
    account = paper.account()
    assert account["open_trades"] == 1
    assert account["open_exposure"] > 0
    assert account["fee_percent_per_side"] > 0
    assert account["live_trading_enabled"] is False
    assert account["mode"] == "PAPER ONLY"
