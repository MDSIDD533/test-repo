import pytest

from app.timetraveler_agent import run_timetraveler_agent


def context(count=100):
    rows = []
    price = 100.0
    for index in range(220):
        price += 0.15
        rows.append({
            "time": 1_700_000_000.0 + index * 300,
            "open": price - 0.04,
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price,
            "volume": 10.0,
        })
    return {
        "symbol": "BTCUSDT",
        "as_of": "2026-10-21T12:00:00+00:00",
        "requested_simulations": count,
        "market_data": {
            "symbol": "BTCUSDT",
            "interval": "5m",
            "candles": rows,
            "validation": {"valid": True, "candle_count": len(rows)},
        },
    }


@pytest.mark.parametrize("count", [100, 500, 1000])
def test_timetraveler_runs_requested_number_of_scenarios(count):
    result = run_timetraveler_agent(context(count))
    assert result["simulation_count"] == count
    assert result["horizon_candles"] == 12
    assert result["method"] == "historical_return_bootstrap"
    assert result["calibrated"] is False
    assert -100 < result["p05_return_pct"] <= result["p95_return_pct"]
    assert result["scenario_positive_pct"] >= 0
    assert result["scenario_positive_pct"] <= 100


def test_timetraveler_is_reproducible_for_same_run_context():
    first = run_timetraveler_agent(context(100))
    second = run_timetraveler_agent(context(100))
    assert first["p05_return_pct"] == second["p05_return_pct"]
    assert first["median_return_pct"] == second["median_return_pct"]
    assert first["p95_return_pct"] == second["p95_return_pct"]


def test_timetraveler_refuses_unvalidated_market_data():
    data = context()
    data["market_data"]["validation"]["valid"] = False
    with pytest.raises(ValueError, match="pass validation"):
        run_timetraveler_agent(data)


def test_timetraveler_refuses_unsupported_simulation_count():
    data = context(250)
    with pytest.raises(ValueError, match="100, 500, or 1000"):
        run_timetraveler_agent(data)
