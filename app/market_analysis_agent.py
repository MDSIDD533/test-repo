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
    symbol_value = market_data.get("symbol", context.get("symbol", ""))
    symbol = str(symbol_value).strip().upper()
    interval = str(market_data.get("interval", "")).strip()
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
        "as_of": context.get("as_of"),
        "signal": analysis["signal"],
        "confidence": analysis["confidence"],
        "entry": analysis["entry"],
        "tp1": analysis["tp1"],
        "tp2": analysis["tp2"],
        "tp3": analysis["tp3"],
        "sl": analysis["sl"],
        "price": analysis.get("price"),
        "rsi": analysis.get("rsi"),
        "macd": analysis.get("macd"),
        "ema9": analysis.get("ema9"),
        "ema20": analysis.get("ema20"),
        "ema50": analysis.get("ema50"),
        "ema200": analysis.get("ema200"),
        "atr": analysis.get("atr"),
        "atr_pct": analysis.get("atr_pct"),
        "structure": analysis.get("structure"),
        "support_50": analysis.get("support_50"),
        "resistance_50": analysis.get("resistance_50"),
        "score": analysis.get("score"),
        "reasons": list(analysis.get("reasons", [])),
        "lookback_candles": analysis.get("lookback_candles"),
        "model_version": "rule_based_technical_v1",
        "evidence": [{
            "source": "validated_candles",
            "candle_count": validation.get("candle_count", len(candles)),
            "latest_candle_time": validation.get("latest_candle_time"),
            "signal": analysis["signal"],
            "indicators": {
                "rsi": analysis.get("rsi"),
                "macd": analysis.get("macd"),
                "structure": analysis.get("structure"),
                "atr_pct": analysis.get("atr_pct"),
            },
        }],
        "warnings": [
            "Technical signal is an indicator-based snapshot, not a calibrated probability of profit."
        ],
    }
