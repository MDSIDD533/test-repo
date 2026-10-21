from app.prediction_agent import run_prediction_agent
from app.risk_decision_agent import run_risk_decision_agent
from app.report_agent import run_report_agent


def test_prediction_never_claims_calibrated_probability():
    output = run_prediction_agent({
        "symbol": "BTCUSDT",
        "market_analysis": {"symbol": "BTCUSDT", "interval": "5m", "signal": "BUY", "confidence": 75},
        "timetraveler": {"scenario_positive_pct": 64.0},
    })
    assert output["direction"] == "BUY"
    assert output["calibrated"] is False
    assert output["probability_tp_before_sl"] is None


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
        "risk_decision": {"action": "NO TRADE"},
    })
    assert output["decision"] == "NO TRADE"
    assert output["completed_stages"] == ["market_data"]
    assert output["incomplete_stages"] == ["research"]
    assert output["live_orders_enabled"] is False
