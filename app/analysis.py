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
    if len(delta) < period: return 50.0
    gain, loss = float(np.mean(gains[-period:])), float(np.mean(losses[-period:]))
    if loss == 0: return 100.0 if gain > 0 else 50.0
    rs = gain / loss
    return 100 - 100 / (1 + rs)

def analyze_candles(candles: list[dict[str, Any]], symbol: str = 'UNKNOWN', timeframe: str = '5m') -> dict[str, Any]:
    if len(candles) < 35: raise ValueError('At least 35 candles are needed for analysis')
    close = np.array([c['close'] for c in candles], dtype=float)
    high = np.array([c['high'] for c in candles], dtype=float)
    low = np.array([c['low'] for c in candles], dtype=float)
    opens = np.array([c['open'] for c in candles], dtype=float)
    price = float(close[-1])
    ema9, ema20, ema50, ema200 = (_ema(close, p) for p in (9, 20, 50, 200))
    rsi = _rsi(close)
    macd_line = _ema(close, 12) - _ema(close, 26)
    macd_signal = _ema(macd_line, 9)
    macd_bull = macd_line[-1] > macd_signal[-1]
    ranges = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr = float(np.mean(ranges[-14:])) if len(ranges) >= 14 else max(price * .005, 1e-8)
    score, reasons = 0, []
    if price > ema20[-1]: score += 1; reasons.append('Price above EMA20')
    else: score -= 1; reasons.append('Price below EMA20')
    if ema9[-1] > ema20[-1] > ema50[-1]: score += 1; reasons.append('Bullish EMA9/20/50 alignment')
    elif ema9[-1] < ema20[-1] < ema50[-1]: score -= 1; reasons.append('Bearish EMA9/20/50 alignment')
    if macd_bull: score += 1; reasons.append('MACD above signal line')
    else: score -= 1; reasons.append('MACD below signal line')
    if 50 <= rsi <= 68: score += 1; reasons.append('RSI supports bullish momentum')
    elif 32 <= rsi < 50: score -= 1; reasons.append('RSI shows weak momentum')
    elif rsi > 75: score -= 1; reasons.append('RSI is overbought; caution')
    elif rsi < 25: score += 1; reasons.append('RSI oversold; rebound is not confirmed')
    if close[-1] > opens[-1] and close[-1] > close[-2]: score += 1; reasons.append('Latest candle closes bullish')
    elif close[-1] < opens[-1] and close[-1] < close[-2]: score -= 1; reasons.append('Latest candle closes bearish')
    swing_high, swing_low = float(np.max(high[-11:-1])), float(np.min(low[-11:-1]))
    if price > swing_high: score += 1; reasons.append('Close broke above recent swing range')
    elif price < swing_low: score -= 1; reasons.append('Close broke below recent swing range')
    signal = 'BUY' if score >= 3 else 'SELL' if score <= -3 else 'WAIT'
    confidence = min(90, 50 + abs(score) * 6)
    if signal == 'BUY':
        stop = max(price - 1.5 * atr, price * .98); risk = max(price - stop, price * .002); targets = [price + risk * m for m in (1,2,3)]
    elif signal == 'SELL':
        stop = price + 1.5 * atr; risk = max(stop - price, price * .002); targets = [max(price - risk * m, 0) for m in (1,2,3)]
    else: stop = price - atr; targets = [price + atr * m for m in (1,2,3)]
    return {'symbol':symbol.upper(),'timeframe':timeframe,'signal':signal,'confidence':confidence,'entry':round(price,8),'tp1':round(targets[0],8),'tp2':round(targets[1],8),'tp3':round(targets[2],8),'sl':round(stop,8),'price':round(price,8),'rsi':round(rsi,2),'ema9':round(float(ema9[-1]),8),'ema20':round(float(ema20[-1]),8),'ema50':round(float(ema50[-1]),8),'ema200':round(float(ema200[-1]),8),'macd':'BULLISH' if macd_bull else 'BEARISH','structure':'BREAKOUT_UP' if price > swing_high else 'BREAKDOWN' if price < swing_low else 'RANGE','reasons':reasons,'score':score,'note':'Heuristic signal only; not guaranteed. Confirm higher timeframes before trading.'}
