"""Risk gate. It authorizes no trade without validated, calibrated prediction evidence."""
from __future__ import annotations

from typing import Any


def run_risk_decision_agent(context: dict[str, Any]) -> dict[str, Any]:
    analysis = context.get("market_analysis")
    prediction = context.get("prediction")
    research = context.get("research")
    reasons: list[str] = []

    if not isinstance(analysis, dict):
        reasons.append("Market analysis is unavailable.")
    if not isinstance(research, dict) or research.get("status") != "evidence_available":
        reasons.append("Fresh, sourced research evidence is unavailable.")
    if not isinstance(prediction, dict):
        reasons.append("Prediction stage output is unavailable.")
    else:
        if prediction.get("calibrated") is not True:
            reasons.append("Prediction has not been calibrated and independently validated.")
        probability = prediction.get("probability_tp_before_sl")
        try:
            probability_valid = probability is not None and 0.0 <= float(probability) <= 1.0
        except (TypeError, ValueError):
            probability_valid = False
        if not probability_valid:
            reasons.append("A valid calibrated TP-before-SL probability is missing.")

    # Even when every input exists, this implementation remains paper-only.
    # No automated order is ever sent by this stage.
    action = "NO TRADE"
    return {
        "action": action,
        "authorized": False,
        "mode": "PAPER ONLY",
        "live_orders_enabled": False,
        "risk_per_trade_pct": 0.0,
        "reasons": reasons or ["Automated trade authorization is disabled pending independent validation."],
        "warnings": ["This stage is a safety gate only; it does not place orders."],
    }
