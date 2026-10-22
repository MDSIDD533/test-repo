"""Tests for the live verification script's pure validators.

These run without network access. They prove the verifier itself would catch a
bad candle series, a misaligned WebSocket kline or a wrong-interval message —
so a PASS from it means something.
"""
import pytest

from scripts import live_verification as verify


def series(start, interval, count):
    size = verify.INTERVAL_SECONDS[interval]
    return [float(start + index * size) for index in range(count)]


def test_exact_hourly_series_passes_spacing_and_alignment():
    result = verify.candle_spacing(series(1_700_000_000 - 1_700_000_000 % 3600, "1h", 50), "1h")

    assert result["spacing_ok"] is True
    assert result["aligned_ok"] is True
    assert result["deltas"] == [3600]
    assert result["expected_seconds"] == 3600


def test_five_minute_data_in_an_hourly_series_is_rejected():
    hourly_start = 1_700_000_000 - 1_700_000_000 % 3600
    times = series(hourly_start, "5m", 20)

    result = verify.candle_spacing(times, "1h")

    assert result["spacing_ok"] is False
    assert 300 in result["deltas"]


def test_misaligned_timestamps_are_reported_even_when_spacing_is_uniform():
    times = series(1_700_000_030, "1h", 10)  # 30s offset from the hour

    result = verify.candle_spacing(times, "1h")

    assert result["spacing_ok"] is True
    assert result["aligned_ok"] is False
    assert result["misaligned_count"] == 10


def test_a_single_candle_cannot_be_called_uniform():
    result = verify.candle_spacing([1_700_000_000.0], "1h")

    assert result["spacing_ok"] is False
    assert "need at least two candles" in result["reason"]


def test_unknown_interval_is_rejected():
    with pytest.raises(ValueError, match="Unknown interval"):
        verify.candle_spacing([0.0, 3600.0], "7m")


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"open": 1, "high": 2, "low": 0.5, "close": 1.5}, []),
        ({"open": 1, "high": 0.9, "low": 0.5, "close": 1.5}, ["high is below open/close"]),
        ({"open": 1, "high": 2, "low": 1.2, "close": 1.5}, ["low is above open/close"]),
        ({"open": 1, "high": 0.4, "low": 0.5, "close": 1.5}, ["high is below open/close", "low exceeds high"]),
        ({"open": 1, "high": 2, "low": 0, "close": 1.5}, ["non-positive price"]),
        ({"open": 1, "high": "x", "low": 0.5, "close": 1.5}, ["missing or non-numeric OHLC fields"]),
    ],
)
def test_ohlc_validation_catches_inconsistent_candles(row, expected):
    assert verify.validate_ohlc(row) == expected


def kline_message(**overrides):
    payload = {
        "e": "kline",
        "k": {
            "t": 1_700_000_040_000,
            "s": "BTCUSDT",
            "i": "1m",
            "o": "100.0",
            "h": "101.0",
            "l": "99.0",
            "c": "100.5",
            "x": False,
        },
    }
    payload["k"].update(overrides)
    return payload


def test_a_well_formed_kline_message_passes():
    assert verify.validate_ws_kline(kline_message(), "BTCUSDT", "1m") == []


def test_a_message_for_the_wrong_symbol_is_rejected():
    problems = verify.validate_ws_kline(kline_message(s="ETHUSDT"), "BTCUSDT", "1m")

    assert any("does not match BTCUSDT" in problem for problem in problems)


def test_a_message_for_the_wrong_interval_is_rejected():
    problems = verify.validate_ws_kline(kline_message(i="5m"), "BTCUSDT", "1m")

    assert any("does not match 1m" in problem for problem in problems)


def test_a_misaligned_kline_start_is_rejected():
    problems = verify.validate_ws_kline(kline_message(t=1_700_000_030_000), "BTCUSDT", "1m")

    assert any("not aligned" in problem for problem in problems)


def test_a_non_kline_event_is_rejected():
    problems = verify.validate_ws_kline({"e": "trade", "k": kline_message()["k"]}, "BTCUSDT", "1m")

    assert any("unexpected event type" in problem for problem in problems)


def test_a_missing_payload_is_rejected():
    assert verify.validate_ws_kline({"e": "kline"}, "BTCUSDT", "1m") == ["missing kline payload"]


def test_news_failure_is_reported_but_does_not_gate_the_run():
    # Research unavailability already forces NO TRADE, so it must be visible
    # in the table without turning the whole verification red.
    result = verify.run_check(
        "research",
        lambda: (_ for _ in ()).throw(RuntimeError("feed down")),
        critical=False,
    )
    summary = verify.summarize([result, {"name": "binance", "status": "pass", "critical": True}])

    assert result["status"] == "fail"
    assert summary["failed"] == 1
    assert summary["ok"] is True


def test_summary_gates_only_on_critical_checks():
    checks = [
        {"name": "a", "status": "pass", "critical": True},
        {"name": "b", "status": "fail", "critical": False},
        {"name": "c", "status": "skip", "critical": False},
    ]

    summary = verify.summarize(checks)

    assert summary["ok"] is True
    assert summary["critical_failed"] == []
    assert (summary["passed"], summary["failed"], summary["skipped"]) == (1, 1, 1)


def test_summary_fails_when_a_critical_check_fails():
    summary = verify.summarize([{"name": "kline", "status": "fail", "critical": True}])

    assert summary["ok"] is False
    assert summary["critical_failed"] == ["kline"]


def test_run_check_records_the_exception_instead_of_raising():
    result = verify.run_check("boom", lambda: (_ for _ in ()).throw(RuntimeError("provider down")))

    assert result["status"] == "fail"
    assert "RuntimeError: provider down" in result["error"]
    assert result["duration_seconds"] >= 0


def test_run_check_records_pass_details():
    result = verify.run_check("fine", lambda: {"candles": 3})

    assert result["status"] == "pass"
    assert result["detail"] == {"candles": 3}
