"""Prediction stage: explicit, uncalibrated direction plus forecast metadata.

This stage never invents a point forecast or a probability. The only validated
point-forecast experiment in this repository is the separate two-minute BTC
trial runner, and its holdout evaluation recommended DO_NOT_DEPLOY.
"""
from __future__ import annotations

import math
from typing import Any


INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}
MODEL_VERSION = "rule_direction_v1"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _unavailable(context: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "symbol": str(context.get("symbol", "")).upper(),
        "interval": None,
        "as_of": context.get("as_of"),
        "forecast_created_at": context.get("as_of"),
        "horizon_candles": None,
        "horizon_seconds": None,
        "reference_price": None,
        "predicted_price": None,
        "predicted_return_pct": None,
        "direction": "UNKNOWN",
        "confidence": None,
        "technical_confidence_pct": None,
        "calibrated": False,
        "probability_tp_before_sl": None,
        "method": MODEL_VERSION,
        "features": {},
        "limitations": [reason],
        "warnings": [reason],
    }


def run_prediction_agent(context: dict[str, Any]) -> dict[str, Any]:
    analysis = context.get("market_analysis")
    if not isinstance(analysis, dict):
        return _unavailable(
            context,
            "Validated market analysis is unavailable; no forecast was produced.",
        )

    symbol = str(analysis.get("symbol", context.get("symbol", ""))).strip().upper()
    interval = str(analysis.get("interval", "")).strip()
    signal = str(analysis.get("signal", "WAIT")).upper()
    scenario = context.get("timetraveler")
    scenario = scenario if isinstance(scenario, dict) else {}

    scenario_positive = _number(scenario.get("scenario_positive_pct"))
    if scenario_positive is not None and not 0 <= scenario_positive <= 100:
        scenario_positive = None
    p05 = _number(scenario.get("p05_return_pct"))
    p95 = _number(scenario.get("p95_return_pct"))
    scenario_range = [p05, p95] if p05 is not None and p95 is not None else None

    reference_price = _number(analysis.get("entry"))
    if reference_price is None:
        reference_price = _number(analysis.get("price"))

    direction = {"BUY": "UP", "SELL": "DOWN"}.get(signal, "FLAT")
    horizon_seconds = INTERVAL_SECONDS.get(interval)
    evidence = analysis.get("evidence")
    latest_candle_time = None
    if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
        latest_candle_time = evidence[0].get("latest_candle_time")

    # Direction is a heuristic from the rule-based technical score. It is not a
    # calibrated probability, and no point price is fabricated from it.
    return {
        "status": "uncalibrated_direction_only",
        "symbol": symbol,
        "interval": interval,
        "as_of": context.get("as_of"),
        "forecast_created_at": context.get("as_of"),
        "horizon_candles": 1 if horizon_seconds else None,
        "horizon_seconds": horizon_seconds,
        "reference_price": reference_price,
        "predicted_price": None,
        "predicted_return_pct": None,
        "direction": direction,
        "technical_signal": signal if signal in {"BUY", "SELL"} else "WAIT",
        "confidence": None,
        "technical_confidence_pct": _number(analysis.get("confidence")),
        "calibrated": False,
        "probability_tp_before_sl": None,
        "scenario_positive_pct": scenario_positive,
        "scenario_range_pct": scenario_range,
        "scenario_horizon_candles": scenario.get("horizon_candles"),
        "features": {
            "technical_score": analysis.get("score"),
            "rsi": analysis.get("rsi"),
            "macd": analysis.get("macd"),
            "structure": analysis.get("structure"),
            "atr_pct": analysis.get("atr_pct"),
        },
        "data_freshness": {"latest_candle_time": latest_candle_time},
        "method": MODEL_VERSION,
        "model_version": MODEL_VERSION,
        "limitations": [
            "Direction only: no validated point price or return is produced.",
            "The displayed technical confidence is a heuristic score, not empirical accuracy.",
            "Scenario percentages describe historical bootstrap paths, not calibrated probabilities.",
            "The two-minute BTC holdout experiment did not beat the zero-return baseline; it was not deployed.",
        ],
        "warnings": [
            "This is an uncalibrated scenario, not a statistically validated prediction.",
            "No win probability is supplied; risk controls must not authorize a trade from this output.",
        ],
    }
