"""Risk gate: paper-only, fail-closed, with explicit reward/risk diagnostics.

The gate reports the metrics it computed even when it refuses a trade. Missing
calibration is enough to force NO TRADE on its own.
"""
from __future__ import annotations

import math
import os
from typing import Any


def _limits(overrides: dict[str, Any] | None) -> dict[str, float]:
    values = {
        "min_reward_risk": float(os.getenv("PAPER_MIN_REWARD_RISK", "1.5")),
        "max_atr_pct": float(os.getenv("PAPER_MAX_ATR_PCT", "6.0")),
        "max_risk_per_trade_pct": float(os.getenv("PAPER_RISK_PERCENT", "1.0")),
        "min_calibrated_probability": float(os.getenv("PAPER_MIN_CALIBRATED_PROBABILITY", "0.55")),
    }
    for key, value in (overrides or {}).items():
        try:
            values[key] = float(value)
        except (TypeError, ValueError):
            continue
    return values


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def run_risk_decision_agent(
    context: dict[str, Any],
    *,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = context.get("market_analysis")
    prediction = context.get("prediction")
    research = context.get("research")
    configured = _limits(limits)
    reasons: list[str] = []
    checks: dict[str, bool] = {}
    metrics: dict[str, float | None] = {
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_per_unit": None,
        "risk_pct_of_entry": None,
        "reward_pct_of_entry": None,
        "reward_risk_ratio": None,
        "atr_pct": None,
        "calibrated_probability": None,
    }

    if not isinstance(analysis, dict):
        reasons.append("Market analysis is unavailable.")
        checks["analysis_available"] = False
    else:
        checks["analysis_available"] = True
        entry = _number(analysis.get("entry"))
        stop = _number(analysis.get("sl"))
        target = _number(analysis.get("tp1"))
        atr_pct = _number(analysis.get("atr_pct"))
        if None not in (entry, stop, target) and entry > 0:
            risk_per_unit = entry - stop
            reward_per_unit = target - entry
            metrics.update({
                "entry": round(entry, 8),
                "stop_loss": round(stop, 8),
                "take_profit": round(target, 8),
                "risk_per_unit": round(risk_per_unit, 8),
                "risk_pct_of_entry": round(risk_per_unit / entry * 100, 5),
                "reward_pct_of_entry": round(reward_per_unit / entry * 100, 5),
                "reward_risk_ratio": (
                    round(reward_per_unit / risk_per_unit, 4) if risk_per_unit > 0 else None
                ),
                "atr_pct": round(atr_pct, 4) if atr_pct is not None else None,
            })
            ratio = metrics["reward_risk_ratio"]
            checks["reward_risk_above_minimum"] = bool(
                ratio is not None and ratio >= configured["min_reward_risk"]
            )
            if not checks["reward_risk_above_minimum"]:
                reasons.append(
                    f"Reward/risk {ratio if ratio is not None else 'unavailable'} is below the "
                    f"configured minimum {configured['min_reward_risk']}."
                )
            checks["volatility_within_limit"] = bool(
                atr_pct is not None and atr_pct <= configured["max_atr_pct"]
            )
            if not checks["volatility_within_limit"]:
                reasons.append(
                    f"ATR volatility {atr_pct if atr_pct is not None else 'unavailable'}% "
                    f"exceeds the configured limit {configured['max_atr_pct']}%."
                )
        else:
            checks["reward_risk_above_minimum"] = False
            checks["volatility_within_limit"] = False
            reasons.append("Entry, stop-loss and take-profit levels are incomplete or invalid.")

    if not isinstance(research, dict) or research.get("status") != "evidence_available":
        checks["research_available"] = False
        reasons.append("Fresh, sourced research evidence is unavailable.")
    else:
        checks["research_available"] = True

    if not isinstance(prediction, dict):
        checks["prediction_calibrated"] = False
        checks["probability_valid"] = False
        reasons.append("Prediction stage output is unavailable.")
    else:
        calibrated = prediction.get("calibrated") is True
        checks["prediction_calibrated"] = calibrated
        if not calibrated:
            reasons.append("Prediction has not been calibrated and independently validated.")
        probability = _number(prediction.get("probability_tp_before_sl"))
        metrics["calibrated_probability"] = probability
        probability_valid = probability is not None and 0.0 <= probability <= 1.0
        checks["probability_valid"] = probability_valid
        if not probability_valid:
            reasons.append("A valid calibrated TP-before-SL probability is missing.")
        elif probability < configured["min_calibrated_probability"]:
            reasons.append(
                f"Calibrated probability {probability} is below the configured minimum "
                f"{configured['min_calibrated_probability']}."
            )

    # Paper-only invariant: this stage never authorizes an automated order. A
    # future deployment needs a separate calibrated model, independent holdout,
    # and a reviewed safety process.
    action = "NO TRADE"
    return {
        "action": action,
        "authorized": False,
        "mode": "PAPER ONLY",
        "live_orders_enabled": False,
        "risk_per_trade_pct": 0.0,
        "limits": configured,
        "metrics": metrics,
        "checks": checks,
        "reasons": reasons or ["Automated trade authorization is disabled pending independent validation."],
        "warnings": ["This stage is a safety gate only; it does not place orders."],
    }
