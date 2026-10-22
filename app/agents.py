"""Testable orchestration for the fail-closed multi-agent pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
import uuid


PIPELINE_STAGES = (
    "market_data",
    "market_analysis",
    "research",
    "prediction",
    "risk_decision",
    "timetraveler",
    "report",
)


@dataclass
class StageResult:
    stage: str
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineReport:
    run_id: str
    symbol: str
    as_of: str
    status: str
    action: str
    stages: list[StageResult]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "status": self.status,
            "action": self.action,
            "stages": [stage.to_dict() for stage in self.stages],
            "warnings": list(self.warnings),
        }


Agent = Callable[[dict[str, Any]], dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_agent_pipeline(
    symbol: str,
    agents: dict[str, Agent],
    *,
    run_id: str | None = None,
    as_of: str | None = None,
    simulation_count: int = 100,
) -> PipelineReport:
    """Run every stage so one unavailable provider does not hide later blockers.

    Stages must explicitly handle missing upstream outputs. Missing/failed
    stages are recorded and downstream stages may report safe NO TRADE results.
    This coordinator never fabricates agent output.
    """
    normalized_symbol = symbol.strip().upper()
    if simulation_count not in {100, 500, 1000}:
        raise ValueError("simulation_count must be one of 100, 500, or 1000")
    run_id = run_id or str(uuid.uuid4())
    as_of = as_of or _utc_now()
    context: dict[str, Any] = {
        "run_id": run_id,
        "symbol": normalized_symbol,
        "as_of": as_of,
        "requested_simulations": simulation_count,
        "_stage_statuses": {},
        "_stage_errors": {},
    }
    results: list[StageResult] = []
    warnings: list[str] = []
    action = "NO TRADE"

    if not normalized_symbol:
        return PipelineReport(
            run_id=run_id,
            symbol="",
            as_of=as_of,
            status="invalid_request",
            action=action,
            stages=[],
            warnings=["A non-empty symbol is required."],
        )

    for stage_name in PIPELINE_STAGES:
        agent = agents.get(stage_name)
        if agent is None:
            message = f"Agent '{stage_name}' is not implemented/configured."
            result = StageResult(
                stage=stage_name,
                status="not_configured",
                warnings=[message],
                started_at=_utc_now(),
                completed_at=_utc_now(),
            )
            results.append(result)
            context["_stage_statuses"][stage_name] = result.status
            context["_stage_errors"][stage_name] = message
            warnings.append(message)
            continue

        started = _utc_now()
        try:
            output = agent(dict(context))
            if not isinstance(output, dict):
                raise TypeError("Agent output must be a dictionary.")
            context[stage_name] = output
            reported_status = output.get("status")
            stage_status = (
                reported_status
                if reported_status in {"unavailable", "not_configured", "blocked"}
                else "completed"
            )
            result = StageResult(
                stage=stage_name,
                status=stage_status,
                result=output,
                evidence=output.get("evidence", []) if isinstance(output.get("evidence", []), list) else [],
                warnings=output.get("warnings", []) if isinstance(output.get("warnings", []), list) else [],
                started_at=started,
                completed_at=_utc_now(),
            )
            results.append(result)
            context["_stage_statuses"][stage_name] = result.status
            warnings.extend(result.warnings)
        except Exception as exc:
            # Agents can mark expected unavailability (for example, no news on
            # a quiet day) as `unavailable` without pretending it succeeded.
            stage_status = getattr(exc, "stage_status", "failed")
            message = f"{stage_name} {stage_status}: {type(exc).__name__}: {exc}"
            result = StageResult(
                stage=stage_name,
                status=stage_status,
                errors=[message],
                started_at=started,
                completed_at=_utc_now(),
            )
            results.append(result)
            context["_stage_statuses"][stage_name] = result.status
            context["_stage_errors"][stage_name] = message
            warnings.append(message)

    all_completed = len(results) == len(PIPELINE_STAGES) and all(
        item.status == "completed" for item in results
    )
    if all_completed:
        decision = context.get("risk_decision", {})
        prediction = context.get("prediction", {})
        calibrated = prediction.get("calibrated") is True
        probability = prediction.get("probability_tp_before_sl")
        try:
            probability_is_valid = probability is not None and 0 <= float(probability) <= 1
        except (TypeError, ValueError):
            probability_is_valid = False
        authorized = decision.get("authorized") is True
        if (
            calibrated
            and probability_is_valid
            and authorized
            and decision.get("action") in {"BUY", "HOLD", "EXIT", "NO TRADE"}
        ):
            action = decision["action"]
            status = "completed" if action != "NO TRADE" else "completed_no_trade"
        else:
            if not calibrated or not probability_is_valid:
                warnings.append(
                    "Prediction is not demonstrably calibrated or probability is invalid; action forced to NO TRADE."
                )
            elif not authorized:
                warnings.append(
                    "Risk decision did not explicitly authorize a trade; action forced to NO TRADE."
                )
            else:
                warnings.append("Risk decision action is invalid; action forced to NO TRADE.")
            status = "completed_no_trade"
    elif any(item.status == "failed" for item in results):
        status = "failed"
    else:
        status = "incomplete"

    # A downstream agent cannot override the safety invariant.
    if not all_completed or status != "completed":
        action = "NO TRADE"

    return PipelineReport(
        run_id=run_id,
        symbol=normalized_symbol,
        as_of=as_of,
        status=status,
        action=action,
        stages=results,
        warnings=warnings,
    )
