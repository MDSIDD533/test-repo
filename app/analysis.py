from __future__ import annotations
from typing import Any
import numpy as np


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2 / (period + 1)
    out = np.empty_like(values, dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _rsi(close: np.ndarray, period: int = 14) -> float:
    delta = np.diff(close)
    gains, losses = np.maximum(delta, 0), np.maximum(-delta, 0)
    if len(delta) < period:
        return 50.0
    gain, loss = float(np.mean(gains[-period:])), float(np.mean(losses[-period:]))
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def analyze_candles(
    candles: list[dict[str, Any]],
    symbol: str = "UNKNOWN",
    timeframe: str = "5m",
    lookback: int = 50,
) -> dict[str, Any]:
    if len(candles) < 35:
        raise ValueError("At least 35 candles are needed for analysis")
    if lookback < 20:
        raise ValueError("Lookback must be at least 20 candles")

    # Keep the full history for stable EMA warm-up, but assess recent price action
    # and market structure using only the requested most recent candles.
    close_all = np.array([c["close"] for c in candles], dtype=float)
    high_all = np.array([c["high"] for c in candles], dtype=float)
    low_all = np.array([c["low"] for c in candles], dtype=float)
    open_all = np.array([c["open"] for c in candles], dtype=float)

    close = close_all[-lookback:]
    high = high_all[-lookback:]
    low = low_all[-lookback:]
    opens = open_all[-lookback:]
    price = float(close[-1])

    ema9_all, ema20_all, ema50_all, ema200_all = (
        _ema(close_all, p) for p in (9, 20, 50, 200)
    )
    ema9, ema20, ema50, ema200 = (
        float(series[-1]) for series in (ema9_all, ema20_all, ema50_all, ema200_all)
    )
    rsi = _rsi(close)
    macd_line_all = _ema(close_all, 12) - _ema(close_all, 26)
    macd_signal_all = _ema(macd_line_all, 9)
    macd_bull = bool(macd_line_all[-1] > macd_signal_all[-1])

    ranges = np.maximum(
        high_all[1:] - low_all[1:],
        np.maximum(
            np.abs(high_all[1:] - close_all[:-1]),
            np.abs(low_all[1:] - close_all[:-1]),
        ),
    )
    atr = float(np.mean(ranges[-14:])) if len(ranges) >= 14 else max(price * 0.005, 1e-8)
    atr = max(atr, price * 0.001, 1e-8)

    score, reasons = 0, []
    if price > ema20:
        score += 1
        reasons.append("Price above EMA20")
    else:
        score -= 1
        reasons.append("Price below EMA20")

    if ema9 > ema20 > ema50:
        score += 1
        reasons.append("Bullish EMA9/20/50 alignment")
    elif ema9 < ema20 < ema50:
        score -= 1
        reasons.append("Bearish EMA9/20/50 alignment")

    if macd_bull:
        score += 1
        reasons.append("MACD above signal line")
    else:
        score -= 1
        reasons.append("MACD below signal line")

    if 50 <= rsi <= 68:
        score += 1
        reasons.append("RSI supports bullish momentum")
    elif 32 <= rsi < 50:
        score -= 1
        reasons.append("RSI shows bearish/weak momentum")
    elif rsi > 75:
        score -= 1
        reasons.append("RSI is overbought; caution")
    elif rsi < 25:
        score += 1
        reasons.append("RSI is oversold; rebound is not confirmed")

    if close[-1] > opens[-1] and close[-1] > close[-2]:
        score += 1
        reasons.append("Latest candle has bullish momentum")
    elif close[-1] < opens[-1] and close[-1] < close[-2]:
        score -= 1
        reasons.append("Latest candle has bearish momentum")

    # Compare the most recent 10 candles with the preceding 10 inside the
    # 50-candle window to estimate short-term market structure.
    recent_high = float(np.max(high[-10:]))
    recent_low = float(np.min(low[-10:]))
    previous_high = float(np.max(high[-20:-10]))
    previous_low = float(np.min(low[-20:-10]))
    if recent_high > previous_high and recent_low > previous_low:
        score += 1
        reasons.append("Last 10 candles show higher highs and higher lows")
        structure = "HIGHER HIGHS / HIGHER LOWS"
    elif recent_high < previous_high and recent_low < previous_low:
        score -= 1
        reasons.append("Last 10 candles show lower highs and lower lows")
        structure = "LOWER HIGHS / LOWER LOWS"
    else:
        structure = "RANGE / MIXED"

    resistance = float(np.max(high[:-1]))
    support = float(np.min(low[:-1]))
    if price > resistance:
        score += 1
        reasons.append("Price broke above the 50-candle resistance")
        structure = "BREAKOUT UP"
    elif price < support:
        score -= 1
        reasons.append("Price broke below the 50-candle support")
        structure = "BREAKDOWN"

    # Cap the displayed confidence and avoid implying certainty.
    signal = "BUY" if score >= 3 else "SELL" if score <= -3 else "WAIT"
    confidence = min(90, 50 + abs(score) * 6)

    if signal == "BUY":
        stop = price - max(1.5 * atr, price * 0.002)
        risk = max(price - stop, price * 0.002)
        targets = [price + risk * multiple for multiple in (1, 2, 3)]
    elif signal == "SELL":
        stop = price + max(1.5 * atr, price * 0.002)
        risk = max(stop - price, price * 0.002)
        targets = [max(price - risk * multiple, 0) for multiple in (1, 2, 3)]
    else:
        stop = price - atr
        targets = [price + atr * multiple for multiple in (1, 2, 3)]

    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "signal": signal,
        "confidence": confidence,
        "lookback_candles": min(lookback, len(candles)),
        "entry": round(price, 8),
        "tp1": round(targets[0], 8),
        "tp2": round(targets[1], 8),
        "tp3": round(targets[2], 8),
        "sl": round(stop, 8),
        "price": round(price, 8),
        "rsi": round(rsi, 2),
        "ema9": round(ema9, 8),
        "ema20": round(ema20, 8),
        "ema50": round(ema50, 8),
        "ema200": round(ema200, 8),
        "macd": "BULLISH" if macd_bull else "BEARISH",
        "structure": structure,
        "support_50": round(support, 8),
        "resistance_50": round(resistance, 8),
        "reasons": reasons,
        "score": score,
        "note": "Signal uses recent 50-candle price action with EMA warm-up from available history. Heuristic only; not guaranteed. Confirm higher timeframes before trading.",
    }
