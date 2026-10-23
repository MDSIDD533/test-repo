"""Server-side market stream relay with provider fallback.

The chart used to connect straight from the browser to
``wss://stream.binance.com``. That fails on every network Binance geo-blocks
(HTTP 451), which is most CI runners and some user networks, and it also means
the browser talks to a third party directly.

This relay connects **server side** and tries providers in order:

1. Binance kline stream.
2. Coinbase ``matches`` stream, aggregated into the requested interval.

Both are normalised into one canonical message that the chart already validates
(``{"e": "kline", "k": {...}}``). Every message states which provider actually
produced it, so the UI can never show Binance data as if it came from Coinbase
or vice versa.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Iterable

INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}
BINANCE_WS = "wss://stream.binance.com:9443/ws"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
COINBASE_PRODUCTS = {"BTCUSDT": "BTC-USD"}

# The canonical wire shape the chart parses. It deliberately matches Binance's
# kline fields because that is the contract static/chart-core.js validates.
def kline_message(
    *,
    symbol: str,
    interval: str,
    start_seconds: float,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    closed: bool,
    provider: str,
    event_time: float | None = None,
) -> dict[str, Any]:
    return {
        "e": "kline",
        "E": int((event_time if event_time is not None else start_seconds * 1000)),
        "k": {
            "t": int(start_seconds * 1000),
            "o": open_price,
            "h": high,
            "l": low,
            "c": close,
            "v": volume,
            "x": bool(closed),
            "i": interval,
            "s": symbol,
            "provider": provider,
        },
    }


def binance_stream_url(symbol: str, interval: str) -> str:
    return f"{BINANCE_WS}/{symbol.lower()}@kline_{interval}"


def coinbase_subscription(symbol: str) -> dict[str, Any] | None:
    product = COINBASE_PRODUCTS.get(symbol.upper())
    if not product:
        return None
    return {
        "type": "subscribe",
        "product_ids": [product],
        "channels": ["matches"],
    }


def provider_order(symbol: str, override: str | None = None) -> list[str]:
    """Providers to try, in order, for a symbol."""
    symbol = symbol.upper()
    if override and override != "auto":
        return [override]
    order = ["binance"]
    if symbol in COINBASE_PRODUCTS:
        order.append("coinbase")
    return order


def translate_binance(raw: str | bytes) -> dict[str, Any] | None:
    """Pass a Binance kline message through, tagging its provider."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("e") != "kline":
        return None
    kline = payload.get("k")
    if not isinstance(kline, dict):
        return None
    try:
        start_ms = float(kline["t"])
        open_price = float(kline["o"])
        high = float(kline["h"])
        low = float(kline["l"])
        close = float(kline["c"])
        volume = float(kline["v"])
    except (KeyError, TypeError, ValueError):
        return None
    if start_ms <= 0 or min(open_price, high, low, close) <= 0 or volume < 0:
        return None
    return kline_message(
        symbol=str(kline.get("s", "")).upper(),
        interval=str(kline.get("i", "")),
        start_seconds=start_ms / 1000.0,
        open_price=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        closed=kline.get("x") is True,
        provider="binance",
        event_time=float(payload["E"]) if isinstance(payload.get("E"), (int, float)) else None,
    )


def translate_coinbase_trade(raw: str | bytes) -> dict[str, Any] | None:
    """Return a normalised trade, or None for non-trade messages."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") not in {"match", "last_match"}:
        return None
    try:
        price = float(payload["price"])
        size = float(payload.get("size", 0.0) or 0.0)
        occurred = datetime.fromisoformat(
            str(payload["time"]).replace("Z", "+00:00")
        ).timestamp()
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or size < 0:
        return None
    return {
        "price": price,
        "size": size,
        "time": occurred,
        "product": payload.get("product_id"),
    }


class TradeAggregator:
    """Build candles for one interval out of individual trades.

    Coinbase's public stream publishes trades, not candles. Aggregating them is
    arithmetic on real trades: the open is the first trade in the bucket, the
    high/low are the observed extremes, and the volume is the sum of sizes. A
    bucket is emitted once as forming and once more when it closes.
    """

    def __init__(self, symbol: str, interval: str, provider: str = "coinbase"):
        if interval not in INTERVAL_SECONDS:
            raise ValueError(f"Unsupported interval: {interval}")
        self.symbol = symbol.upper()
        self.interval = interval
        self.provider = provider
        self.seconds = INTERVAL_SECONDS[interval]
        self._bucket: dict[str, float] | None = None

    def _start(self, moment: float) -> float:
        return float(int(moment // self.seconds) * self.seconds)

    def feed(self, trade: dict[str, Any]) -> list[dict[str, Any]]:
        price = float(trade["price"])
        size = float(trade.get("size", 0.0) or 0.0)
        moment = float(trade["time"])
        start = self._start(moment)
        emitted: list[dict[str, Any]] = []

        if self._bucket is not None and start > self._bucket["start"]:
            # The previous candle is finished; publish it as closed so the chart
            # stops mutating it and starts a new bar.
            emitted.append(self._message(self._bucket, closed=True))
            self._bucket = None
        if self._bucket is None:
            self._bucket = {
                "start": start, "open": price, "high": price,
                "low": price, "close": price, "volume": size,
            }
        else:
            self._bucket["high"] = max(self._bucket["high"], price)
            self._bucket["low"] = min(self._bucket["low"], price)
            self._bucket["close"] = price
            self._bucket["volume"] += size
        emitted.append(self._message(self._bucket, closed=False))
        return emitted

    def _message(self, bucket: dict[str, float], *, closed: bool) -> dict[str, Any]:
        return kline_message(
            symbol=self.symbol,
            interval=self.interval,
            start_seconds=bucket["start"],
            open_price=bucket["open"],
            high=bucket["high"],
            low=bucket["low"],
            close=bucket["close"],
            volume=bucket["volume"],
            closed=closed,
            provider=self.provider,
        )


async def _relay(ws: Any, provider: str, symbol: str, interval: str) -> AsyncIterator[dict[str, Any]]:
    aggregator = (
        TradeAggregator(symbol, interval, provider=f"coinbase:{COINBASE_PRODUCTS[symbol]}")
        if provider == "coinbase" else None
    )
    while True:
        try:
            raw = await ws.recv()
        except StopAsyncIteration:
            # The socket ended. Returning keeps async-generator semantics clean
            # instead of turning exhaustion into a RuntimeError (PEP 479).
            return
        if aggregator is None:
            message = translate_binance(raw)
            if message is not None:
                yield message
            continue
        trade = translate_coinbase_trade(raw)
        if trade is None:
            continue
        for message in aggregator.feed(trade):
            yield message


async def stream_messages(
    symbol: str,
    interval: str,
    *,
    connect: Callable[..., Any] | None = None,
    providers: Iterable[str] | None = None,
    open_timeout: float = 8.0,
    first_message_timeout: float = 12.0,
    errors: list[dict[str, str]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield canonical kline messages, falling back to the next provider.

    ``connect`` is injected in tests; in production it is ``websockets.connect``.
    A provider that connects but never sends a candle within
    ``first_message_timeout`` seconds is treated as failed, because an open
    socket that silently yields nothing is not a working feed.
    Failures are appended to ``errors`` so the caller can log why a provider was
    skipped instead of silently pretending everything worked.
    """
    if interval not in INTERVAL_SECONDS:
        raise ValueError(f"Unsupported interval: {interval}")
    symbol = symbol.upper()
    if connect is None:
        import websockets

        connect = websockets.connect
    order = list(providers if providers is not None else provider_order(symbol))

    for provider in order:
        if provider == "binance":
            url = binance_stream_url(symbol, interval)
            handshake = None
        elif provider == "coinbase":
            handshake = coinbase_subscription(symbol)
            if handshake is None:
                if errors is not None:
                    errors.append({"provider": provider, "error": "symbol is not listed on Coinbase"})
                continue
            url = COINBASE_WS
        else:
            if errors is not None:
                errors.append({"provider": provider, "error": "unknown provider"})
            continue

        try:
            async with connect(url, open_timeout=open_timeout) as ws:
                if handshake is not None:
                    await ws.send(json.dumps(handshake))
                messages = _relay(ws, provider, symbol, interval)
                first = await asyncio.wait_for(messages.__anext__(), timeout=first_message_timeout)
                yield first
                async for message in messages:
                    yield message
        except (StopAsyncIteration, asyncio.TimeoutError) as exc:
            if errors is not None:
                errors.append({
                    "provider": provider,
                    "error": f"no candle within {first_message_timeout}s ({type(exc).__name__})",
                })
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport failure means "try the next one"
            if errors is not None:
                errors.append({"provider": provider, "error": f"{type(exc).__name__}: {exc}"})
            continue
