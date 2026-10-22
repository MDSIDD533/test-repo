"""Report stage: combine real stage outputs, warnings, errors and blockers."""
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def run_report_agent(context: dict[str, Any]) -> dict[str, Any]:
    statuses = context.get("_stage_statuses", {})
    if not isinstance(statuses, dict):
        statuses = {}
    stage_errors = context.get("_stage_errors", {})
    if not isinstance(stage_errors, dict):
        stage_errors = {}

    market_data = _dict(context.get("market_data"))
    analysis = _dict(context.get("market_analysis"))
    research = _dict(context.get("research"))
    prediction = _dict(context.get("prediction"))
    risk = _dict(context.get("risk_decision"))
    timetraveler = _dict(context.get("timetraveler"))
    validation = _dict(market_data.get("validation"))

    decision = str(risk.get("action", "NO TRADE"))
    completed = [name for name, status in statuses.items() if status == "completed"]
    incomplete = [name for name, status in statuses.items() if status != "completed"]
    # The report must never claim an action that the coordinator would have
    # forced back to NO TRADE for missing stages, calibration, or authorization.
    if (
        incomplete
        or prediction.get("calibrated") is not True
        or risk.get("authorized") is not True
    ):
        decision = "NO TRADE"

    blockers: list[str] = []
    if validation.get("valid") is not True:
        blockers.append("Market data did not pass validation or was not provided.")
    if research.get("status") != "evidence_available":
        blockers.append("Fresh, source-attributed research evidence is unavailable.")
    if prediction.get("calibrated") is not True:
        blockers.append("Prediction is not calibrated on out-of-sample outcomes.")
    if risk.get("authorized") is not True:
        blockers.append("Risk gate did not authorize a trade.")
    if decision == "NO TRADE" and not blockers:
        blockers.append("Risk gate returned NO TRADE.")

    warnings: list[str] = []
    for key in ("market_data", "market_analysis", "timetraveler", "research", "prediction", "risk_decision"):
        stage = _dict(context.get(key))
        raw = stage.get("warnings")
        if isinstance(raw, list):
            warnings.extend(str(item) for item in raw)
    warnings.extend(str(item) for item in stage_errors.values())
    warnings.append("Report summarizes this run only; it is not investment advice.")
    warnings = _dedupe(warnings)

    evidence: list[dict[str, Any]] = []
    market_evidence = market_data.get("evidence")
    if isinstance(market_evidence, list):
        evidence.extend(item for item in market_evidence if isinstance(item, dict))
    analysis_evidence = analysis.get("evidence")
    if isinstance(analysis_evidence, list):
        evidence.extend(item for item in analysis_evidence if isinstance(item, dict))
    research_evidence = research.get("evidence")
    if isinstance(research_evidence, list):
        evidence.extend(item for item in research_evidence if isinstance(item, dict))
    simulation_evidence = timetraveler.get("evidence")
    if isinstance(simulation_evidence, list):
        evidence.extend(item for item in simulation_evidence if isinstance(item, dict))

    validation_summary = {
        "valid": validation.get("valid") is True,
        "candle_count": validation.get("candle_count"),
        "latest_candle_time": validation.get("latest_candle_time"),
        "age_seconds": validation.get("age_seconds"),
        "checks": validation.get("checks", []),
    }
    prediction_summary = {
        "status": prediction.get("status"),
        "direction": prediction.get("direction"),
        "forecast_created_at": prediction.get("forecast_created_at"),
        "horizon_seconds": prediction.get("horizon_seconds"),
        "reference_price": prediction.get("reference_price"),
        "predicted_price": prediction.get("predicted_price"),
        "calibrated": prediction.get("calibrated") is True,
        "probability_tp_before_sl": prediction.get("probability_tp_before_sl"),
        "model_version": prediction.get("model_version") or prediction.get("method"),
        "limitations": prediction.get("limitations", []),
    }

    return {
        "symbol": str(context.get("symbol", "")).upper(),
        "run_id": context.get("run_id"),
        "as_of": context.get("as_of"),
        "decision": decision,
        "authorized": risk.get("authorized") is True,
        "mode": "PAPER ONLY",
        "live_orders_enabled": False,
        "completed_stages": completed,
        "incomplete_stages": incomplete,
        "stage_statuses": dict(statuses),
        "data_validation": validation_summary,
        "market_analysis": {
            "signal": analysis.get("signal"),
            "technical_confidence_pct": analysis.get("confidence"),
            "entry": analysis.get("entry"),
            "stop_loss": analysis.get("sl"),
            "take_profit_1": analysis.get("tp1"),
            "take_profit_2": analysis.get("tp2"),
            "take_profit_3": analysis.get("tp3"),
            "reasons": analysis.get("reasons", []),
        },
        "research": {
            "status": research.get("status", "unavailable"),
            "items_accepted": research.get("items_accepted", 0),
            "sources": [
                item.get("url") for item in research.get("evidence", [])
                if isinstance(item, dict) and item.get("url")
            ][:10],
        },
        "prediction": prediction_summary,
        "risk_decision": {
            "action": decision,
            "authorized": risk.get("authorized") is True,
            "reasons": risk.get("reasons", []),
            "metrics": risk.get("metrics", {}),
            "limits": risk.get("limits", {}),
        },
        "simulation": {
            "simulation_only": timetraveler.get("simulation_only") is True,
            "not_an_observation": timetraveler.get("not_an_observation") is True,
            "simulation_count": timetraveler.get("simulation_count"),
            "horizon_candles": timetraveler.get("horizon_candles"),
            "median_return_pct": timetraveler.get("median_return_pct"),
            "p05_return_pct": timetraveler.get("p05_return_pct"),
            "p95_return_pct": timetraveler.get("p95_return_pct"),
            "scenario_positive_pct": timetraveler.get("scenario_positive_pct"),
            "warning": "Scenario frequencies are not calibrated win probabilities.",
        },
        "blockers": blockers,
        "evidence": evidence,
        "warnings": warnings,
        "summary": (
            f"{len(completed)} stages completed; {len(incomplete)} stages incomplete. "
            f"Decision: {decision}. Blockers: " + ("; ".join(blockers) if blockers else "none")
        ),
    }
