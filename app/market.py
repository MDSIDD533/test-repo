from __future__ import annotations
import os
import requests
from functools import lru_cache
from typing import Any

BASE_URL = os.getenv('BINANCE_BASE_URL', 'https://api.binance.com').rstrip('/')

class MarketDataError(RuntimeError):
    pass

def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        response = requests.get(BASE_URL + path, params=params, timeout=12)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise MarketDataError('Binance market data request failed: ' + str(exc)) from exc

@lru_cache(maxsize=1)
def eligible_usdt_spot_symbols() -> set[str]:
    info = _get('/api/v3/exchangeInfo')
    excluded = ('UPUSDT', 'DOWNUSDT', 'BULLUSDT', 'BEARUSDT')
    result = set()
    for item in info.get('symbols', []):
        symbol = item.get('symbol', '')
        if item.get('status') == 'TRADING' and item.get('isSpotTradingAllowed', False) and item.get('quoteAsset') == 'USDT' and symbol.endswith('USDT') and not any(symbol.endswith(x) for x in excluded):
            result.add(symbol)
    return result

def all_24h_tickers() -> list[dict[str, Any]]:
    raw = _get('/api/v3/ticker/24hr')
    allowed = eligible_usdt_spot_symbols()
    rows = []
    for item in raw:
        if item.get('symbol') not in allowed:
            continue
        try:
            rows.append({'symbol': item['symbol'], 'price': float(item['lastPrice']), 'change_percent': float(item['priceChangePercent']), 'quote_volume': float(item['quoteVolume']), 'high': float(item['highPrice']), 'low': float(item['lowPrice'])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows, key=lambda row: row['quote_volume'], reverse=True)

def candles(symbol: str, interval: str = '5m', limit: int = 200) -> list[dict[str, float]]:
    if interval not in {'1m','3m','5m','15m','30m','1h','2h','4h','1d'}:
        raise ValueError('Unsupported candle interval')
    symbol = symbol.upper()
    if symbol not in eligible_usdt_spot_symbols():
        raise ValueError('Symbol is not an eligible Binance USDT spot pair')
    raw = _get('/api/v3/klines', {'symbol': symbol, 'interval': interval, 'limit': max(30, min(limit, 500))})
    return [{'time': r[0] / 1000, 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in raw]
