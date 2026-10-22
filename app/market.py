from __future__ import annotations
import os
import requests
from functools import lru_cache
from typing import Any

BASE_URL = os.getenv('BINANCE_BASE_URL', 'https://api.binance.com').rstrip('/')
# Binance documents several API hostnames; trying alternates helps when one route
# is slow or unreachable from a user's network.
DEFAULT_ENDPOINTS = (
    'https://api.binance.com',
    'https://api1.binance.com',
    'https://api2.binance.com',
    'https://api3.binance.com',
)

class MarketDataError(RuntimeError):
    pass

def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    configured = [BASE_URL]
    if BASE_URL == 'https://api.binance.com':
        configured.extend(url for url in DEFAULT_ENDPOINTS if url != BASE_URL)
    errors = []
    for base_url in dict.fromkeys(configured):
        try:
            # Separate connect/read limits avoid hanging for a long time on a
            # connection that was established but never returned response data.
            response = requests.get(base_url + path, params=params, timeout=(4, 8))
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            errors.append(f'{base_url}: {exc}')
    detail = '; '.join(errors)
    raise MarketDataError(
        'Binance market data request failed after trying available API endpoints. '
        'Check internet/VPN/firewall access and retry. Details: ' + detail
    ) from (errors and None)

@lru_cache(maxsize=1)
def eligible_usdt_spot_symbols() -> set[str]:
    info = _get('/api/v3/exchangeInfo')
    if not isinstance(info, dict) or not isinstance(info.get('symbols'), list):
        raise MarketDataError('Binance exchangeInfo payload was not the expected object with a symbols list')
    excluded = ('UPUSDT', 'DOWNUSDT', 'BULLUSDT', 'BEARUSDT')
    result = set()
    for item in info.get('symbols', []):
        symbol = item.get('symbol', '')
        if item.get('status') == 'TRADING' and item.get('isSpotTradingAllowed', False) and item.get('quoteAsset') == 'USDT' and symbol.endswith('USDT') and not any(symbol.endswith(x) for x in excluded):
            result.add(symbol)
    return result

def all_24h_tickers() -> list[dict[str, Any]]:
    raw = _get('/api/v3/ticker/24hr')
    if not isinstance(raw, list):
        raise MarketDataError('Binance 24h ticker payload was not a list')
    allowed = eligible_usdt_spot_symbols()
    rows = []
    for item in raw:
        if not isinstance(item, dict) or item.get('symbol') not in allowed:
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
    if not isinstance(raw, list):
        raise MarketDataError('Binance kline payload was not a list')
    return [{'time': r[0] / 1000, 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])} for r in raw]
