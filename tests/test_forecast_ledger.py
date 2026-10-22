import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import forecast_ledger
from app.forecast_ledger import LedgerError, read_ledger, record_forecast, record_resolution


def prediction(**overrides):
    payload = {
        "status": "uncalibrated_direction_only",
        "symbol": "BTCUSDT",
        "interval": "5m",
        "forecast_created_at": "2026-01-01T00:00:00+00:00",
        "horizon_seconds": 300,
        "horizon_candles": 1,
        "reference_price": 100.0,
        "direction": "UP",
        "calibrated": False,
        "probability_tp_before_sl": None,
        "predicted_price": None,
        "predicted_return_pct": None,
        "model_version": "rule_direction_v1",
        "features": {"rsi": 55.0},
        "limitations": ["direction only"],
        "warnings": ["not calibrated"],
    }
    payload.update(overrides)
    return payload


def test_forecast_is_frozen_with_a_target_time_and_stays_pending():
    entry = record_forecast(prediction(), run_id="run-1", symbol="BTCUSDT")

    assert entry["event"] == "forecast"
    assert entry["target_time"] == "2026-01-01T00:05:00+00:00"
    assert entry["horizon_seconds"] == 300
    assert entry["reference_price"] == 100.0
    ledger = read_ledger()
    assert ledger["summary"]["total"] == 1
    assert ledger["summary"]["pending"] == 1
    assert ledger["summary"]["resolved"] == 0
    assert ledger["forecasts"][0]["status"] == "pending"


def test_ledger_file_is_append_only_and_never_rewrites_the_forecast(tmp_path):
    first = record_forecast(prediction(), run_id="run-1", symbol="BTCUSDT")
    original_line = forecast_ledger.ledger_path().read_text(encoding="utf-8")
    record_resolution(first["forecast_id"], observed_price=101.0)

    lines = forecast_ledger.ledger_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] + "\n" == original_line
    assert json.loads(lines[0])["event"] == "forecast"
    assert json.loads(lines[1])["event"] == "resolution"


def test_ledger_lives_outside_the_repository_by_default(monkeypatch):
    monkeypatch.delenv("FORECAST_LEDGER_PATH", raising=False)
    path = forecast_ledger.ledger_path()
    assert path.parts[-3:] == ("test-repo", "runtime", "forecast_ledger.jsonl") or (
        path.parts[-2:] == ("runtime", "forecast_ledger.jsonl")
    )


def test_resolution_scores_direction_against_the_frozen_reference_price():
    up = record_forecast(prediction(direction="UP"), run_id="run-up", symbol="BTCUSDT")
    down = record_forecast(prediction(direction="DOWN"), run_id="run-down", symbol="BTCUSDT")
    flat = record_forecast(prediction(direction="FLAT"), run_id="run-flat", symbol="BTCUSDT")

    up_resolution = record_resolution(up["forecast_id"], observed_price=101.0)
    down_resolution = record_resolution(down["forecast_id"], observed_price=101.0)
    flat_resolution = record_resolution(flat["forecast_id"], observed_price=100.01)

    assert up_resolution["observed_return_pct"] == pytest.approx(1.0)
    assert up_resolution["direction_correct"] is True
    assert down_resolution["direction_correct"] is False
    assert flat_resolution["direction_correct"] is True
    # A direction-only forecast has no point price, so no price error is invented.
    assert up_resolution["predicted_price"] is None
    assert up_resolution["absolute_price_error_pct"] is None


def test_resolution_uses_the_direction_threshold_for_flat_forecasts():
    flat = record_forecast(prediction(direction="FLAT"), run_id="run-flat", symbol="BTCUSDT")

    borderline = record_resolution(flat["forecast_id"], observed_price=100.04)
    moved = record_forecast(prediction(direction="FLAT"), run_id="run-flat-2", symbol="BTCUSDT")
    moved_resolution = record_resolution(moved["forecast_id"], observed_price=100.5)

    assert borderline["direction_correct"] is True
    assert moved_resolution["direction_correct"] is False


def test_summary_counts_direction_accuracy_from_resolved_forecasts_only():
    first = record_forecast(prediction(), run_id="a", symbol="BTCUSDT")
    second = record_forecast(prediction(direction="UP"), run_id="b", symbol="BTCUSDT")
    record_forecast(prediction(direction="DOWN"), run_id="c", symbol="BTCUSDT")
    record_resolution(first["forecast_id"], observed_price=101.0)
    record_resolution(second["forecast_id"], observed_price=99.0)

    summary = read_ledger()["summary"]

    assert summary["total"] == 3
    assert summary["resolved"] == 2
    assert summary["pending"] == 1
    assert summary["direction_correct"] == 1
    assert summary["direction_incorrect"] == 1
    assert summary["direction_accuracy_pct"] == 50.0
    assert summary["by_direction"] == {"DOWN": 1, "FLAT": 0, "UP": 2}


def test_a_forecast_cannot_be_resolved_twice_or_unknown():
    entry = record_forecast(prediction(), run_id="run-1", symbol="BTCUSDT")
    record_resolution(entry["forecast_id"], observed_price=101.0)

    with pytest.raises(LedgerError, match="already resolved"):
        record_resolution(entry["forecast_id"], observed_price=102.0)
    with pytest.raises(LedgerError, match="unknown forecast_id"):
        record_resolution("does-not-exist", observed_price=101.0)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"forecast_created_at": "not-a-time"}, "ISO-8601"),
        ({"reference_price": 0}, "positive"),
        ({"reference_price": "abc"}, "numeric"),
        ({"horizon_seconds": 0}, "positive"),
        ({"horizon_seconds": 12.5}, "whole number"),
        ({"direction": "SIDEWAYS"}, "direction must be"),
        ({"model_version": ""}, "model_version"),
        ({"status": "unavailable"}, "no forecast to freeze"),
    ],
)
def test_invalid_forecasts_are_rejected_without_writing_anything(overrides, message):
    with pytest.raises(LedgerError, match=message):
        record_forecast(prediction(**overrides), run_id="run-1", symbol="BTCUSDT")
    assert not forecast_ledger.ledger_path().exists()


def test_naive_timestamps_are_treated_as_utc():
    entry = record_forecast(
        prediction(forecast_created_at="2026-01-01T00:00:00"), run_id="run-1", symbol="BTCUSDT"
    )
    assert entry["forecast_created_at"] == "2026-01-01T00:00:00+00:00"
    assert entry["target_time"] == "2026-01-01T00:05:00+00:00"


def test_ledger_reports_corrupt_lines_instead_of_hiding_them():
    path = forecast_ledger.ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"event":"forecast","forecast_id":"kept","recorded_at":"2026-01-01T00:00:00+00:00"}\n'
        "this is not json\n"
        '{"event":"mystery"}\n'
        '{"event":"resolution","forecast_id":"orphan"}\n',
        encoding="utf-8",
    )

    ledger = read_ledger()

    assert ledger["summary"]["total"] == 1
    assert ledger["summary"]["corrupt_event_count"] == 3
    errors = " ".join(item["error"] for item in ledger["corrupt_lines"])
    assert "invalid JSON" in errors
    assert "unknown event type" in errors
    assert "unknown forecast" in errors


def test_resolution_records_the_observation_source_and_offset():
    entry = record_forecast(prediction(), run_id="run-1", symbol="BTCUSDT")
    resolution = record_resolution(
        entry["forecast_id"],
        observed_price=100.5,
        observed_at="2026-01-01T00:04:58+00:00",
        source="nearest_completed_1m_candle_close",
        time_difference_seconds=-2.0,
    )

    assert resolution["source"] == "nearest_completed_1m_candle_close"
    assert resolution["time_difference_seconds"] == 2.0
    assert resolution["observed_at"] == "2026-01-01T00:04:58+00:00"


def test_recording_is_durable_across_module_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("FORECAST_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    import importlib
    import sys

    entry = record_forecast(prediction(), run_id="run-1", symbol="BTCUSDT")
    original = sys.modules["app.forecast_ledger"]
    try:
        reloaded = importlib.reload(forecast_ledger)

        assert reloaded.ledger_path() == tmp_path / "ledger.jsonl"
        assert reloaded.read_ledger()["forecasts"][0]["forecast_id"] == entry["forecast_id"]
    finally:
        # A reload creates a second LedgerError class object. Restoring the
        # original module keeps exception identity stable for other tests.
        sys.modules["app.forecast_ledger"] = original


def test_ledger_path_parent_is_created_on_first_write(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "deeper" / "ledger.jsonl"
    monkeypatch.setenv("FORECAST_LEDGER_PATH", str(target))

    record_forecast(prediction(), run_id="run-1", symbol="BTCUSDT")

    assert target.exists()
    assert os.path.getsize(target) > 0


def test_target_time_is_derived_from_the_frozen_creation_time_not_from_now():
    entry = record_forecast(
        prediction(forecast_created_at="2020-05-05T12:00:00+00:00"), run_id="run-1", symbol="BTCUSDT"
    )
    expected = datetime(2020, 5, 5, 12, 5, tzinfo=timezone.utc)
    assert entry["target_time"] == expected.isoformat()
    assert entry["target_time"] != (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()


def resolved_ledger(specs):
    """specs: list of (direction, observed_price) with a 100.0 reference."""
    for index, (direction, price) in enumerate(specs):
        entry = record_forecast(
            prediction(direction=direction), run_id=f"run-{index}", symbol="BTCUSDT"
        )
        record_resolution(entry["forecast_id"], observed_price=price)
    return read_ledger()


def test_always_flat_baseline_uses_the_same_resolved_sample():
    # UP +1.0% (correct), DOWN +1.0% (wrong), FLAT +0.01% (correct)
    ledger = resolved_ledger([("UP", 101.0), ("DOWN", 101.0), ("FLAT", 100.01)])
    summary = ledger["summary"]

    assert summary["direction_correct"] == 2
    assert summary["direction_accuracy_pct"] == 66.67
    # Always-FLAT is correct only on the one forecast that barely moved.
    assert summary["always_flat_accuracy_pct"] == 33.33
    assert summary["edge_vs_always_flat_pct"] == 33.34
    assert summary["beats_always_flat"] is True


def test_a_model_can_lose_to_the_always_flat_baseline():
    # Every forecast made a confident call but the market barely moved.
    ledger = resolved_ledger([("UP", 100.01), ("DOWN", 100.01), ("UP", 100.0)])
    summary = ledger["summary"]

    assert summary["direction_correct"] == 0
    assert summary["always_flat_accuracy_pct"] == 100.0
    assert summary["beats_always_flat"] is False
    assert summary["edge_vs_always_flat_pct"] == -100.0


def test_baseline_metrics_are_null_before_anything_is_resolved():
    record_forecast(prediction(), run_id="pending-only", symbol="BTCUSDT")
    summary = read_ledger()["summary"]

    assert summary["resolved"] == 0
    assert summary["direction_accuracy_pct"] is None
    assert summary["always_flat_accuracy_pct"] is None
    assert summary["edge_vs_always_flat_pct"] is None
    assert summary["beats_always_flat"] is None


def test_per_direction_accuracy_is_reported_and_null_when_never_used():
    ledger = resolved_ledger([("UP", 101.0), ("UP", 99.0), ("DOWN", 99.0)])
    per_direction = ledger["summary"]["per_direction_accuracy"]

    assert per_direction["UP"]["forecasts"] == 2
    assert per_direction["UP"]["correct"] == 1
    assert per_direction["UP"]["accuracy_pct"] == 50.0
    assert per_direction["DOWN"]["accuracy_pct"] == 100.0
    assert per_direction["FLAT"]["forecasts"] == 0
    assert per_direction["FLAT"]["accuracy_pct"] is None


def test_the_summary_flags_a_small_sample():
    small = resolved_ledger([("UP", 101.0)])
    assert small["summary"]["sample_is_small"] is True


def test_a_large_sample_is_not_flagged_as_small():
    big = resolved_ledger([("UP", 101.0)] * 30)
    assert big["summary"]["resolved"] == 30
    assert big["summary"]["sample_is_small"] is False



def test_duplicate_reference_prices_are_counted_not_hidden():
    first = record_forecast(prediction(), run_id="a", symbol="BTCUSDT")
    second = record_forecast(prediction(), run_id="b", symbol="BTCUSDT")
    third = record_forecast(prediction(reference_price=101.0), run_id="c", symbol="BTCUSDT")

    for entry, price in ((first, 100.5), (second, 99.5), (third, 101.5)):
        record_resolution(entry["forecast_id"], observed_price=price)

    summary = read_ledger()["summary"]
    assert summary["resolved"] == 3
    assert summary["distinct_reference_prices"] == 2
    assert summary["duplicate_reference_rows"] == 1
    assert summary["sample_has_duplicates"] is True


def test_a_clean_sample_reports_no_duplicates():
    for index in range(3):
        entry = record_forecast(
            prediction(reference_price=100.0 + index), run_id=f"run-{index}", symbol="BTCUSDT"
        )
        record_resolution(entry["forecast_id"], observed_price=101.0 + index)

    summary = read_ledger()["summary"]
    assert summary["distinct_reference_prices"] == 3
    assert summary["duplicate_reference_rows"] == 0
    assert summary["sample_has_duplicates"] is False
