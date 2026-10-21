import pytest

from app.market_data_agent import (
    CandleValidationError,
    make_market_data_agent,
    validate_candles,
)


def sample_candles(count=40, now=100_000.0, interval_seconds=300):
    start = now - interval_seconds * count
    rows = []
    for index in range(count):
        price = 100.0 + index
        rows.append({
            "time": start + index * interval_seconds,
            "open": price,
            "high": price + 2.0,
            "low": price - 2.0,
            "close": price + 1.0,
            "volume": 12.0,
        })
    return rows


def test_valid_candles_pass_with_audit_checks():
    rows = sample_candles()
    result = validate_candles(rows, "5m", now=100_000.0)
    assert result["valid"] is True
    assert result["candle_count"] == 40
    assert "freshness" in result["checks"]
    assert result["age_seconds"] == 300.0


@pytest.mark.parametrize("mutation, message", [
    (lambda rows: rows[0].pop("close"), "missing fields"),
    (lambda rows: rows[4].update(close=float("nan")), "non-finite"),
    (lambda rows: rows[4].update(open=0), "non-positive"),
    (lambda rows: rows[4].update(high=1), "inconsistent high"),
    (lambda rows: rows[4].update(volume=-1), "negative volume"),
    (lambda rows: rows[4].update(time=rows[3]["time"]), "strictly increasing"),
    (lambda rows: rows[4].update(time=rows[3]["time"] + 900), "Gap detected"),
])
def test_invalid_candle_data_is_rejected(mutation, message):
    rows = sample_candles()
    mutation(rows)
    with pytest.raises(CandleValidationError, match=message):
        validate_candles(rows, "5m", now=100_000.0)


def test_stale_candles_are_rejected():
    rows = sample_candles(now=100_000.0)
    with pytest.raises(CandleValidationError, match="stale"):
        validate_candles(rows, "5m", now=200_000.0)


def test_future_candles_are_rejected():
    rows = sample_candles(now=100_000.0)
    rows[-1]["time"] = 100_600.0
    with pytest.raises(CandleValidationError, match="future"):
        validate_candles(rows, "5m", now=100_000.0)


def test_too_short_history_is_rejected():
    with pytest.raises(CandleValidationError, match="At least 35"):
        validate_candles(sample_candles(20), "5m", now=100_000.0)


def test_agent_returns_validated_market_data_and_evidence():
    rows = sample_candles()
    agent = make_market_data_agent(
        lambda symbol, interval, limit: rows,
        interval="5m",
        limit=40,
        clock=lambda: 100_000.0,
    )
    result = agent({"symbol": "btcusdt"})
    assert result["symbol"] == "BTCUSDT"
    assert result["validation"]["valid"] is True
    assert result["evidence"][0]["candle_count"] == 40
    assert len(result["candles"]) == 40


def test_agent_rejects_blank_symbol_before_fetching():
    called = []
    agent = make_market_data_agent(lambda *args: called.append(args) or [])
    with pytest.raises(ValueError, match="symbol"):
        agent({"symbol": "  "})
    assert called == []
