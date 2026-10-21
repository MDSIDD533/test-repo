"""Market Analysis Agent: turns validated candles into a technical snapshot."""
from __future__ import annotations

from typing import Any

from app.analysis import analyze_candles


def run_market_analysis_agent(context: dict[str, Any]) -> dict[str, Any]:
    market_data = context.get("market_data")
    if not isinstance(market_data, dict):
        raise ValueError("Validated market_data stage is required")
    validation = market_data.get("validation")
    candles = market_data.get("candles")
    symbol = str(market_data.get("symbol") or context.get("symbol") or "").strip().upper()
    interval = str(market_data.get("interval") or "")
    if not isinstance(validation, dict) or validation.get("valid") is not True:
        raise ValueError("Market data must pass validation before analysis")
    if not isinstance(candles, list):
        raise ValueError("Market data candles must be a list")
    if not symbol or not interval:
        raise ValueError("Market data symbol and interval are required")

    analysis = analyze_candles(candles, symbol, interval)
    return {
        "symbol": symbol,
        "interval": interval,
        "signal": analysis["signal"],
        "confidence": analysis["confidence"],
        "entry": analysis["entry"],
        "tp1": analysis["tp1"],
        "tp2": analysis["tp2"],
        "tp3": analysis["tp3"],
        "sl": analysis["sl"],
        "reasons": list(analysis.get("reasons", [])),
        "lookback_candles": analysis.get("lookback_candles"),
        "evidence": [{
            "source": "validated_candles",
            "candle_count": validation.get("candle_count", len(candles)),
            "latest_candle_time": validation.get("latest_candle_time"),
            "signal": analysis["signal"],
        }],
        "warnings": [
            "Technical signal is an indicator-based snapshot, not a calibrated probability of profit."
        ],
    }
