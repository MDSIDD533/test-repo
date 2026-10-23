"""Tests for the batch summarizer: the numbers must be reproducible by hand."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts import summarize_trial_batch as summary_module
from scripts.summarize_trial_batch import summarize_trials, wilson_interval

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def trial(index, start, actual, direction, offset_seconds=None, source="spot_ticker"):
    offset = offset_seconds if offset_seconds is not None else index * 60
    forecast_return = 0.1 if direction == "UP" else -0.1 if direction == "DOWN" else 0.0
    actual_return = (actual / start - 1) * 100
    return {
        "trial": f"trial-{index:03d}",
        "forecast_created_at": (BASE + timedelta(seconds=offset)).isoformat(),
        "start": start,
        "actual": actual,
        "forecast_price": start * (1 + forecast_return / 100),
        "forecast_direction": direction,
        "probability_up": None,
        "momentum": 0.0,
        "technical_bias": 0,
        "sentiment": 0.0,
        "almost_same_reported": (
            direction == ("UP" if actual_return > 0.005 else "DOWN" if actual_return < -0.005 else "FLAT")
            and abs(actual_return - forecast_return) <= 0.05
        ),
        "market_data_provider": "coinbase:BTC-USD",
        "observation_source": source,
        "observation_time_difference_seconds": 0.0,
    }


def test_direction_accuracy_is_counted_against_the_documented_thresholds():
    trials = [
        trial(1, 100.0, 100.5, "UP"),        # correct
        trial(2, 100.0, 100.5, "DOWN"),      # wrong
        trial(3, 100.0, 100.0, "FLAT"),      # correct (no move)
        trial(4, 100.0, 100.004, "UP"),      # 0.004% is FLAT, so wrong
    ]

    summary = summarize_trials(trials)

    assert summary["valid_trials"] == 4
    assert summary["direction_correct"] == 2
    assert summary["direction_accuracy_pct"] == 50.0
    assert summary["confusion_matrix"]["UP"]["UP"] == 1
    assert summary["confusion_matrix"]["UP"]["FLAT"] == 1
    assert summary["confusion_matrix"]["DOWN"]["UP"] == 1
    assert summary["confusion_matrix"]["FLAT"]["FLAT"] == 1
    assert summary["per_direction"]["UP"]["precision_pct"] == 50.0
    # two trials actually moved up (one predicted DOWN), so recall is 50%
    assert summary["per_direction"]["UP"]["recall_pct"] == 50.0


def test_it_reports_baselines_on_the_same_sample():
    trials = [
        trial(1, 100.0, 100.0, "FLAT"),
        trial(2, 100.0, 100.0, "UP"),
        trial(3, 100.0, 100.0, "DOWN"),
    ]

    summary = summarize_trials(trials)

    # always-FLAT would be right on all three, so a 2/3 model does not beat it
    assert summary["baselines"]["always_flat_direction_accuracy_pct"] == 100.0
    assert summary["direction_accuracy_pct"] == 33.33


def test_overlapping_windows_are_separated_out():
    # starts every 60s, so only every second trial is a non-overlapping window
    trials = [trial(i, 100.0, 100.5, "UP") for i in range(6)]

    summary = summarize_trials(trials)

    assert summary["independence"]["window_seconds"] == 120
    assert summary["independence"]["non_overlapping_trials"] == 3
    assert summary["independence"]["non_overlapping_direction_accuracy_pct"] == 100.0


def test_wilson_interval_is_sane_at_the_extremes():
    assert wilson_interval(0, 0) is None
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0 < high < 0.35
    low, high = wilson_interval(10, 10)
    assert high == 1.0 and 0.65 < low < 1.0
    low, high = wilson_interval(44, 100)
    assert 0.34 < low < 0.44 < high < 0.55


def test_empty_input_reports_nulls_not_zero_accuracy():
    summary = summarize_trials([])

    assert summary["valid_trials"] == 0
    assert summary["direction_accuracy_pct"] is None
    assert summary["direction_accuracy_ci95_pct"] is None
    assert summary["mean_absolute_error_pct_of_start"] is None


def test_errors_are_measured_from_real_prices():
    trials = [trial(1, 100.0, 101.0, "UP")]  # forecasts 100.1, actual 101.0

    summary = summarize_trials(trials)

    assert summary["mean_absolute_error_pct_of_start"] == pytest.approx(0.9, abs=1e-6)
    assert summary["root_mean_squared_error_pct"] == pytest.approx(0.9, abs=1e-6)


def test_directory_summary_counts_invalid_files(tmp_path):
    good = trial(1, 100.0, 100.5, "UP")
    report = {
        "forecast": {
            "forecast_created_at": good["forecast_created_at"],
            "momentum_3m_pct": 0.0,
            "technical_signal": "WAIT",
            "news_sentiment_score": 0,
            "forecast_price": 100.1,
            "direction": "UP",
            "horizon_seconds": 120,
        },
        "comparison": {
            "baseline_price": 100.0,
            "actual_price": 100.5,
            "almost_same": True,
        },
        "market_data_provider": "coinbase:BTC-USD",
        "actual_observation": {"source": "spot_ticker", "time_difference_seconds": 0.0},
    }
    (tmp_path / "trial-001.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "trial-002.json").write_text("{not json", encoding="utf-8")

    from scripts.summarize_trial_batch import summarize_directory

    summary = summarize_directory(tmp_path)

    assert summary["valid_trials"] == 1
    assert summary["discovered_trial_files"] == 2
    assert summary["invalid_trial_count"] == 1
    assert summary["coverage_pct"] == 50.0
    assert summary["direction_accuracy_pct"] == 100.0


def test_render_mentions_the_caveats():
    text = summary_module.render(summarize_trials([trial(1, 100.0, 100.5, "UP")]))

    assert "Direction accuracy: 100.0%" in text
    assert "Always-FLAT baseline" in text
    assert "Non-overlapping trials" in text
