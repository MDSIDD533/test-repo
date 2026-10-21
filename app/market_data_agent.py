"""Market Data Agent: validates candle payloads before downstream analysis.

This module has no network side effects. Inject a fetcher so validation stays
deterministic in tests and the coordinator controls data access.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Callable

INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}
PRICE_FIELDS = ("open", "high", "low", "close")
REQUIRED_FIELDS = ("time", "open", "high", "low", "close", "volume")


class CandleValidationError(ValueError):
    """Raised when market candles cannot safely be passed to analysis."""


def validate_candles(
    candles: list[dict[str, Any]],
    interval: str,
    *,
    now: float | None = None,
    minimum: int = 35,
    max_age_intervals: float = 3.0,
) -> dict[str, Any]:
    """Validate candle shape, OHLC relationships, ordering, and freshness."""
    if interval not in INTERVAL_SECONDS:
        raise CandleValidationError(f"Unsupported interval: {interval}")
    if not isinstance(candles, list) or len(candles) < minimum:
        raise CandleValidationError(f"At least {minimum} candles are required")
    current_time = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    previous_time: float | None = None
    for index, candle in enumerate(candles):
        if not isinstance(candle, dict):
            raise CandleValidationError(f"Candle {index} must be an object")
        missing = [field for field in REQUIRED_FIELDS if field not in candle]
        if missing:
            raise CandleValidationError(f"Candle {index} missing fields: {', '.join(missing)}")
        values: dict[str, float] = {}
        for field in REQUIRED_FIELDS:
            try:
                values[field] = float(candle[field])
            except (TypeError, ValueError):
                raise CandleValidationError(f"Candle {index} has invalid {field}") from None
            if not math.isfinite(values[field]):
                raise CandleValidationError(f"Candle {index} has non-finite {field}")
        if values["time"] < 0:
            raise CandleValidationError(f"Candle {index} has a negative timestamp")
        if values["time"] > current_time + INTERVAL_SECONDS[interval]:
            raise CandleValidationError("Candle timestamp is too far in the future")
        if any(values[field] <= 0 for field in PRICE_FIELDS):
            raise CandleValidationError(f"Candle {index} has a non-positive price")
        if values["volume"] < 0:
            raise CandleValidationError(f"Candle {index} has negative volume")
        if values["high"] < max(values["open"], values["close"], values["low"]):
            raise CandleValidationError(f"Candle {index} has inconsistent high")
        if values["low"] > min(values["open"], values["close"], values["high"]):
            raise CandleValidationError(f"Candle {index} has inconsistent low")
        if previous_time is not None and values["time"] <= previous_time:
            raise CandleValidationError("Candle timestamps must be strictly increasing and unique")
        if previous_time is not None and values["time"] - previous_time > INTERVAL_SECONDS[interval] * 1.5:
            raise CandleValidationError(f"Gap detected between candles {index - 1} and {index}")
        previous_time = values["time"]

    latest_time = float(candles[-1]["time"])
    age_seconds = current_time - latest_time
    if age_seconds < -INTERVAL_SECONDS[interval]:
        raise CandleValidationError("Latest candle timestamp is too far in the future")
    max_age_seconds = INTERVAL_SECONDS[interval] * max_age_intervals
    if age_seconds > max_age_seconds:
        raise CandleValidationError(
            f"Market data is stale ({int(age_seconds)}s old; limit {int(max_age_seconds)}s)"
        )
    return {
        "valid": True,
        "candle_count": len(candles),
        "interval": interval,
        "latest_candle_time": latest_time,
        "age_seconds": max(0.0, age_seconds),
        "checks": [
            "required_fields", "finite_numeric_values", "positive_prices",
            "ohlc_consistency", "nonnegative_volume", "ordered_unique_timestamps",
            "no_large_gaps", "freshness",
        ],
    }


def make_market_data_agent(
    fetcher: Callable[[str, str, int], list[dict[str, Any]]],
    *,
    interval: str = "5m",
    limit: int = 220,
    clock: Callable[[], float] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a pipeline-compatible agent around an injected candle fetcher."""
    if interval not in INTERVAL_SECONDS:
        raise ValueError(f"Unsupported interval: {interval}")
    if limit < 35:
        raise ValueError("limit must be at least 35 candles")

    def run(context: dict[str, Any]) -> dict[str, Any]:
        symbol = str(context.get("symbol", "")).strip().upper()
        if not symbol:
            raise ValueError("A non-empty symbol is required")
        rows = fetcher(symbol, interval, limit)
        validation = validate_candles(rows, interval, now=clock() if clock else None)
        return {
            "symbol": symbol,
            "interval": interval,
            "candles": rows,
            "validation": validation,
            "evidence": [{
                "source": "injected_market_data_provider",
                "symbol": symbol,
                "interval": interval,
                "candle_count": validation["candle_count"],
                "latest_candle_time": validation["latest_candle_time"],
            }],
            "warnings": [],
        }

    return run
