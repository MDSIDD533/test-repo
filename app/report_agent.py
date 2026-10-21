"""Report stage summarizes actual stage outputs and explicit blockers."""
from __future__ import annotations

from typing import Any


def run_report_agent(context: dict[str, Any]) -> dict[str, Any]:
    statuses = context.get("_stage_statuses", {})
    if not isinstance(statuses, dict):
        statuses = {}
    risk = context.get("risk_decision")
    decision = risk.get("action", "NO TRADE") if isinstance(risk, dict) else "NO TRADE"
    completed = [name for name, status in statuses.items() if status == "completed"]
    blocked = [name for name, status in statuses.items() if status != "completed"]
    return {
        "symbol": str(context.get("symbol", "")).upper(),
        "run_id": context.get("run_id"),
        "as_of": context.get("as_of"),
        "decision": decision,
        "mode": "PAPER ONLY",
        "live_orders_enabled": False,
        "completed_stages": completed,
        "incomplete_stages": blocked,
        "stage_statuses": dict(statuses),
        "summary": (
            f"{len(completed)} stages completed; {len(blocked)} stages incomplete. "
            f"Decision: {decision}."
        ),
        "warnings": ["Report summarizes this run only; it is not investment advice."],
    }
