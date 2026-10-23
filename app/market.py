from __future__ import annotations
import os
from pathlib import Path

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


# Binance geo-blocks some locations (HTTP 451) and is unreachable from others.
# Coinbase's public API serves the same kind of spot candles for BTC-USD, and
# this repository's experiment runner has always used it as a fallback. It is
# used here only for the symbols/intervals it genuinely supports, and every
# response states which provider produced it. Nothing is silently substituted.
COINBASE_BASE_URL = 'https://api.exchange.coinbase.com'
COINBASE_GRANULARITIES = {'1m': 60, '5m': 300, '15m': 900, '1h': 3600, '6h': 21600, '1d': 86400}
COINBASE_PRODUCTS = {'BTCUSDT': 'BTC-USD'}
_PROVIDER_USED: dict[tuple[str, str], str] = {}

# Replay mode serves a *recording* of real provider candles. It exists because
# some networks cannot reach an exchange at all. It is never allowed to look
# live: every consumer can ask what it is serving and when it was recorded.
REPLAY_MODE = os.getenv('MARKET_DATA_MODE', 'live').strip().lower() == 'replay'
REPLAY_FIXTURE = Path(os.getenv('MARKET_FIXTURE', 'fixtures/candles.json'))
# Intervals the recording does not contain, and the interval they are built from.
# Resampling real candles is arithmetic on real data; nothing is invented.
REPLAY_DERIVED = {'3m': '1m', '30m': '15m', '2h': '1h', '4h': '1h'}
_REPLAY_CACHE: dict[str, Any] = {}


def replay_metadata() -> dict[str, Any]:
    """Describe the recording being served, or say that there is none."""
    if not REPLAY_MODE:
        return {'mode': 'live'}
    fixture = _load_replay_fixture()
    if not fixture:
        return {'mode': 'replay', 'available': False, 'fixture': str(REPLAY_FIXTURE)}
    return {
        'mode': 'replay',
        'available': True,
        'fixture': str(REPLAY_FIXTURE),
        'symbol': fixture.get('symbol'),
        'fetched_at': fixture.get('fetched_at'),
        'newest_candle': fixture.get('newest_candle'),
        'providers': fixture.get('providers'),
        'intervals': sorted(fixture.get('intervals', {})),
        'derived': sorted(REPLAY_DERIVED),
        'note': 'Recorded market data, replayed with its original timestamps. Not live.',
    }


def _load_replay_fixture() -> dict[str, Any] | None:
    if _REPLAY_CACHE.get('fixture') is None and REPLAY_FIXTURE.exists():
        try:
            import json

            payload = json.loads(REPLAY_FIXTURE.read_text(encoding='utf-8'))
            if isinstance(payload, dict) and isinstance(payload.get('intervals'), dict):
                _REPLAY_CACHE['fixture'] = payload
        except (OSError, ValueError):
            return None
    return _REPLAY_CACHE.get('fixture')


def replay_tickers() -> list[dict[str, Any]]:
    """Build 24h rows from the recording itself, for the symbols it contains."""
    fixture = _load_replay_fixture()
    if not fixture:
        raise MarketDataError(
            f'Replay mode is on but no recording was found at {REPLAY_FIXTURE}'
        )
    symbol = str(fixture.get('symbol', '')).upper()
    rows = _replay_rows(symbol, '1h', 500)
    if not rows:
        raise MarketDataError('The replay recording contains no usable candles')
    first = rows[0]['open']
    last = rows[-1]['close']
    change = (last / first - 1.0) * 100.0 if first else 0.0
    window = len(rows)
    # Same keys as all_24h_tickers(); a replay row that used different names
    # broke the dashboard renderer instead of showing data.
    return [{
        'symbol': symbol,
        'price': last,
        'change_percent': round(change, 4),
        'quote_volume': round(last * sum(row['volume'] for row in rows), 2),
        'high': max(row['high'] for row in rows),
        'low': min(row['low'] for row in rows),
        'window_hours': window,
        'replay': True,
    }]


def _replay_rows(symbol: str, interval: str, limit: int) -> list[dict[str, float]]:
    fixture = _load_replay_fixture()
    if not fixture:
        raise MarketDataError(
            f'Replay mode is on but no recording was found at {REPLAY_FIXTURE}. '
            'Run scripts/export_candles_fixture.py where an exchange is reachable.'
        )
    if str(fixture.get('symbol', '')).upper() != symbol:
        raise MarketDataError(
            f'Replay recording only contains {fixture.get("symbol")}, not {symbol}'
        )
    intervals = fixture['intervals']
    source_interval = interval if interval in intervals else REPLAY_DERIVED.get(interval)
    if source_interval is None or source_interval not in intervals:
        raise MarketDataError(f'Replay recording has no {interval} candles and no source to derive them')

    rows = [
        {'time': float(r[0]), 'open': float(r[1]), 'high': float(r[2]),
         'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])}
        for r in intervals[source_interval]
    ]
    if source_interval != interval:
        rows = _resample(rows, INTERVAL_SECONDS[source_interval], INTERVAL_SECONDS[interval])
    if not rows:
        raise MarketDataError(f'Replay recording produced no {interval} candles')
    _PROVIDER_USED[(symbol, interval)] = (
        f'replay:{fixture.get("symbol")}@{source_interval}'
        + (f'->{interval}' if source_interval != interval else '')
    )
    return rows[-max(30, min(limit, 500)):]


INTERVAL_SECONDS = {'1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
                    '1h': 3600, '2h': 7200, '4h': 14400, '6h': 21600, '1d': 86400}


def _resample(rows: list[dict[str, float]], source_seconds: int, target_seconds: int) -> list[dict[str, float]]:
    """Aggregate real candles into a coarser interval; never invents a candle."""
    if target_seconds % source_seconds != 0:
        raise MarketDataError(
            f'Cannot derive {target_seconds}s candles from {source_seconds}s candles'
        )
    buckets: dict[int, dict[str, float]] = {}
    for row in rows:
        bucket = int(row['time'] // target_seconds) * target_seconds
        current = buckets.get(bucket)
        if current is None:
            buckets[bucket] = {
                'time': float(bucket), 'open': row['open'], 'high': row['high'],
                'low': row['low'], 'close': row['close'], 'volume': row['volume'],
            }
            continue
        current['high'] = max(current['high'], row['high'])
        current['low'] = min(current['low'], row['low'])
        current['close'] = row['close']
        current['volume'] += row['volume']
    return [buckets[key] for key in sorted(buckets)]


def provider_used(symbol: str, interval: str) -> str | None:
    """Return the provider that produced the most recent candle response.

    This is process-local bookkeeping so responses can state their real source
    instead of always claiming Binance.
    """
    return _PROVIDER_USED.get((symbol.upper(), interval))


def _coinbase_candles(symbol: str, interval: str, limit: int) -> list[dict[str, float]]:
    product = COINBASE_PRODUCTS[symbol]
    granularity = COINBASE_GRANULARITIES[interval]
    response = requests.get(
        f'{COINBASE_BASE_URL}/products/{product}/candles',
        params={'granularity': granularity},
        headers={'Accept': 'application/json', 'User-Agent': 'SiddTradingPro/1.0'},
        timeout=(5, 15),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise MarketDataError('Coinbase candle payload was not a list')
    rows = []
    # Coinbase rows are [time, low, high, open, close, volume], newest first.
    for item in reversed(payload):
        if not isinstance(item, list) or len(item) < 6:
            continue
        try:
            rows.append({
                'time': float(item[0]),
                'low': float(item[1]),
                'high': float(item[2]),
                'open': float(item[3]),
                'close': float(item[4]),
                'volume': float(item[5]),
            })
        except (TypeError, ValueError):
            continue
    if len(rows) < 2:
        raise MarketDataError('Coinbase returned too few usable candles')
    return rows[-limit:]

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

SUPPORTED_INTERVALS = {'1m','3m','5m','15m','30m','1h','2h','4h','1d'}


def candles(symbol: str, interval: str = '5m', limit: int = 200) -> list[dict[str, float]]:
    """Return spot candles, preferring Binance and falling back to Coinbase.

    The fallback is limited to the symbols and intervals Coinbase really
    supports; anything else still raises instead of inventing candles.
    """
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError('Unsupported candle interval')
    symbol = symbol.upper()
    window = max(30, min(limit, 500))
    if REPLAY_MODE:
        return _replay_rows(symbol, interval, window)
    try:
        if symbol not in eligible_usdt_spot_symbols():
            raise ValueError('Symbol is not an eligible Binance USDT spot pair')
        raw = _get('/api/v3/klines', {'symbol': symbol, 'interval': interval, 'limit': window})
        if not isinstance(raw, list):
            raise MarketDataError('Binance kline payload was not a list')
        rows = [
            {'time': r[0] / 1000, 'open': float(r[1]), 'high': float(r[2]),
             'low': float(r[3]), 'close': float(r[4]), 'volume': float(r[5])}
            for r in raw
        ]
        _PROVIDER_USED[(symbol, interval)] = 'binance'
        return rows
    except MarketDataError as binance_error:
        product = COINBASE_PRODUCTS.get(symbol)
        if product is None or interval not in COINBASE_GRANULARITIES:
            raise
        try:
            rows = _coinbase_candles(symbol, interval, window)
        except Exception as fallback_error:
            raise MarketDataError(
                f'Binance failed ({binance_error}) and the Coinbase fallback for {product} '
                f'also failed ({type(fallback_error).__name__}: {fallback_error})'
            ) from fallback_error
        _PROVIDER_USED[(symbol, interval)] = f'coinbase:{product}'
        return rows
