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
