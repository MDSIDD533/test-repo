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

