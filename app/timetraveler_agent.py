"""TimeTraveler Agent: bootstrap simulations over historical candle returns.

Outputs scenario distributions, not calibrated probabilities of profit. A
separate validation process is required before any probability can authorize
a trading action.
"""
from __future__ import annotations

import hashlib
import math
import random
from typing import Any

from app.market_data_agent import INTERVAL_SECONDS


ALLOWED_SIMULATIONS = {100, 500, 1000}


def run_timetraveler_agent(context: dict[str, Any]) -> dict[str, Any]:
    market_data = context.get("market_data")
    if not isinstance(market_data, dict) or not isinstance(market_data.get("validation"), dict):
        raise ValueError("Validated market_data stage is required")
    if market_data["validation"].get("valid") is not True:
        raise ValueError("Market data must pass validation before simulation")
    candles = market_data.get("candles")
    if not isinstance(candles, list) or len(candles) < 35:
        raise ValueError("At least 35 validated candles are required for simulation")

    count = context.get("requested_simulations", 100)
    if count not in ALLOWED_SIMULATIONS:
        raise ValueError("requested_simulations must be one of 100, 500, or 1000")
    closes: list[float] = []
    for index, candle in enumerate(candles):
        try:
            close = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Candle {index} has invalid close") from None
        if not math.isfinite(close) or close <= 0:
            raise ValueError(f"Candle {index} has invalid close")
        closes.append(close)

    returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ][-100:]
    if len(returns) < 30:
        raise ValueError("At least 30 historical returns are required")
    if any(not math.isfinite(value) for value in returns):
        raise ValueError("Historical returns contain invalid values")

    symbol = str(context.get("symbol", market_data.get("symbol", ""))).strip().upper()
    interval = str(market_data.get("interval", ""))
    as_of = str(context.get("as_of", ""))
    seed_text = f"{symbol}|{interval}|{as_of}|{count}"
    seed = int.from_bytes(hashlib.sha256(seed_text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    horizon = 12
    scenarios: list[float] = []
    for _ in range(count):
        compounded = 1.0
        for _step in range(horizon):
            compounded *= 1.0 + rng.choice(returns)
        scenarios.append(compounded - 1.0)
    scenarios.sort()

    def percentile(p: float) -> float:
        position = (len(scenarios) - 1) * p
        lower = int(position)
        upper = min(lower + 1, len(scenarios) - 1)
        weight = position - lower
        return scenarios[lower] * (1 - weight) + scenarios[upper] * weight

    positive_count = sum(value > 0 for value in scenarios)
    return {
        "symbol": symbol,
        "interval": interval,
        "simulation_count": count,
        "horizon_candles": horizon,
        "horizon_seconds": horizon * INTERVAL_SECONDS.get(interval, 0) or None,
        "simulation_only": True,
        "not_an_observation": True,
        "costs_modeled": False,
        "historical_return_samples": len(returns),
        "scenario_positive_pct": round(positive_count / count * 100, 2),
        "median_return_pct": round(percentile(0.5) * 100, 3),
        "p05_return_pct": round(percentile(0.05) * 100, 3),
        "p95_return_pct": round(percentile(0.95) * 100, 3),
        "method": "historical_return_bootstrap",
        "calibrated": False,
        "assumptions": [
            "Historical returns are sampled with replacement from the last 100 returns.",
            f"The horizon is {horizon} candles of {interval or 'the selected interval'}.",
            "Fees, spread and slippage are not modeled.",
            "Each path is a simulated scenario, not an observed future price.",
        ],
        "evidence": [{
            "source": "validated_historical_candles",
            "candle_count": len(candles),
            "return_sample_count": len(returns),
            "random_seed": seed,
        }],
        "warnings": [
            "Scenario percentages are not calibrated win probabilities and must not be interpreted as a guarantee.",
            "Bootstrap assumes historical return samples are informative; regime changes and fees/slippage are not modeled.",
        ],
    }
