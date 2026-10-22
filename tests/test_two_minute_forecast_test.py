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


def test_exact_ticker_observation_records_zero_time_difference(monkeypatch):
    import scripts.two_minute_forecast_test as runner

    monkeypatch.setattr(runner, "fetch_experiment_spot_price", lambda symbol, provider: 65000.0)
    result = runner.fetch_actual_price_with_fallback("BTCUSDT", "binance", 1_700_000_120.0)
    assert result["price"] == 65000.0
    assert result["source"] == "spot_ticker"
    assert result["time_difference_seconds"] == 0.0


def test_nearest_completed_candle_is_used_with_documented_time_difference(monkeypatch):
    import scripts.two_minute_forecast_test as runner

    def no_ticker(symbol, provider):
        raise RuntimeError("ticker unavailable")

    rows = [
        {"time": 0.0, "close": 100.0},
        {"time": 60.0, "close": 101.0},
        {"time": 120.0, "close": 102.0},
    ]
    monkeypatch.setattr(runner, "fetch_experiment_spot_price", no_ticker)
    monkeypatch.setattr(runner, "fetch_experiment_candles", lambda symbol, limit: (rows, "test-provider"))

    result = runner.fetch_actual_price_with_fallback("BTCUSDT", "binance", 125.0)

    assert result["source"] == "nearest_completed_1m_candle_close"
    assert result["price"] == 101.0  # candle starting at 60s closes at 120s
    assert result["observed_at"] == 120.0
    assert result["time_difference_seconds"] == 5.0
    assert "ticker unavailable" in result["ticker_error"]


def test_fallback_rejects_an_observation_outside_the_tolerance(monkeypatch):
    import scripts.two_minute_forecast_test as runner

    monkeypatch.setattr(
        runner,
        "fetch_experiment_spot_price",
        lambda symbol, provider: (_ for _ in ()).throw(RuntimeError("ticker unavailable")),
    )
    monkeypatch.setattr(
        runner,
        "fetch_experiment_candles",
        lambda symbol, limit: ([{"time": 0.0, "close": 100.0}], "test-provider"),
    )
    with pytest.raises(RuntimeError, match="beyond the"):
        runner.fetch_actual_price_with_fallback(
            "BTCUSDT", "binance", 300.0, tolerance_seconds=30.0
        )


def test_fallback_never_uses_a_candle_that_closed_after_the_target(monkeypatch):
    import scripts.two_minute_forecast_test as runner

    monkeypatch.setattr(
        runner,
        "fetch_experiment_spot_price",
        lambda symbol, provider: (_ for _ in ()).throw(RuntimeError("ticker unavailable")),
    )
    # Candle starts at 120s and closes at 180s, after the 170s target.
    monkeypatch.setattr(
        runner,
        "fetch_experiment_candles",
        lambda symbol, limit: ([{"time": 120.0, "close": 100.0}], "test-provider"),
    )
    with pytest.raises(RuntimeError, match="before the target"):
        runner.fetch_actual_price_with_fallback("BTCUSDT", "binance", 170.0)


def test_experiment_reports_the_provider_that_actually_served_the_candles(monkeypatch):
    """The app falls back to Coinbase, so the runner must not assume Binance."""
    import scripts.two_minute_forecast_test as runner
    from app import market

    rows = [{"time": 1_700_000_000.0 + i * 60, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1}
            for i in range(40)]

    def coinbase_backed(symbol, interval, limit):
        market._PROVIDER_USED[(symbol.upper(), interval)] = "coinbase:BTC-USD"
        return rows

    monkeypatch.setattr(runner, "candles", coinbase_backed)
    fetched, provider = runner.fetch_experiment_candles("BTCUSDT", 40)

    assert fetched == rows
    assert provider == "coinbase:BTC-USD"
