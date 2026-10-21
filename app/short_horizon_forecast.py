"""Short-horizon experimental forecast helpers; not a trading signal or guarantee."""
from __future__ import annotations

import math
from typing import Any


POSITIVE_TERMS = {
    "surge", "surges", "surged", "rally", "rallies", "gain", "gains", "higher",
    "bullish", "inflow", "approval", "approve", "adoption", "breakout", "rise",
}
NEGATIVE_TERMS = {
    "crash", "crashes", "crashed", "drop", "drops", "dropped", "lower", "bearish",
    "outflow", "hack", "hacked", "lawsuit", "ban", "banned", "selloff", "fall",
}


def news_sentiment(items: list[dict[str, Any]]) -> tuple[int, dict[str, int]]:
    """Count simple transparent headline cues; this is not an NLP sentiment model."""
    positive = negative = 0
    for item in items:
        text = (str(item.get("title", "")) + " " + str(item.get("summary", ""))).lower()
        words = {word.strip(".,:;!?()[]{}\"'") for word in text.split()}
        positive += len(words & POSITIVE_TERMS)
        negative += len(words & NEGATIVE_TERMS)
    score = 0 if positive == negative else (1 if positive > negative else -1)
    return score, {"positive_cues": positive, "negative_cues": negative}


def make_two_minute_forecast(
    candles: list[dict[str, Any]],
    symbol: str,
    technical_signal: str,
    research_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a small, inspectable baseline forecast from recent momentum + context."""
    if len(candles) < 5:
        raise ValueError("At least five 1-minute candles are required")
    closes = [float(item["close"]) for item in candles[-5:]]
    if any(not math.isfinite(value) or value <= 0 for value in closes):
        raise ValueError("Forecast candles must have finite positive closes")

    # Use a 3-minute close-to-close move as momentum, with a deliberately
    # conservative 0.35 shrinkage to avoid extrapolating the entire move.
    momentum_pct = (closes[-1] / closes[-4] - 1.0) * 100.0
    signal = str(technical_signal).upper()
    technical_bias = 1 if signal == "BUY" else -1 if signal == "SELL" else 0
    sentiment, sentiment_counts = news_sentiment(research_items)

    # Small context terms; cap at +/- 0.20% because a two-minute forecast should
    # not promise an outsized move from noisy indicators or a few headlines.
    expected_return_pct = max(
        -0.20,
        min(0.20, 0.35 * momentum_pct + 0.025 * technical_bias + 0.015 * sentiment),
    )
    direction = "UP" if expected_return_pct > 0.005 else "DOWN" if expected_return_pct < -0.005 else "FLAT"
    return {
        "symbol": symbol.upper(),
        "horizon_seconds": 120,
        "baseline_price": closes[-1],
        "expected_return_pct": round(expected_return_pct, 5),
        "forecast_price": round(closes[-1] * (1.0 + expected_return_pct / 100.0), 8),
        "direction": direction,
        "technical_signal": signal,
        "momentum_3m_pct": round(momentum_pct, 5),
        "news_sentiment_score": sentiment,
        "news_sentiment_counts": sentiment_counts,
        "research_item_count": len(research_items),
        "method": "shrunk_3m_momentum_plus_small_technical_and_headline_cues",
        "calibrated": False,
        "warnings": [
            "Experimental baseline only; one two-minute result cannot establish predictive accuracy.",
            "Headline cue counts are a simple lexical proxy, not a verified understanding of news.",
            "No trade authorization is produced.",
        ],
    }


def compare_forecast(forecast: dict[str, Any], actual_price: float) -> dict[str, Any]:
    """Compare the frozen forecast with the observed price after the horizon."""
    actual = float(actual_price)
    start = float(forecast["baseline_price"])
    predicted = float(forecast["forecast_price"])
    if not all(math.isfinite(value) and value > 0 for value in (actual, start, predicted)):
        raise ValueError("Prices must be finite and positive")
    actual_return = (actual / start - 1.0) * 100.0
    forecast_return = float(forecast["expected_return_pct"])
    actual_direction = "UP" if actual_return > 0.005 else "DOWN" if actual_return < -0.005 else "FLAT"
    direction_match = forecast["direction"] == actual_direction
    absolute_error_pct = abs(predicted - actual) / start * 100.0
    return {
        "forecast_direction": forecast["direction"],
        "actual_direction": actual_direction,
        "direction_match": direction_match,
        "forecast_price": round(predicted, 8),
        "actual_price": round(actual, 8),
        "baseline_price": round(start, 8),
        "forecast_return_pct": round(forecast_return, 5),
        "actual_return_pct": round(actual_return, 5),
        "absolute_price_error_pct_of_start": round(absolute_error_pct, 5),
        "almost_same": bool(direction_match and absolute_error_pct <= 0.05),
        "evaluation_rule": "direction matches AND absolute price error <= 0.05% of starting price",
    }
