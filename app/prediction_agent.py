"""Prediction stage that reports transparent, explicitly uncalibrated scenarios."""
from __future__ import annotations

from typing import Any


def run_prediction_agent(context: dict[str, Any]) -> dict[str, Any]:
    analysis = context.get("market_analysis")
    simulation = context.get("timetraveler")
    if not isinstance(analysis, dict):
        return {
            "status": "unavailable",
            "calibrated": False,
            "probability_tp_before_sl": None,
            "direction": "UNKNOWN",
            "warnings": ["Validated market analysis is unavailable; no forecast produced."],
        }

    signal = str(analysis.get("signal", "WAIT")).upper()
    scenario_positive = None
    if isinstance(simulation, dict):
        value = simulation.get("scenario_positive_pct")
        if isinstance(value, (int, float)) and 0 <= value <= 100:
            scenario_positive = float(value)

    # Scenario frequency is deliberately not converted into a win probability.
    return {
        "status": "scenario_only",
        "symbol": str(analysis.get("symbol", context.get("symbol", ""))).upper(),
        "interval": analysis.get("interval"),
        "direction": signal if signal in {"BUY", "SELL"} else "NEUTRAL",
        "technical_confidence_pct": analysis.get("confidence"),
        "scenario_positive_pct": scenario_positive,
        "calibrated": False,
        "probability_tp_before_sl": None,
        "method": "rule_based_direction_plus_historical_bootstrap_context",
        "warnings": [
            "This is an uncalibrated scenario, not a statistically validated prediction.",
            "No win probability is supplied; risk controls must not authorize a trade from this output.",
        ],
    }
