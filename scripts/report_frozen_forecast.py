"""Print the forecast id frozen by a pipeline run, for CI evidence.

Usage: python scripts/report_frozen_forecast.py evidence/pipeline.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    arguments = (argv if argv is not None else sys.argv[1:])
    path = Path(arguments[0] if arguments else "evidence/pipeline.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"could not read {path}: {type(exc).__name__}: {exc}")
        return 0

    prediction = next(
        (stage for stage in data.get("stages", []) if stage.get("stage") == "prediction"), {}
    )
    result = prediction.get("result") or {}
    print(f"run: {data.get('run_id')} status: {data.get('status')} action: {data.get('action')}")
    print(f"stage status: {prediction.get('status')}")
    print(f"frozen forecast: {result.get('forecast_ledger_id') or 'NONE'}")
    print(f"horizon seconds: {result.get('horizon_seconds')}")
    print(f"target time: {result.get('forecast_target_time')}")
    if result.get("ledger_error"):
        print(f"ledger error: {result['ledger_error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
