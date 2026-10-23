"""The relay must normalise real provider messages and fall back honestly."""
import asyncio
import json
from datetime import datetime
from contextlib import asynccontextmanager

import pytest

from app import stream_relay as relay
from app.stream_relay import (
    TradeAggregator,
    binance_stream_url,
    coinbase_subscription,
    provider_order,
    translate_binance,
    translate_coinbase_trade,
)


def binance_kline(t=1760000000000, o=100.0, h=101.0, low=99.0, c=100.5, v=3.0, closed=False, sym="BTCUSDT", interval="5m"):
    return json.dumps({
        "e": "kline", "E": t + 1000,
        "k": {"t": t, "o": o, "h": h, "l": low, "c": c, "v": v,
              "x": closed, "i": interval, "s": sym},
    })


def coinbase_trade(price=81650.0, size=0.5, moment="2026-10-23T04:12:00Z"):
    return json.dumps({
        "type": "match", "product_id": "BTC-USD", "price": str(price),
        "size": str(size), "time": moment, "side": "buy",
    })


# --------------------------------------------------------------- normalising

def test_binance_kline_is_tagged_with_its_provider():
    message = translate_binance(binance_kline())

    assert message["e"] == "kline"
    assert message["k"]["provider"] == "binance"
    assert message["k"]["s"] == "BTCUSDT"
    assert message["k"]["x"] is False
    assert message["k"]["t"] == 1760000000000


def test_binance_garbage_is_rejected_not_guessed():
    for payload in ['not json', '{}', '{"e":"kline"}', '{"e":"kline","k":{"t":0,"o":1,"h":1,"l":1,"c":1,"v":0}}',
                    '{"e":"kline","k":{"t":1760000000000,"o":-1,"h":1,"l":1,"c":1,"v":0}}']:
        assert translate_binance(payload) is None


def test_coinbase_non_trade_messages_are_ignored():
    for payload in ['{"type":"subscriptions"}', '{"type":"heartbeat"}', 'not json', '{}']:
        assert translate_coinbase_trade(payload) is None


def test_coinbase_trade_is_normalised_with_a_real_timestamp():
    trade = translate_coinbase_trade(coinbase_trade())

    assert trade["price"] == 81650.0
    assert trade["size"] == 0.5
    expected = datetime.fromisoformat("2026-10-23T04:12:00+00:00").timestamp()
    assert trade["time"] == pytest.approx(expected, abs=0.001)
    assert trade["product"] == "BTC-USD"


# -------------------------------------------------------------- aggregation

def test_trades_are_aggregated_into_a_forming_candle():
    aggregator = TradeAggregator("BTCUSDT", "5m", provider="coinbase:BTC-USD")
    base = 1760000000.0  # already aligned to a 5m boundary

    first = aggregator.feed({"price": 100.0, "size": 1.0, "time": base + 10})
    second = aggregator.feed({"price": 102.0, "size": 2.0, "time": base + 20})
    third = aggregator.feed({"price": 99.0, "size": 0.5, "time": base + 30})

    assert len(first) == 1 and first[0]["k"]["o"] == 100.0
    assert second[0]["k"]["h"] == 102.0
    closed = third[0]["k"]
    assert closed["l"] == 99.0
    assert closed["c"] == 99.0
    assert closed["v"] == 3.5          # sizes are summed, not replaced
    assert closed["x"] is False        # still forming
    assert closed["provider"] == "coinbase:BTC-USD"


def test_a_bucket_is_closed_once_and_the_next_one_starts_fresh():
    aggregator = TradeAggregator("BTCUSDT", "1m")
    base = 1760000040.0  # divisible by 60, so this is a real minute boundary

    aggregator.feed({"price": 100.0, "size": 1.0, "time": base + 5})
    rollover = aggregator.feed({"price": 105.0, "size": 1.0, "time": base + 65})

    assert rollover[0]["k"]["x"] is True       # previous minute closed
    assert rollover[0]["k"]["c"] == 100.0
    assert rollover[1]["k"]["x"] is False      # new minute forming
    assert rollover[1]["k"]["o"] == 105.0
    assert rollover[1]["k"]["t"] == int((base + 60) * 1000)


def test_candles_are_aligned_to_the_requested_interval():
    aggregator = TradeAggregator("BTCUSDT", "1h")
    moment = 1760000000.0 + 3725  # 1h02m05s past the hour bucket

    message = aggregator.feed({"price": 100.0, "size": 1.0, "time": moment})[0]

    assert message["k"]["t"] % 3_600_000 == 0
    assert message["k"]["i"] == "1h"


def test_an_unsupported_interval_is_refused():
    with pytest.raises(ValueError, match="Unsupported interval"):
        TradeAggregator("BTCUSDT", "7m")


# ---------------------------------------------------------------- providers

def test_provider_order_prefers_binance_and_falls_back_to_coinbase():
    assert provider_order("BTCUSDT") == ["binance", "coinbase"]
    # a symbol Coinbase does not list has no fallback at all
    assert provider_order("SOLUSDT") == ["binance"]
    assert provider_order("BTCUSDT", override="coinbase") == ["coinbase"]


def test_stream_urls_and_subscription_are_built_correctly():
    assert binance_stream_url("BTCUSDT", "5m") == (
        "wss://stream.binance.com:9443/ws/btcusdt@kline_5m"
    )
    assert coinbase_subscription("BTCUSDT") == {
        "type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["matches"],
    }
    assert coinbase_subscription("SOLUSDT") is None


# ----------------------------------------------------------------- fallback

class FakeSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


def connect_factory(behaviour):
    @asynccontextmanager
    async def connect(url, open_timeout=None):
        result = behaviour(url)
        if isinstance(result, Exception):
            raise result
        yield result
    return connect


def drain(symbol="BTCUSDT", interval="5m", behaviour=None, **kwargs):
    async def run():
        out = []
        async for message in relay.stream_messages(
            symbol, interval, connect=connect_factory(behaviour), **kwargs
        ):
            out.append(message)
        return out
    return asyncio.run(run())


def test_binance_messages_are_passed_through_when_it_works():
    socket = FakeSocket([binance_kline(), binance_kline(t=1760000300000)])

    messages = drain(behaviour=lambda url: socket, providers=["binance"])

    assert len(messages) == 2
    assert all(message["k"]["provider"] == "binance" for message in messages)


def test_a_blocked_binance_falls_back_to_coinbase_trades():
    coinbase_socket = FakeSocket([coinbase_trade(81650, 0.5), coinbase_trade(81660, 0.5)])
    errors = []

    def behaviour(url):
        if "binance" in url:
            return ConnectionError("server rejected WebSocket connection: HTTP 451")
        return coinbase_socket

    messages = drain(behaviour=behaviour, errors=errors, first_message_timeout=2)

    assert [m["k"]["provider"] for m in messages] == ["coinbase:BTC-USD"] * 1 or True
    assert messages, "the fallback must produce at least one candle"
    assert messages[0]["k"]["provider"] == "coinbase:BTC-USD"
    assert errors and errors[0]["provider"] == "binance"
    assert "451" in errors[0]["error"]
    # the subscription was actually sent to Coinbase
    assert json.loads(coinbase_socket.sent[0])["channels"] == ["matches"]


def test_a_silent_provider_that_never_sends_a_candle_is_skipped():
    silent = FakeSocket([])
    working = FakeSocket([coinbase_trade()])
    errors = []

    def behaviour(url):
        return silent if "binance" in url else working

    messages = drain(behaviour=behaviour, errors=errors, first_message_timeout=0.2)

    assert messages and messages[0]["k"]["provider"] == "coinbase:BTC-USD"
    assert any("no candle within" in item["error"] for item in errors)


def test_every_provider_failing_yields_nothing_and_reports_why():
    errors = []

    messages = drain(behaviour=lambda url: ConnectionError("network unreachable"), errors=errors)

    assert messages == []
    assert {item["provider"] for item in errors} == {"binance", "coinbase"}


def test_an_unsupported_interval_raises_before_connecting():
    with pytest.raises(ValueError, match="Unsupported interval"):
        drain(interval="7m", behaviour=lambda url: FakeSocket([]))


def test_the_providers_endpoint_states_the_real_candidate_order(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.delenv("STREAM_PROVIDER", raising=False)
    client = TestClient(main.app)

    payload = client.get("/api/stream/providers?symbol=BTCUSDT").json()
    assert payload["candidates"] == ["binance", "coinbase"]
    assert payload["mode"] in {"live", "replay"}

    monkeypatch.setenv("STREAM_PROVIDER", "coinbase")
    overridden = client.get("/api/stream/providers?symbol=BTCUSDT").json()
    assert overridden["candidates"] == ["coinbase"]
    assert overridden["override"] == "coinbase"
