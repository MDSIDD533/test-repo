"""Resolve frozen pipeline forecasts against a real observed price.

Usage:
    python scripts/resolve_forecasts.py --dry-run
    python scripts/resolve_forecasts.py --limit 5

Only forecasts whose frozen target time has passed are considered. The
observation rule is the same one used by the two-minute experiment: a spot
ticker is preferred, and a completed 1-minute candle is used only if it closed
at or before the target time and within the documented tolerance. The observed
source and time offset are appended to the ledger.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.forecast_ledger import LedgerError, read_ledger, record_resolution
from scripts.two_minute_forecast_test import fetch_actual_price_with_fallback

Observation = Callable[..., dict[str, Any]]


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def pending_forecasts(now: datetime) -> list[dict[str, Any]]:
    ledger = read_ledger()
    due = []
    for forecast in ledger["forecasts"]:
        if forecast.get("status") != "pending":
            continue
        try:
            target = _parse(forecast["target_time"])
        except (KeyError, TypeError, ValueError):
            continue
        if target <= now:
            due.append(forecast)
    return sorted(due, key=lambda item: item["target_time"])


def resolve_pending(
    *,
    now: datetime | None = None,
    limit: int = 10,
    provider: str = "binance",
    observation: Observation = fetch_actual_price_with_fallback,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Append resolutions for due forecasts. Never edits a frozen forecast."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    due_all = pending_forecasts(moment)
    due = due_all[: max(0, limit)]
    resolved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for forecast in due:
        target = _parse(forecast["target_time"]).timestamp()
        try:
            observed = observation(forecast["symbol"], provider, target)
        except Exception as exc:
            errors.append(
                {
                    "forecast_id": forecast["forecast_id"],
                    "symbol": forecast["symbol"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if dry_run:
            resolved.append(
                {
                    "forecast_id": forecast["forecast_id"],
                    "symbol": forecast["symbol"],
                    "observed_price": observed.get("price"),
                    "source": observed.get("source"),
                    "time_difference_seconds": observed.get("time_difference_seconds"),
                    "dry_run": True,
                }
            )
            continue
        try:
            event = record_resolution(
                forecast["forecast_id"],
                observed_price=observed.get("price"),
                observed_at=datetime.fromtimestamp(
                    float(observed.get("observed_at", target)), timezone.utc
                ).isoformat(),
                source=str(observed.get("source") or "unspecified_observation"),
                time_difference_seconds=observed.get("time_difference_seconds"),
            )
        except (LedgerError, OSError) as exc:
            errors.append(
                {
                    "forecast_id": forecast["forecast_id"],
                    "symbol": forecast["symbol"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        resolved.append(
            {
                "forecast_id": event["forecast_id"],
                "symbol": forecast["symbol"],
                "observed_return_pct": event["observed_return_pct"],
                "direction_correct": event["direction_correct"],
                "source": event["source"],
            }
        )

    return {
        "checked_at": moment.isoformat(),
        "dry_run": dry_run,
        "due_count": len(due_all),
        "attempted": len(due),
        "resolved_count": 0 if dry_run else len(resolved),
        "planned_count": len(resolved) if dry_run else 0,
        "resolved": resolved,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--provider", default="binance")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default=None, help="write the run summary as JSON")
    arguments = parser.parse_args(argv)

    summary = resolve_pending(
        limit=arguments.limit,
        provider=arguments.provider,
        dry_run=arguments.dry_run,
    )
    # Report the resulting sample so a positive accuracy number is always shown
    # next to the always-FLAT baseline it has to beat.
    ledger = read_ledger()
    summary["ledger_summary"] = ledger["summary"]

    text = json.dumps(summary, indent=2, sort_keys=True)
    print(text)
    if arguments.out:
        destination = Path(arguments.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
