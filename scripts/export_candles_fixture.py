"""Export real provider candles into a fixture for offline replay.

The development sandbox and many user networks cannot reach an exchange, so the
dashboard has nothing real to draw. This script records what the live provider
layer actually returned, with its own timestamps and source, so replay mode can
show real market history instead of invented data.

Usage:
    python scripts/export_candles_fixture.py --output fixtures/candles.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market import candles, provider_used

# Only intervals a provider can actually serve are recorded. Anything else is
# derived honestly at replay time from one of these, never invented.
EXPORT_INTERVALS = ("1m", "5m", "15m", "1h", "1d")
DEFAULT_LIMIT = 300


def export(symbol: str = "BTCUSDT", limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    intervals: dict[str, Any] = {}
    failures: dict[str, str] = {}
    providers: set[str] = set()

    for interval in EXPORT_INTERVALS:
        try:
            rows = candles(symbol, interval, limit)
        except Exception as exc:  # noqa: BLE001
            failures[interval] = f"{type(exc).__name__}: {exc}"
            continue
        if not rows:
            failures[interval] = "provider returned no candles"
            continue
        # Compact rows keep the committed fixture small; the reader restores names.
        intervals[interval] = [
            [
                float(row["time"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            ]
            for row in rows
        ]
        used = provider_used(symbol, interval)
        if used:
            providers.add(used)

    if not intervals:
        raise SystemExit(f"No interval could be recorded: {failures}")

    newest = max(rows[-1][0] for rows in intervals.values())
    return {
        "symbol": symbol,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "newest_candle": datetime.fromtimestamp(newest, timezone.utc).isoformat(),
        "providers": sorted(providers),
        "requested_limit": limit,
        "intervals": intervals,
        "failures": failures,
        "columns": ["time", "open", "high", "low", "close", "volume"],
        "note": (
            "Real provider data recorded once. Timestamps are the provider's own and are "
            "never shifted; replay mode is labelled everywhere it is served."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", default="fixtures/candles.json")
    arguments = parser.parse_args(argv)

    fixture = export(arguments.symbol, arguments.limit)
    destination = Path(arguments.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(fixture, separators=(",", ":")) + "\n", encoding="utf-8")

    counts = {name: len(rows) for name, rows in fixture["intervals"].items()}
    print(f"recorded {counts} from {fixture['providers']} into {destination}")
    print(f"newest candle: {fixture['newest_candle']}")
    if fixture["failures"]:
        print(f"intervals that failed: {fixture['failures']}")
    return 0 if len(fixture["intervals"]) >= 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
