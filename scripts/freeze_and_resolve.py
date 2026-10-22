"""Freeze one real pipeline forecast, wait for its horizon, then resolve it.

A pipeline forecast records its own ``forecast_target_time``. This script reads
that value and waits exactly as long as it needs to, instead of guessing, and
reports every step so a failure is diagnosable from the CI comment.

Usage:
    python scripts/freeze_and_resolve.py --base-url http://127.0.0.1:8000 \
        --out evidence/resolution.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

from scripts.resolve_forecasts import resolve_pending

MAX_WAIT_SECONDS = 420
SETTLE_SECONDS = 5


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _default_get(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(url, params=params, timeout=(5, 90))
    response.raise_for_status()
    return response.json()


def _summarize_stages(pipeline: dict[str, Any]) -> dict[str, Any]:
    """Keep the status and any error text for every stage."""
    summary = {}
    for stage in pipeline.get("stages", []):
        name = stage.get("stage")
        summary[name] = {
            "status": stage.get("status"),
            "errors": (stage.get("errors") or [])[:2],
            "warnings": (stage.get("warnings") or [])[:2],
        }
    return summary


def freeze_and_resolve(
    base_url: str,
    *,
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    fallback_intervals: tuple[str, ...] = ("5m",),
    get: Callable[..., dict[str, Any]] = _default_get,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    max_wait_seconds: int = MAX_WAIT_SECONDS,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "base_url": base_url,
        "steps": [],
    }

    try:
        before = get(f"{base_url}/api/forecast/ledger")
        report["ledger_before"] = (before or {}).get("summary", {})
    except Exception as exc:  # noqa: BLE001
        report["ledger_before"] = {}
        report["error"] = f"server is not reachable: {type(exc).__name__}: {exc}"
        return report
    report["steps"].append({"step": "read_ledger_before", "total": report["ledger_before"].get("total")})

    # A short interval can fail its own freshness check with a provider whose
    # newest candle lags. Try each configured interval rather than assuming one.
    attempts = []
    chosen = None
    for candidate in (interval, *fallback_intervals):
        try:
            pipeline = get(f"{base_url}/api/agents/run/{symbol}", params={"interval": candidate})
        except Exception as exc:  # noqa: BLE001
            attempts.append({"interval": candidate, "error": f"{type(exc).__name__}: {exc}"})
            continue
        prediction = next(
            (stage for stage in pipeline.get("stages", []) if stage.get("stage") == "prediction"), {}
        )
        prediction_result = prediction.get("result") or {}
        attempt = {
            "interval": candidate,
            "status": pipeline.get("status"),
            "action": pipeline.get("action"),
            "stages": _summarize_stages(pipeline),
            "forecast_ledger_id": prediction_result.get("forecast_ledger_id"),
            "forecast_target_time": prediction_result.get("forecast_target_time"),
            "horizon_seconds": prediction_result.get("horizon_seconds"),
            "ledger_error": prediction_result.get("ledger_error"),
        }
        attempts.append(attempt)
        if prediction_result.get("forecast_ledger_id"):
            chosen = attempt
            report["interval"] = candidate
            break

    report["pipeline_attempts"] = attempts
    if chosen is None:
        report["error"] = (
            "no forecast was frozen at any configured interval; "
            "see pipeline_attempts for the blocking stage"
        )
        report["pipeline"] = attempts[-1] if attempts else {}
        return report

    report["pipeline"] = chosen
    prediction_result = chosen
    report["steps"].append(
        {"step": "run_pipeline", "interval": chosen["interval"], "status": chosen["status"],
         "action": chosen["action"]}
    )

    target_text = prediction_result.get("forecast_target_time")
    try:
        target = _parse(target_text)
    except (TypeError, ValueError):
        report["error"] = f"pipeline returned an unusable target time: {target_text!r}"
        return report

    wait_seconds = (target - now()).total_seconds() + SETTLE_SECONDS
    required_seconds = wait_seconds
    wait_seconds = max(0.0, min(wait_seconds, float(max_wait_seconds)))
    report["wait_seconds"] = round(wait_seconds, 1)
    report["wait_required_seconds"] = round(max(0.0, required_seconds), 1)
    # If the cap bites we must say so: resolving before the target time would
    # turn the newest forecast into a row that merely looks resolved.
    report["wait_capped"] = required_seconds > wait_seconds + 0.001
    report["steps"].append(
        {"step": "wait_for_horizon", "seconds": report["wait_seconds"],
         "capped": report["wait_capped"]}
    )
    if wait_seconds:
        sleep(wait_seconds)

    observation_time = now()
    resolution = resolve_pending(now=observation_time, limit=5, provider="auto")
    report["resolution"] = resolution
    resolved_ids = {
        item.get("forecast_id") for item in resolution.get("resolved") or []
    }
    # Distinguish "our forecast was evaluated" from "an older forecast was".
    report["this_run_resolved"] = prediction_result.get("forecast_ledger_id") in resolved_ids
    report["steps"].append(
        {
            "step": "resolve_due_forecasts",
            "due": resolution.get("due_count"),
            "attempted": resolution.get("attempted"),
            "resolved": resolution.get("resolved_count"),
        }
    )

    try:
        after = get(f"{base_url}/api/forecast/ledger")
        report["ledger_after"] = (after or {}).get("summary", {})
    except Exception as exc:  # noqa: BLE001
        report["ledger_after"] = {}
        report["ledger_after_error"] = f"{type(exc).__name__}: {exc}"
    report["steps"].append({"step": "read_ledger_after", "resolved": report["ledger_after"].get("resolved")})
    report["finished_at"] = observation_time.isoformat()
    report["new_forecast_pending"] = not report["this_run_resolved"]
    report["session_hours"] = round(
        (observation_time - _parse(prediction_result["forecast_target_time"])).total_seconds() / 3600, 4
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--out", default=None)
    arguments = parser.parse_args(argv)

    report = freeze_and_resolve(
        arguments.base_url, symbol=arguments.symbol, interval=arguments.interval
    )
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    print(text)
    if arguments.out:
        destination = Path(arguments.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
