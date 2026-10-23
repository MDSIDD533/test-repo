import pytest

from app import market


INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def kline_payload(interval, limit=60):
    step = INTERVAL_SECONDS[interval]
    # Start at a UTC boundary that is aligned to every supported interval.
    start = 1700006400 - (limit * step)
    start -= start % step
    rows = []
    for index in range(limit):
        price = 100.0 + index * 0.01
        rows.append([
            (start + index * step) * 1000,
            f"{price:.2f}",
            f"{price + 0.5:.2f}",
            f"{price - 0.5:.2f}",
            f"{price + 0.2:.2f}",
            "10.0",
        ])
    return rows


@pytest.fixture
def market_requests(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        return FakeResponse(kline_payload(params["interval"]))

    monkeypatch.setattr(market, "eligible_usdt_spot_symbols", lambda: {"BTCUSDT"})
    monkeypatch.setattr(market.requests, "get", fake_get)
    return calls


@pytest.mark.parametrize("interval, seconds", sorted(INTERVAL_SECONDS.items()))
def test_candle_request_forwards_the_exact_interval(market_requests, interval, seconds):
    rows = market.candles("btcusdt", interval, 60)

    assert len(rows) == 60
    assert market_requests[0]["params"]["symbol"] == "BTCUSDT"
    assert market_requests[0]["params"]["interval"] == interval
    assert market_requests[0]["params"]["limit"] == 60

    times = [row["time"] for row in rows]
    assert times == sorted(times)
    assert all(b - a == seconds for a, b in zip(times, times[1:])), (
        f"{interval} candles were not spaced exactly {seconds}s apart"
    )
    # 1h candles must be one hour wide, not five-minute candles aggregated or repeated.
    if interval == "1h":
        assert all(int(time) % 3600 == 0 for time in times)


def test_unsupported_interval_is_rejected_before_a_network_call(market_requests):
    with pytest.raises(ValueError, match="Unsupported candle interval"):
        market.candles("BTCUSDT", "7m", 60)
    assert market_requests == []


def test_ineligible_symbol_is_rejected_before_a_network_call(market_requests):
    with pytest.raises(ValueError, match="not an eligible"):
        market.candles("NOTREALUSDT", "5m", 60)
    assert market_requests == []


def test_limit_is_clamped_to_provider_safe_bounds(market_requests):
    market.candles("BTCUSDT", "5m", 5000)
    assert market_requests[0]["params"]["limit"] == 500

    market.candles("BTCUSDT", "5m", 1)
    assert market_requests[1]["params"]["limit"] == 30
