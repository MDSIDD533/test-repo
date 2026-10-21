import pytest

from app.short_horizon_forecast import compare_forecast, make_two_minute_forecast, news_sentiment


def sample_candles():
    closes = [100.0, 100.1, 100.2, 100.3, 100.4]
    return [{"close": value} for value in closes]


def test_news_sentiment_is_transparent_and_bounded():
    score, counts = news_sentiment([
        {"title": "Bitcoin rally and adoption"},
        {"title": "Market faces lawsuit"},
    ])
    assert score == 0  # two positive cues, two negative cues
    assert counts["positive_cues"] == 2
    assert counts["negative_cues"] == 2


def test_forecast_includes_two_minute_horizon_and_does_not_claim_calibration():
    forecast = make_two_minute_forecast(
        sample_candles(), "BTCUSDT", "BUY",
        [{"title": "Bitcoin adoption and rally"}],
    )
    assert forecast["horizon_seconds"] == 120
    assert forecast["baseline_price"] > 0
    assert forecast["forecast_price"] > 0
    assert -0.2 <= forecast["expected_return_pct"] <= 0.2
    assert forecast["calibrated"] is False


def test_comparison_reports_direction_and_tolerance():
    forecast = {
        "baseline_price": 100.0,
        "forecast_price": 100.02,
        "expected_return_pct": 0.02,
        "direction": "UP",
    }
    result = compare_forecast(forecast, 100.03)
    assert result["direction_match"] is True
    assert result["almost_same"] is True
    assert result["actual_direction"] == "UP"


def test_comparison_flags_direction_mismatch():
    forecast = {
        "baseline_price": 100.0,
        "forecast_price": 100.1,
        "expected_return_pct": 0.1,
        "direction": "UP",
    }
    result = compare_forecast(forecast, 99.9)
    assert result["direction_match"] is False
    assert result["almost_same"] is False


def test_forecast_rejects_insufficient_or_invalid_candles():
    with pytest.raises(ValueError):
        make_two_minute_forecast([{"close": 1.0}] * 4, "BTCUSDT", "WAIT", [])
    invalid = sample_candles()
    invalid[-1]["close"] = 0
    with pytest.raises(ValueError):
        make_two_minute_forecast(invalid, "BTCUSDT", "WAIT", [])
