"""Append-only forecast ledger.

Forecasts are written once, before their outcome is known. Resolutions are
appended as separate events; the original forecast line is never mutated.
This makes the frozen forecast auditable and makes accidental retrospective
editing visible in the event history.
"""
from __future__ import annotations

import json
import math
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FORECAST_EVENT = "forecast"
RESOLUTION_EVENT = "resolution"
DIRECTION_THRESHOLD_PCT = 0.05
ALLOWED_DIRECTIONS = {"UP", "DOWN", "FLAT"}
_LOCK = threading.Lock()


class LedgerError(ValueError):
    """Raised when an event cannot be safely written or resolved."""


def ledger_path() -> Path:
    """Return the ledger path, resolving the environment override at call time."""
    configured = os.environ.get("FORECAST_LEDGER_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent / "runtime" / "forecast_ledger.jsonl"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{field} must be an ISO-8601 timestamp")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LedgerError(f"{field} must be an ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any, field: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LedgerError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise LedgerError(f"{field} must be finite")
    if positive and number <= 0:
        raise LedgerError(f"{field} must be positive")
    return number


def _positive_horizon(value: Any) -> int:
    number = _finite_number(value, "horizon_seconds", positive=True)
    if not number.is_integer():
        raise LedgerError("horizon_seconds must be a whole number of seconds")
    return int(number)


def _append(event: dict[str, Any]) -> dict[str, Any]:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return event


def record_forecast(
    prediction: dict[str, Any],
    *,
    run_id: str,
    symbol: str,
    source: str = "agent_pipeline",
) -> dict[str, Any]:
    """Freeze one prediction as an immutable ledger event.

    Only metadata that the prediction stage actually produced is stored. A
    direction-only forecast is stored as direction-only; no point price is
    invented to make later error metrics look better.
    """
    if not isinstance(prediction, dict):
        raise LedgerError("prediction must be a dictionary")
    if prediction.get("status") == "unavailable":
        raise LedgerError("an unavailable prediction has no forecast to freeze")
    if not str(run_id or "").strip():
        raise LedgerError("run_id is required")
    symbol = str(symbol or prediction.get("symbol") or "").strip().upper()
    if not symbol:
        raise LedgerError("symbol is required")

    direction = str(prediction.get("direction", "")).upper()
    if direction not in ALLOWED_DIRECTIONS:
        raise LedgerError(f"direction must be one of {sorted(ALLOWED_DIRECTIONS)}")
    created = _parse_timestamp(prediction.get("forecast_created_at"), "forecast_created_at")
    reference_price = _finite_number(
        prediction.get("reference_price"), "reference_price", positive=True
    )
    horizon_seconds = _positive_horizon(prediction.get("horizon_seconds"))
    model_version = str(prediction.get("model_version") or prediction.get("method") or "").strip()
    if not model_version:
        raise LedgerError("model_version is required")

    target = created + timedelta(seconds=horizon_seconds)
    forecast_id = str(uuid.uuid4())
    event = {
        "event": FORECAST_EVENT,
        "forecast_id": forecast_id,
        "run_id": str(run_id),
        "recorded_at": _utc_now().isoformat(),
        "source": str(source),
        "symbol": symbol,
        "interval": str(prediction.get("interval") or ""),
        "direction": direction,
        "technical_signal": prediction.get("technical_signal"),
        "forecast_created_at": created.isoformat(),
        "target_time": target.isoformat(),
        "horizon_seconds": horizon_seconds,
        "horizon_candles": prediction.get("horizon_candles"),
        "reference_price": reference_price,
        "predicted_price": prediction.get("predicted_price"),
        "predicted_return_pct": prediction.get("predicted_return_pct"),
        "calibrated": prediction.get("calibrated") is True,
        "probability_tp_before_sl": prediction.get("probability_tp_before_sl"),
        "model_version": model_version,
        "features": prediction.get("features") if isinstance(prediction.get("features"), dict) else {},
        "data_freshness": (
            prediction.get("data_freshness")
            if isinstance(prediction.get("data_freshness"), dict)
            else {}
        ),
        "limitations": (
            prediction.get("limitations")
            if isinstance(prediction.get("limitations"), list)
            else []
        ),
        "warnings": (
            prediction.get("warnings") if isinstance(prediction.get("warnings"), list) else []
        ),
    }
    return _append(event)


def record_resolution(
    forecast_id: str,
    *,
    observed_price: Any,
    observed_at: str | None = None,
    source: str = "unspecified_observation",
    time_difference_seconds: float | None = None,
) -> dict[str, Any]:
    """Append an outcome for a pending direction-only forecast.

    A forecast can be resolved once. The forecast event is left untouched.
    """
    forecast_id = str(forecast_id or "").strip()
    if not forecast_id:
        raise LedgerError("forecast_id is required")
    ledger = read_ledger()
    forecast = next(
        (item for item in ledger["forecasts"] if item["forecast_id"] == forecast_id), None
    )
    if forecast is None:
        raise LedgerError(f"unknown forecast_id: {forecast_id}")
    if forecast["status"] == "resolved":
        raise LedgerError(f"forecast_id {forecast_id} is already resolved")

    observed = _finite_number(observed_price, "observed_price", positive=True)
    observation_time = _parse_timestamp(observed_at, "observed_at") if observed_at else _utc_now()
    reference = float(forecast["reference_price"])
    return_pct = (observed / reference - 1.0) * 100.0
    direction = forecast["direction"]
    if direction == "UP":
        correct = return_pct > DIRECTION_THRESHOLD_PCT
    elif direction == "DOWN":
        correct = return_pct < -DIRECTION_THRESHOLD_PCT
    else:
        correct = abs(return_pct) <= DIRECTION_THRESHOLD_PCT

    difference = None
    if time_difference_seconds is not None:
        difference = abs(_finite_number(time_difference_seconds, "time_difference_seconds"))
    event = {
        "event": RESOLUTION_EVENT,
        "forecast_id": forecast_id,
        "resolved_at": _utc_now().isoformat(),
        "observed_at": observation_time.isoformat(),
        "observed_price": observed,
        "observed_return_pct": round(return_pct, 8),
        "direction_correct": bool(correct),
        "direction_threshold_pct": DIRECTION_THRESHOLD_PCT,
        "predicted_price": None,
        "absolute_price_error_pct": None,
        "source": str(source),
        "time_difference_seconds": difference,
    }
    return _append(event)


def read_ledger() -> dict[str, Any]:
    """Fold forecast and resolution events into current forecast records.

    Malformed lines are reported instead of being silently ignored. A
    resolution without a matching forecast is also reported.
    """
    path = ledger_path()
    events: list[dict[str, Any]] = []
    corrupt: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    corrupt.append({"line": number, "error": f"invalid JSON: {exc.msg}"})
                    continue
                if not isinstance(event, dict) or event.get("event") not in {
                    FORECAST_EVENT,
                    RESOLUTION_EVENT,
                }:
                    corrupt.append({"line": number, "error": "unknown event type"})
                    continue
                if not str(event.get("forecast_id") or "").strip():
                    corrupt.append({"line": number, "error": "missing forecast_id"})
                    continue
                events.append(event)

    forecasts: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["event"] == FORECAST_EVENT:
            forecasts[event["forecast_id"]] = dict(
                event, status="pending", resolution=None
            )
            continue
        target = forecasts.get(event["forecast_id"])
        if target is None:
            corrupt.append(
                {
                    "line": None,
                    "error": f"resolution for unknown forecast {event['forecast_id']}",
                }
            )
            continue
        if target["resolution"] is not None:
            corrupt.append(
                {
                    "line": None,
                    "error": f"duplicate resolution for {event['forecast_id']}",
                }
            )
            continue
        target["resolution"] = event
        target["status"] = "resolved"

    ordered = sorted(
        forecasts.values(),
        key=lambda item: (item.get("recorded_at") or "", item["forecast_id"]),
        reverse=True,
    )
    resolved = [item for item in ordered if item["status"] == "resolved"]
    correct = sum(1 for item in resolved if item["resolution"]["direction_correct"] is True)
    directions = {direction: 0 for direction in sorted(ALLOWED_DIRECTIONS)}
    for item in ordered:
        if item.get("direction") in directions:
            directions[item["direction"]] += 1
    summary = {
        "total": len(ordered),
        "pending": len(ordered) - len(resolved),
        "resolved": len(resolved),
        "direction_correct": correct,
        "direction_incorrect": len(resolved) - correct,
        "direction_accuracy_pct": round(correct / len(resolved) * 100, 2)
        if resolved
        else None,
        "by_direction": directions,
        "corrupt_event_count": len(corrupt),
    }
    return {
        "events": len(events),
        "forecasts": ordered,
        "corrupt_lines": corrupt,
        "summary": summary,
    }
