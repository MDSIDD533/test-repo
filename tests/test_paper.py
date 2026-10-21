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
