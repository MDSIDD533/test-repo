"""Small, testable orchestration primitives for the multi-agent pipeline.

Domain agents are injected as callables so the pipeline can be tested without
network access and can degrade safely when an individual provider fails.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
import uuid


PIPELINE_STAGES = (
    "market_data",
    "market_analysis",
    "timetraveler",
    "research",
    "prediction",
    "risk_decision",
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
    """Run injected stages in order; stop safely when a stage is missing/fails.

    This is orchestration only. It does not fabricate research, forecasts, or
    simulation outcomes when domain agents have not yet been implemented.
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
    }
    results: list[StageResult] = []
    warnings: list[str] = []
    action = "NO TRADE"
    overall_status = "blocked"

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
            results.append(StageResult(
                stage=stage_name,
                status="not_configured",
                warnings=[message],
                started_at=_utc_now(),
                completed_at=_utc_now(),
            ))
            warnings.append(message)
            overall_status = "incomplete"
            break

        started = _utc_now()
        try:
            output = agent(dict(context))
            if not isinstance(output, dict):
                raise TypeError("Agent output must be a dictionary.")
            context[stage_name] = output
            stage_result = StageResult(
                stage=stage_name,
                status="completed",
                result=output,
                evidence=output.get("evidence", []) if isinstance(output.get("evidence", []), list) else [],
                warnings=output.get("warnings", []) if isinstance(output.get("warnings", []), list) else [],
                started_at=started,
                completed_at=_utc_now(),
            )
            results.append(stage_result)
            warnings.extend(stage_result.warnings)
        except Exception as exc:
            message = f"{stage_name} failed: {type(exc).__name__}: {exc}"
            results.append(StageResult(
                stage=stage_name,
                status="failed",
                errors=[message],
                started_at=started,
                completed_at=_utc_now(),
            ))
            warnings.append(message)
            overall_status = "failed"
            break

    if len(results) == len(PIPELINE_STAGES) and all(
        item.status == "completed" for item in results
    ):
        decision = context.get("risk_decision", {})
        prediction = context.get("prediction", {})
        calibrated = prediction.get("calibrated") is True
        probability = prediction.get("probability_tp_before_sl")
        try:
            probability_is_valid = probability is not None and 0 <= float(probability) <= 1
        except (TypeError, ValueError):
            probability_is_valid = False
        if calibrated and probability_is_valid and decision.get("action") in {"BUY", "HOLD", "EXIT", "NO TRADE"}:
            action = decision["action"]
            overall_status = "completed"
        else:
            warnings.append(
                "Prediction is not demonstrably calibrated or decision is invalid; action forced to NO TRADE."
            )
            overall_status = "completed_no_trade"

    return PipelineReport(
        run_id=run_id,
        symbol=normalized_symbol,
        as_of=as_of,
        status=overall_status,
        action=action,
        stages=results,
        warnings=warnings,
    )
