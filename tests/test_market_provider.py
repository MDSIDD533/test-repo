"""Provider-layer tests for app/market.py.

Every API test monkeypatches ``candles``, so the real HTTP parsing, endpoint
fallback and error contract had no coverage. These tests use a fake
``requests`` module: they never touch the network.

The payloads below are *shape fixtures* built from Binance's documented REST
schema. They are synthetic and are never presented as recorded market data.
"""
import pytest

from app import market


class FakeResponse:
    def __init__(self, payload, *, fail=None):
        self._payload = payload
        self._fail = fail

    def raise_for_status(self):
        if self._fail:
            raise self._fail

    def json(self):
        return self._payload


_CACHED_SYMBOLS = market.eligible_usdt_spot_symbols


@pytest.fixture(autouse=True)
def clean_symbol_cache():
    # Clear the cache on the original function object: some tests replace the
    # module attribute, but the lru_cache lives on the wrapped function.
    _CACHED_SYMBOLS.cache_clear()
    yield
    _CACHED_SYMBOLS.cache_clear()


def fake_get(monkeypatch, responses):
    """Install a fake requests.get that answers per-call and records URLs."""
    calls = []

    def _get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout})
        index = len(calls) - 1
        result = responses[index]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(market.requests, "get", _get)
    return calls


def test_configured_base_url_is_used_first_with_connect_and_read_timeouts(monkeypatch):
    calls = fake_get(monkeypatch, [FakeResponse({"ok": True})])

    assert market._get("/api/v3/ping") == {"ok": True}
    assert calls[0]["url"] == market.BASE_URL + "/api/v3/ping"
    assert calls[0]["timeout"] == (4, 8)


def test_generic_failure_falls_back_to_the_next_binance_endpoint(monkeypatch):
    import requests

    calls = fake_get(
        monkeypatch,
        [
            requests.ConnectionError("primary host unreachable"),
            FakeResponse([{"symbol": "BTCUSDT"}]),
        ],
    )

    assert market._get("/api/v3/klines") == [{"symbol": "BTCUSDT"}]
    assert len(calls) == 2
    assert calls[1]["url"].startswith("https://api1.binance.com")


def test_all_endpoints_failing_raises_a_market_data_error_naming_each_host(monkeypatch):
    import requests

    monkeypatch.setattr(market, "BASE_URL", "https://api.binance.com")
    attempts = len(market.DEFAULT_ENDPOINTS)
    fake_get(monkeypatch, [requests.Timeout("timed out")] * attempts)

    with pytest.raises(market.MarketDataError) as caught:
        market._get("/api/v3/klines")

    message = str(caught.value)
    for endpoint in market.DEFAULT_ENDPOINTS:
        assert endpoint in message
    assert "timed out" in message
    # The UI/API maps this type to 502; it must not surface as a bare ValueError.
    assert not isinstance(caught.value, ValueError)


def test_http_error_status_is_treated_as_a_provider_failure(monkeypatch):
    import requests

    monkeypatch.setattr(market, "BASE_URL", "https://example.invalid")
    response = FakeResponse({}, fail=requests.HTTPError("429 Too Many Requests"))
    fake_get(monkeypatch, [response])

    with pytest.raises(market.MarketDataError, match="429"):
        market._get("/api/v3/klines")


def test_custom_base_url_does_not_silently_use_public_fallbacks(monkeypatch):
    import requests

    monkeypatch.setattr(market, "BASE_URL", "https://proxy.example.test")
    calls = fake_get(monkeypatch, [requests.ConnectionError("no route")])

    with pytest.raises(market.MarketDataError, match="proxy.example.test"):
        market._get("/api/v3/klines")
    assert len(calls) == 1


def test_eligible_symbols_filter_status_market_quote_and_leveraged_tokens(monkeypatch):
    payload = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "isSpotTradingAllowed": True, "quoteAsset": "USDT"},
            {"symbol": "ETHUSDT", "status": "TRADING", "isSpotTradingAllowed": True, "quoteAsset": "USDT"},
            {"symbol": "HALTUSDT", "status": "BREAK", "isSpotTradingAllowed": True, "quoteAsset": "USDT"},
            {"symbol": "MARGINUSDT", "status": "TRADING", "isSpotTradingAllowed": False, "quoteAsset": "USDT"},
            {"symbol": "BTCEUR", "status": "TRADING", "isSpotTradingAllowed": True, "quoteAsset": "EUR"},
            {"symbol": "BTCUPUSDT", "status": "TRADING", "isSpotTradingAllowed": True, "quoteAsset": "USDT"},
            {"symbol": "BTCUSDC", "status": "TRADING", "isSpotTradingAllowed": True, "quoteAsset": "USDC"},
        ]
    }
    fake_get(monkeypatch, [FakeResponse(payload)])

    assert market.eligible_usdt_spot_symbols() == {"BTCUSDT", "ETHUSDT"}


def test_eligible_symbols_are_cached_between_calls(monkeypatch):
    calls = fake_get(
        monkeypatch,
        [
            FakeResponse(
                {
                    "symbols": [
                        {"symbol": "BTCUSDT", "status": "TRADING", "isSpotTradingAllowed": True, "quoteAsset": "USDT"}
                    ]
                }
            )
        ],
    )

    market.eligible_usdt_spot_symbols()
    market.eligible_usdt_spot_symbols()

    assert len(calls) == 1


def test_candles_reject_an_unsupported_interval_before_any_request(monkeypatch):
    calls = fake_get(monkeypatch, [])

    with pytest.raises(ValueError, match="Unsupported candle interval"):
        market.candles("BTCUSDT", "7m")

    assert calls == []


def test_candles_reject_a_symbol_outside_the_eligible_usdt_spot_list(monkeypatch):
    monkeypatch.setattr(market, "eligible_usdt_spot_symbols", lambda: {"BTCUSDT"})

    with pytest.raises(ValueError, match="not an eligible Binance USDT spot pair"):
        market.candles("BTCUPUSDT", "5m")


def test_candles_map_binance_kline_rows_to_seconds_and_floats(monkeypatch):
    monkeypatch.setattr(market, "eligible_usdt_spot_symbols", lambda: {"BTCUSDT"})
    raw = [
        [1_700_000_000_000, "100.0", "101.5", "99.5", "100.5", "12.345", 1_700_000_299_999, "0", 10, "0", "0", "0"],
        [1_700_000_300_000, "100.5", "102.0", "100.0", "101.5", "8.0", 1_700_000_599_999, "0", 10, "0", "0", "0"],
    ]
    calls = fake_get(monkeypatch, [FakeResponse(raw)])

    rows = market.candles("btcusdt", "5m", 200)

    assert [row["time"] for row in rows] == [1_700_000_000.0, 1_700_000_300.0]
    assert rows[0] == {
        "time": 1_700_000_000.0,
        "open": 100.0,
        "high": 101.5,
        "low": 99.5,
        "close": 100.5,
        "volume": 12.345,
    }
    assert calls[0]["params"] == {"symbol": "BTCUSDT", "interval": "5m", "limit": 200}


@pytest.mark.parametrize(
    "requested, expected",
    [(5, 30), (35, 35), (500, 500), (900, 500)],
)
def test_candles_clamp_the_requested_limit(monkeypatch, requested, expected):
    monkeypatch.setattr(market, "eligible_usdt_spot_symbols", lambda: {"BTCUSDT"})
    calls = fake_get(monkeypatch, [FakeResponse([])])

    market.candles("BTCUSDT", "1h", requested)

    assert calls[0]["params"]["limit"] == expected


def test_all_24h_tickers_filters_skips_malformed_rows_and_sorts_by_volume(monkeypatch):
    monkeypatch.setattr(market, "eligible_usdt_spot_symbols", lambda: {"BTCUSDT", "ETHUSDT", "SOLUSDT"})
    payload = [
        {"symbol": "BTCUSDT", "lastPrice": "65000", "priceChangePercent": "1.5", "quoteVolume": "900", "highPrice": "66000", "lowPrice": "64000"},
        {"symbol": "ETHUSDT", "lastPrice": "3200", "priceChangePercent": "-0.5", "quoteVolume": "1500", "highPrice": "3300", "lowPrice": "3100"},
        {"symbol": "SOLUSDT", "lastPrice": "not-a-number", "priceChangePercent": "1", "quoteVolume": "500", "highPrice": "1", "lowPrice": "1"},
        {"symbol": "DOGEUSDT", "lastPrice": "0.1", "priceChangePercent": "5", "quoteVolume": "99999", "highPrice": "1", "lowPrice": "1"},
    ]
    fake_get(monkeypatch, [FakeResponse(payload)])

    rows = market.all_24h_tickers()

    assert [row["symbol"] for row in rows] == ["ETHUSDT", "BTCUSDT"]
    assert rows[0]["price"] == 3200.0
    assert rows[0]["change_percent"] == -0.5
    assert rows[0]["quote_volume"] == 1500.0


def test_ticker_payload_that_is_not_a_list_is_a_provider_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(market, "eligible_usdt_spot_symbols", lambda: {"BTCUSDT"})
    fake_get(monkeypatch, [FakeResponse({"unexpected": "object"})])

    with pytest.raises(market.MarketDataError, match="ticker payload was not a list"):
        market.all_24h_tickers()


def test_non_object_symbol_entries_are_skipped_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(market, "eligible_usdt_spot_symbols", lambda: {"BTCUSDT"})
    fake_get(
        monkeypatch,
        [
            FakeResponse(
                [
                    "not-an-object",
                    {"symbol": "BTCUSDT", "lastPrice": "100", "priceChangePercent": "1", "quoteVolume": "5", "highPrice": "101", "lowPrice": "99"},
                ]
            )
        ],
    )

    assert [row["symbol"] for row in market.all_24h_tickers()] == ["BTCUSDT"]


def test_kline_schema_drift_is_reported_as_a_provider_error(monkeypatch):
    monkeypatch.setattr(market, "eligible_usdt_spot_symbols", lambda: {"BTCUSDT"})
    fake_get(monkeypatch, [FakeResponse({"code": -1121, "msg": "Invalid symbol."})])

    with pytest.raises(market.MarketDataError, match="kline payload was not a list"):
        market.candles("BTCUSDT", "5m")


def test_exchange_info_schema_drift_is_reported_as_a_provider_error(monkeypatch):
    fake_get(monkeypatch, [FakeResponse([{"unexpected": True}])])

    with pytest.raises(market.MarketDataError, match="exchangeInfo"):
        market.eligible_usdt_spot_symbols()
