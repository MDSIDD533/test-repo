from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def fake_candles(symbol, interval, limit):
    price = 100.0
    rows = []
    for i in range(limit):
        price += 0.15
        rows.append({
            "time": float(i),
            "open": price - 0.04,
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price,
            "volume": 10.0,
        })
    return rows


def test_health_keeps_live_trading_disabled():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "PAPER ONLY"
    assert response.json()["live_trading_enabled"] is False


def test_analyze_endpoint_returns_signal_and_multi_timeframe(monkeypatch):
    monkeypatch.setattr(main, "candles", fake_candles)
    response = client.get("/api/analyze/TESTUSDT?interval=5m")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "TESTUSDT"
    assert data["signal"] in {"BUY", "SELL", "WAIT"}
    assert 50 <= data["confidence"] <= 90
    assert {"entry", "tp1", "tp2", "tp3", "sl", "reasons"} <= data.keys()
    assert data["multi_timeframe"] == {"15m": data["signal"], "1h": data["signal"]}
    assert isinstance(data["confirmed"], bool)


def test_candles_endpoint_returns_requested_window(monkeypatch):
    monkeypatch.setattr(main, "candles", fake_candles)
    response = client.get("/api/candles/TESTUSDT?interval=5m&limit=50")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "TESTUSDT"
    assert len(data["candles"]) == 50


def test_analyze_endpoint_reports_invalid_history(monkeypatch):
    monkeypatch.setattr(main, "candles", lambda *args: fake_candles(*args)[:20])
    response = client.get("/api/analyze/TESTUSDT?interval=5m")
    assert response.status_code == 400
    assert "35 candles" in response.json()["detail"]

def test_backtest_includes_entry_candle_for_target_exit(monkeypatch):
    def candles_with_entry_spike(symbol, interval, limit):
        rows = fake_candles(symbol, interval, limit)
        # The first backtest entry occurs at index 80. Simulate TP1 being
        # reached after that candle opens, rather than only on later candles.
        rows[80]["high"] = rows[80]["open"] + 100.0
        return rows

    monkeypatch.setattr(main, "candles", candles_with_entry_spike)
    response = client.post("/api/backtest/TESTUSDT?interval=5m&limit=100")
    assert response.status_code == 200
    data = response.json()
    assert data["trades"] > 0
    assert data["results"][0]["outcome"] == "WIN"
    assert data["final_balance"] > data["initial_balance"]
    assert "excludes fees, slippage" in data["warning"]

def test_backtest_uses_open_price_when_target_is_gapped_over(monkeypatch):
    gap_open = {}

    def candles_with_target_gap(symbol, interval, limit):
        rows = fake_candles(symbol, interval, limit)
        rows[81]["open"] = rows[80]["open"] + 100.0
        gap_open["price"] = rows[81]["open"]
        return rows

    monkeypatch.setattr(main, "candles", candles_with_target_gap)
    response = client.post("/api/backtest/TESTUSDT?interval=5m&limit=100")
    assert response.status_code == 200
    data = response.json()
    assert data["trades"] > 0
    assert data["results"][0]["outcome"] == "WIN"
    assert data["results"][0]["exit"] == gap_open["price"]

def test_paper_refresh_uses_latest_market_prices(monkeypatch):
    monkeypatch.setattr(main, "all_24h_tickers", lambda: [
        {"symbol": "BTCUSDT", "price": 65000.0},
        {"symbol": "ETHUSDT", "price": 3200.0},
    ])
    seen = {}
    def fake_update_prices(prices):
        seen.update(prices)
        return 1
    monkeypatch.setattr(main.paper, "update_prices", fake_update_prices)

    response = client.post("/api/paper/refresh")

    assert response.status_code == 200
    assert response.json()["updated"] is True
    assert response.json()["closed_trades"] == 1
    assert seen == {"BTCUSDT": 65000.0, "ETHUSDT": 3200.0}

def test_backtest_does_not_overlap_simulated_positions(monkeypatch):
    monkeypatch.setattr(main, "candles", fake_candles)
    response = client.post("/api/backtest/TESTUSDT?interval=5m&limit=150")
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) > 1
    for previous, current in zip(results, results[1:]):
        assert current["entry_candle_index"] > previous["exit_candle_index"]



def test_agent_pipeline_api_runs_available_stages_and_fails_closed(monkeypatch):
    import time

    def fresh_candles(symbol, interval, limit):
        now = time.time()
        rows = []
        for index in range(limit):
            price = 100.0 + index * 0.15
            rows.append({
                "time": now - (limit - index) * 300,
                "open": price - 0.04,
                "high": price + 0.2,
                "low": price - 0.2,
                "close": price,
                "volume": 10.0,
            })
        return rows

    monkeypatch.setattr(main, "candles", fresh_candles)
    response = client.get("/api/agents/run/BTCUSDT?interval=5m&simulation_count=500")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "BTCUSDT"
    assert data["action"] == "NO TRADE"
    assert data["status"] == "incomplete"
    assert data["stages"][0]["stage"] == "market_data"
    assert data["stages"][0]["status"] == "completed"
    assert data["stages"][1]["stage"] == "market_analysis"
    assert data["stages"][1]["status"] == "completed"
    # TimeTraveler is implemented and runs before Research in the current pipeline.
    assert data["stages"][2]["stage"] == "timetraveler"
    assert data["stages"][2]["status"] == "completed"
    assert data["stages"][3]["stage"] == "research"
    assert data["stages"][3]["status"] == "not_configured"
    # The pipeline must fail closed until the required downstream agents exist.
    assert data["action"] == "NO TRADE"


def test_agent_pipeline_api_rejects_unsupported_simulation_count():
    response = client.get("/api/agents/run/BTCUSDT?simulation_count=250")
    assert response.status_code == 400
    assert "100, 500, or 1000" in response.json()["detail"]
