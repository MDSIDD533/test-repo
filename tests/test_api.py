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
    assert "includes configurable fees and slippage" in data["warning"]
    assert data["fee_bps_per_side"] == 10.0
    assert data["slippage_bps_per_side"] == 0.0

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

    from datetime import datetime, timezone

    monkeypatch.setattr(main, "candles", fresh_candles)
    monkeypatch.setattr(main, "fetch_crypto_news", lambda symbol, as_of: [{
        "title": "Bitcoin market update",
        "source": "Test RSS",
        "url": "https://example.com/bitcoin-market-update",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "summary": "Synthetic test fixture; not live research.",
    }])
    response = client.get("/api/agents/run/BTCUSDT?interval=5m&simulation_count=500")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "BTCUSDT"
    assert data["action"] == "NO TRADE"
    assert data["status"] == "completed_no_trade"
    assert [stage["stage"] for stage in data["stages"]] == [
        "market_data", "market_analysis", "timetraveler", "research",
        "prediction", "risk_decision", "report",
    ]
    assert all(stage["status"] == "completed" for stage in data["stages"])
    assert data["stages"][0]["result"]["validation"]["valid"] is True
    assert data["stages"][2]["result"]["simulation_count"] == 500
    assert data["stages"][3]["result"]["items_accepted"] == 1
    assert data["stages"][4]["result"]["calibrated"] is False
    assert data["stages"][5]["result"]["action"] == "NO TRADE"
    assert data["stages"][5]["result"]["authorized"] is False
    assert data["stages"][6]["result"]["decision"] == "NO TRADE"
    assert data["stages"][4]["result"]["direction"] in {"UP", "DOWN", "FLAT"}
    assert data["stages"][4]["result"]["calibrated"] is False
    assert data["stages"][4]["result"]["predicted_price"] is None
    assert data["stages"][5]["result"]["authorized"] is False
    assert data["stages"][6]["result"]["data_validation"]["valid"] is True
    assert data["stages"][6]["result"]["blockers"]


def test_agent_pipeline_api_rejects_unsupported_simulation_count():
    response = client.get("/api/agents/run/BTCUSDT?simulation_count=250")
    assert response.status_code == 400
    assert "100, 500, or 1000" in response.json()["detail"]


def test_chart_pages_and_core_script_are_served():
    chart = client.get("/chart")
    assert chart.status_code == 200
    assert "chart-core.js" in chart.text
    assert "Full Market Chart" in chart.text

    core = client.get("/chart-core.js")
    assert core.status_code == 200
    assert "javascript" in core.headers["content-type"]
    assert "class LiveStream" in core.text
    assert "normalizeKline" in core.text


def test_agent_pipeline_reports_missing_research_as_unavailable_not_fabricated(monkeypatch):
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
    monkeypatch.setattr(main, "fetch_crypto_news", lambda symbol, as_of: [])
    response = client.get("/api/agents/run/BTCUSDT?interval=5m&simulation_count=100")
    assert response.status_code == 200
    data = response.json()
    research_stage = data["stages"][3]
    assert research_stage["status"] == "unavailable"
    assert any("No research evidence" in error for error in research_stage["errors"])
    assert data["status"] == "incomplete"
    assert data["action"] == "NO TRADE"
    assert data["stages"][6]["result"]["decision"] == "NO TRADE"
    assert any("research" in blocker.lower() for blocker in data["stages"][6]["result"]["blockers"])


def test_backtest_costs_reduce_reported_returns_and_final_balance(monkeypatch):
    monkeypatch.setattr(main, "candles", fake_candles)

    free = client.post("/api/backtest/TESTUSDT?interval=5m&limit=150&fee_bps=0&slippage_bps=0")
    costly = client.post("/api/backtest/TESTUSDT?interval=5m&limit=150&fee_bps=50&slippage_bps=25")

    assert free.status_code == 200
    assert costly.status_code == 200
    free_data = free.json()
    costly_data = costly.json()
    assert free_data["trades"] > 0 and costly_data["trades"] > 0
    assert costly_data["final_balance"] < free_data["final_balance"]
    assert costly_data["results"][0]["return_pct"] < costly_data["results"][0]["gross_return_pct"]
    assert costly_data["results"][0]["fees_and_costs_pct"] > 0


def test_recorded_forecast_evaluation_is_served_without_hiding_failure():
    response = client.get("/api/reports/forecast-evaluation")
    assert response.status_code == 200
    data = response.json()
    assert data["batch_run_id"] == 39610582675
    assert data["valid_trials"] == 100
    assert data["decision"] == "DO_NOT_DEPLOY"
    assert data["candidate_beats_baseline_on_holdout"] is False
    assert (
        data["selected_candidate_holdout_metrics"]["mean_absolute_price_error_pct"]
        > data["zero_return_holdout_metrics"]["mean_absolute_price_error_pct"]
    )


def test_backtest_applies_stop_first_when_one_candle_touches_both_levels(monkeypatch):
    def both_levels_touched(symbol, interval, limit):
        rows = fake_candles(symbol, interval, limit)
        # The entry candle opens between the levels, then trades through the
        # stop and the target intrabar. The conservative rule must record a loss.
        rows[80]["high"] = rows[80]["open"] + 100.0
        rows[80]["low"] = 0.01
        return rows

    monkeypatch.setattr(main, "candles", both_levels_touched)
    response = client.post("/api/backtest/TESTUSDT?interval=5m&limit=100")
    assert response.status_code == 200
    data = response.json()
    assert data["trades"] > 0
    assert data["results"][0]["outcome"] == "LOSS"
