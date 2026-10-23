from app.prediction_agent import run_prediction_agent
from app.risk_decision_agent import run_risk_decision_agent
from app.report_agent import run_report_agent


def test_prediction_never_claims_calibrated_probability():
    output = run_prediction_agent({
        "symbol": "BTCUSDT",
        "as_of": "2026-10-21T12:00:00Z",
        "market_analysis": {"symbol": "BTCUSDT", "interval": "5m", "signal": "BUY", "confidence": 75, "entry": 100.0},
        "timetraveler": {"scenario_positive_pct": 64.0, "p05_return_pct": -1.2, "p95_return_pct": 1.5, "horizon_candles": 12},
    })
    assert output["direction"] == "UP"
    assert output["calibrated"] is False
    assert output["probability_tp_before_sl"] is None
    assert output["horizon_candles"] == 1
    assert output["horizon_seconds"] == 300
    assert output["reference_price"] == 100.0
    assert output["predicted_price"] is None
    assert output["forecast_created_at"] == "2026-10-21T12:00:00Z"
    assert output["model_version"] == "rule_direction_v1"


def test_risk_decision_fails_closed_without_research_and_calibration():
    output = run_risk_decision_agent({
        "market_analysis": {"signal": "BUY"},
        "prediction": {"calibrated": False, "probability_tp_before_sl": None},
    })
    assert output["action"] == "NO TRADE"
    assert output["authorized"] is False
    assert output["live_orders_enabled"] is False
    assert any("research" in reason.lower() for reason in output["reasons"])
    assert any("calibrated" in reason.lower() for reason in output["reasons"])


def test_report_summarizes_actual_stage_statuses():
    output = run_report_agent({
        "symbol": "ETHUSDT",
        "run_id": "fixture",
        "as_of": "2026-01-01T00:00:00Z",
        "_stage_statuses": {"market_data": "completed", "research": "failed"},
        "_stage_errors": {"research": "research failed: provider unavailable"},
        "risk_decision": {"action": "NO TRADE"},
    })
    assert output["decision"] == "NO TRADE"
    assert output["completed_stages"] == ["market_data"]
    assert output["incomplete_stages"] == ["research"]
    assert output["live_orders_enabled"] is False
    assert output["data_validation"]["valid"] is False
    assert output["research"]["status"] == "unavailable"
    assert any("research" in blocker.lower() for blocker in output["blockers"])
    assert any("provider unavailable" in warning for warning in output["warnings"])


def test_risk_gate_reports_reward_risk_and_volatility_checks_but_still_blocks():
    output = run_risk_decision_agent(
        {
            "market_analysis": {
                "entry": 100.0,
                "sl": 99.0,
                "tp1": 102.0,
                "atr_pct": 1.5,
            },
            "research": {"status": "evidence_available"},
            "prediction": {"calibrated": False, "probability_tp_before_sl": None},
        },
        limits={"min_reward_risk": 1.5, "max_atr_pct": 6.0},
    )
    assert output["metrics"]["reward_risk_ratio"] == 2.0
    assert output["metrics"]["risk_pct_of_entry"] == 1.0
    assert output["checks"]["reward_risk_above_minimum"] is True
    assert output["checks"]["volatility_within_limit"] is True
    assert output["checks"]["research_available"] is True
    assert output["checks"]["prediction_calibrated"] is False
    assert output["action"] == "NO TRADE"
    assert output["authorized"] is False
    assert output["risk_per_trade_pct"] == 0.0


def test_risk_gate_flags_excessive_volatility_and_weak_reward_risk():
    output = run_risk_decision_agent(
        {
            "market_analysis": {"entry": 100.0, "sl": 98.0, "tp1": 101.0, "atr_pct": 8.5},
            "research": {"status": "evidence_available"},
            "prediction": {"calibrated": True, "probability_tp_before_sl": 0.4},
        },
        limits={"min_reward_risk": 1.5, "max_atr_pct": 6.0, "min_calibrated_probability": 0.55},
    )
    assert output["checks"]["reward_risk_above_minimum"] is False
    assert output["checks"]["volatility_within_limit"] is False
    assert any("Reward/risk" in reason for reason in output["reasons"])
    assert any("ATR volatility" in reason for reason in output["reasons"])
    assert any("below the configured minimum" in reason for reason in output["reasons"])
    assert output["action"] == "NO TRADE"


def test_report_includes_labelled_simulation_summary():
    output = run_report_agent({
        "symbol": "BTCUSDT",
        "run_id": "fixture",
        "as_of": "2026-01-01T00:00:00Z",
        "_stage_statuses": {"timetraveler": "completed"},
        "timetraveler": {
            "simulation_only": True,
            "not_an_observation": True,
            "simulation_count": 500,
            "horizon_candles": 12,
            "median_return_pct": 0.2,
            "p05_return_pct": -1.5,
            "p95_return_pct": 2.0,
            "scenario_positive_pct": 55.0,
        },
        "risk_decision": {"action": "NO TRADE", "authorized": False},
    })
    assert output["simulation"]["simulation_only"] is True
    assert output["simulation"]["not_an_observation"] is True
    assert output["simulation"]["simulation_count"] == 500
    assert "not calibrated" in output["simulation"]["warning"]
