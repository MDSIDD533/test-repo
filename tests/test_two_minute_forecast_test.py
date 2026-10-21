import pytest

from scripts.two_minute_forecast_test import fetch_experiment_spot_price


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_coinbase_spot_ticker_is_used_for_btc_fallback(monkeypatch):
    import scripts.two_minute_forecast_test as runner

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return FakeResponse({"price": "65000.25"})

    monkeypatch.setattr(runner.requests, "get", fake_get)

    assert fetch_experiment_spot_price("BTCUSDT", "coinbase:BTC-USD") == 65000.25
    assert seen["url"].endswith("/products/BTC-USD/ticker")


def test_binance_spot_ticker_is_used_for_binance_provider(monkeypatch):
    import scripts.two_minute_forecast_test as runner

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs["params"]
        return FakeResponse({"price": "65001.5"})

    monkeypatch.setattr(runner.requests, "get", fake_get)

    assert fetch_experiment_spot_price("BTCUSDT", "binance") == 65001.5
    assert seen["url"].endswith("/api/v3/ticker/price")
    assert seen["params"] == {"symbol": "BTCUSDT"}


def test_spot_ticker_rejects_non_positive_price(monkeypatch):
    import scripts.two_minute_forecast_test as runner

    monkeypatch.setattr(runner.requests, "get", lambda *args, **kwargs: FakeResponse({"price": "0"}))
    with pytest.raises(RuntimeError, match="non-finite or non-positive"):
        fetch_experiment_spot_price("BTCUSDT", "coinbase:BTC-USD")
