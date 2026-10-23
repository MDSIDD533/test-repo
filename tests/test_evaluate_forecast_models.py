import json

import pytest

from scripts.evaluate_forecast_models import evaluate_models, load_trials


def write_reports(tmp_path, returns):
    for index, actual_return in enumerate(returns, start=1):
        start = 100.0
        report = {
            "forecast": {
                "momentum_3m_pct": actual_return * 0.5,
                "technical_signal": "BUY" if actual_return > 0 else "SELL" if actual_return < 0 else "WAIT",
                "news_sentiment_score": 0,
            },
            "comparison": {
                "baseline_price": start,
                "actual_price": start * (1 + actual_return / 100),
            },
        }
        (tmp_path / f"trial-{index:03d}.json").write_text(json.dumps(report), encoding="utf-8")


def test_load_trials_ignores_malformed_and_orders_by_trial_number(tmp_path):
    write_reports(tmp_path, [0.02, -0.01, 0.03])
    (tmp_path / "trial-004.json").write_text("{broken", encoding="utf-8")

    trials = load_trials(tmp_path)

    assert len(trials) == 3
    assert [trial["trial"] for trial in trials] == ["trial-001", "trial-002", "trial-003"]


def test_evaluation_uses_chronological_holdout_and_compares_baseline(tmp_path):
    write_reports(tmp_path, [0.02, 0.03, 0.01, 0.04, 0.02, -0.02, -0.03, -0.01, -0.04, -0.02])

    result = evaluate_models(tmp_path, train_fraction=0.7)

    assert result["valid_trials"] == 10
    assert result["training_trials"] == 7
    assert result["holdout_trials"] == 3
    assert result["split_policy"].startswith("chronological")
    assert result["holdout_candidate_metrics"]["count"] == 3
    assert result["holdout_zero_return_baseline_metrics"]["count"] == 3
    assert isinstance(result["candidate_beats_baseline_on_holdout"], bool)
    assert "do not deploy" in result["warning"].lower() or "offline" in result["warning"].lower()


def test_evaluation_rejects_too_few_trials(tmp_path):
    write_reports(tmp_path, [0.01, -0.01, 0.02, -0.02, 0.01, -0.01])

    with pytest.raises(ValueError, match="at least 7 valid trials"):
        evaluate_models(tmp_path)


def test_evaluation_rejects_invalid_split_fraction(tmp_path):
    write_reports(tmp_path, [0.01, -0.01, 0.02, -0.02, 0.01, -0.01, 0.03])

    with pytest.raises(ValueError, match="train_fraction"):
        evaluate_models(tmp_path, train_fraction=1.0)


def test_evaluation_reports_confusion_matrix_precision_recall_and_error_metrics(tmp_path):
    write_reports(tmp_path, [2.0, -1.0, 0.0])
    trials = load_trials(tmp_path)

    from scripts.evaluate_forecast_models import evaluate
    result = evaluate(trials, None)

    assert result["count"] == 3
    assert result["confusion_matrix"]["FLAT"] == {"UP": 1, "DOWN": 1, "FLAT": 1}
    assert result["per_direction"]["FLAT"]["precision"] == 0.3333
    assert result["per_direction"]["FLAT"]["recall"] == 1.0
    assert result["per_direction"]["UP"]["precision"] is None
    assert result["direction_accuracy_pct"] == 33.33
    assert result["mean_absolute_error_pct"] == 1.0
    assert result["root_mean_squared_error_pct"] == 1.290994
    assert result["mean_absolute_percentage_error_pct"] == round(
        ((2 / 102) + (1 / 99) + 0) / 3 * 100, 6
    )
    assert result["within_tolerance_count"] == 1
    assert result["within_tolerance_rate_pct"] == 33.33
    assert result["brier_score"] is None
    assert result["brier_sample_count"] == 0


def test_brier_score_is_reported_only_with_valid_probabilities(tmp_path):
    for index, (probability, actual_return) in enumerate(
        [(0.7, 1.0), (0.3, -1.0), (0.6, 0.2)], start=1
    ):
        start = 100.0
        (tmp_path / f"trial-{index:03d}.json").write_text(json.dumps({
            "forecast": {
                "momentum_3m_pct": 0.0,
                "technical_signal": "WAIT",
                "news_sentiment_score": 0,
                "probability_up": probability,
            },
            "comparison": {
                "baseline_price": start,
                "actual_price": start * (1 + actual_return / 100),
            },
        }), encoding="utf-8")

    from scripts.evaluate_forecast_models import evaluate
    result = evaluate(load_trials(tmp_path), (0.0, 0.0, 0.0))

    assert result["brier_sample_count"] == 3
    assert result["brier_score"] == round(((0.7 - 1) ** 2 + (0.3 - 0) ** 2 + (0.6 - 1) ** 2) / 3, 6)


def test_evaluation_reports_coverage_and_invalid_trial_files(tmp_path):
    write_reports(tmp_path, [0.02, -0.01, 0.03, 0.01, -0.02, 0.02, 0.0, -0.03])
    (tmp_path / "trial-009.json").write_text("{broken", encoding="utf-8")

    from scripts.evaluate_forecast_models import evaluate_models
    result = evaluate_models(tmp_path, train_fraction=0.7)

    assert result["discovered_trial_files"] == 9
    assert result["valid_trials"] == 8
    assert result["invalid_trial_count"] == 1
    assert result["coverage_pct"] == 88.89
    assert result["invalid_trial_files"][0]["file"] == "trial-009.json"
    assert result["deployment_decision"] in {"DO_NOT_DEPLOY", "CANDIDATE_MERITS_NEW_EVALUATION"}
    assert result["metrics_definitions"]["tolerance_pct"] == 0.05
    assert "holdout_baselines" in result


def test_original_frozen_forecast_is_evaluated_separately(tmp_path):
    (tmp_path / "trial-001.json").write_text(json.dumps({
        "forecast": {
            "momentum_3m_pct": 0.0,
            "technical_signal": "WAIT",
            "news_sentiment_score": 0,
            "forecast_price": 100.02,
            "direction": "UP",
        },
        "comparison": {
            "baseline_price": 100.0,
            "actual_price": 100.03,
            "almost_same": True,
        },
    }), encoding="utf-8")

    from scripts.evaluate_forecast_models import evaluate_original_forecast, load_trials
    result = evaluate_original_forecast(load_trials(tmp_path))

    assert result["count"] == 1
    assert result["direction_accuracy_pct"] == 100.0
    assert result["within_tolerance_count"] == 1
    assert result["reported_almost_same_count"] == 1
    assert result["mean_absolute_error_pct"] == 0.01


def test_candidate_that_ties_the_baseline_is_not_deployed(tmp_path):
    write_reports(tmp_path, [0.0] * 10)

    from scripts.evaluate_forecast_models import evaluate_models
    result = evaluate_models(tmp_path, train_fraction=0.7)

    assert result["relative_improvement_vs_zero_return_pct"] is None
    assert result["candidate_beats_baseline_on_holdout"] is False
    assert result["deployment_decision"] == "DO_NOT_DEPLOY"
    assert "do not replace" in result["deployment_recommendation"]
