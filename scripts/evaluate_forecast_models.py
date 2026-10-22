"""Chronological train/holdout evaluation for short-horizon forecast variants.

This tool evaluates stored trial reports only. It never edits the live forecast
model and never tunes on the holdout split.

Usage: python scripts/evaluate_forecast_models.py forecast-batch-reports
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any


DIRECTION_THRESHOLD_PCT = 0.005
RETURN_CAP_PCT = 0.20
TOLERANCE_PCT = 0.05
MIN_RELATIVE_IMPROVEMENT_PCT = 5.0
DIRECTION_LABELS = ("UP", "DOWN", "FLAT")

METRICS_DEFINITIONS = {
    "mean_absolute_error_pct": (
        "mean of |predicted_price - actual_price| / starting_price * 100"
    ),
    "mean_absolute_percentage_error_pct": (
        "mean of |predicted_price - actual_price| / actual_price * 100"
    ),
    "root_mean_squared_error_pct": (
        "sqrt(mean(((predicted_price - actual_price) / starting_price * 100)^2))"
    ),
    "direction_threshold_pct": DIRECTION_THRESHOLD_PCT,
    "direction_rule": (
        "UP if return > threshold, DOWN if return < -threshold, otherwise FLAT"
    ),
    "within_tolerance_rule": (
        "direction matches AND absolute price error <= 0.05% of the starting price"
    ),
    "tolerance_pct": TOLERANCE_PCT,
    "brier_score": (
        "mean((P(UP) - 1[actual return > direction threshold])^2); null when no valid probability exists"
    ),
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_trial_dataset(directory: str | Path) -> dict[str, Any]:
    """Load stored trial reports and count files that could not be used.

    Chronology is derived from the zero-padded ``trial-NNN`` filenames produced
    by the batch runner. Loading never inspects a later trial to build an
    earlier trial's features.
    """
    root = Path(directory)
    discovered = sorted(root.glob("trial-*.json"))
    trials: list[dict[str, Any]] = []
    invalid_files: list[dict[str, str]] = []
    for path in discovered:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            forecast = report["forecast"]
            comparison = report["comparison"]
            start = float(comparison["baseline_price"])
            actual = float(comparison["actual_price"])
            momentum = float(forecast["momentum_3m_pct"])
            technical = str(forecast.get("technical_signal", "WAIT")).upper()
            sentiment = float(forecast.get("news_sentiment_score", 0))
            values = (start, actual, momentum, sentiment)
            if not all(math.isfinite(value) for value in values) or start <= 0 or actual <= 0:
                raise ValueError("non-finite or non-positive price/feature")
            if technical not in {"BUY", "SELL", "WAIT", "HOLD", "FLAT"}:
                technical = "WAIT"
            forecast_price = _number(forecast.get("forecast_price"))
            if forecast_price is None:
                expected = _number(forecast.get("expected_return_pct"))
                if expected is not None:
                    forecast_price = start * (1 + expected / 100.0)
            probability_up = _number(forecast.get("probability_up"))
            if probability_up is not None and not 0 <= probability_up <= 1:
                probability_up = None
            trials.append({
                "trial": path.stem,
                "forecast_created_at": forecast.get("forecast_created_at") or report.get("started_at"),
                "start": start,
                "actual": actual,
                "forecast_price": forecast_price,
                "forecast_direction": str(forecast.get("direction", "")).upper() or None,
                "probability_up": probability_up,
                "momentum": momentum,
                "technical_bias": 1 if technical == "BUY" else -1 if technical == "SELL" else 0,
                "sentiment": max(-1, min(1, sentiment)),
                "almost_same_reported": comparison.get("almost_same"),
                "market_data_provider": report.get("market_data_provider"),
                "observation_source": (report.get("actual_observation") or {}).get("source"),
                "observation_time_difference_seconds": _number(
                    (report.get("actual_observation") or {}).get("time_difference_seconds")
                ),
            })
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            invalid_files.append({"file": path.name, "reason": f"{type(exc).__name__}: {exc}"})
    return {
        "trials": trials,
        "discovered_files": len(discovered),
        "invalid_files": invalid_files,
    }


def load_trials(directory: str | Path) -> list[dict[str, Any]]:
    """Backward-compatible accessor returning only usable trials."""
    return load_trial_dataset(directory)["trials"]


def candidate_weights() -> list[tuple[float, float, float]]:
    """Small predeclared grid; candidate selection happens on training data only."""
    return [
        (momentum, technical, sentiment)
        for momentum in (0.0, 0.10, 0.20, 0.35)
        for technical in (0.0, 0.01, 0.025)
        for sentiment in (0.0, 0.005, 0.015)
    ]


def predict_return(trial: dict[str, Any], weights: tuple[float, float, float]) -> float:
    momentum_weight, technical_weight, sentiment_weight = weights
    raw = (
        momentum_weight * trial["momentum"]
        + technical_weight * trial["technical_bias"]
        + sentiment_weight * trial["sentiment"]
    )
    return max(-RETURN_CAP_PCT, min(RETURN_CAP_PCT, raw))


def direction_for_return(return_pct: float) -> str:
    if return_pct > DIRECTION_THRESHOLD_PCT:
        return "UP"
    if return_pct < -DIRECTION_THRESHOLD_PCT:
        return "DOWN"
    return "FLAT"


def classification_metrics(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    matrix = {predicted: {actual: 0 for actual in DIRECTION_LABELS} for predicted in DIRECTION_LABELS}
    for predicted, actual in pairs:
        matrix[predicted][actual] += 1
    total = len(pairs)
    correct = sum(matrix[label][label] for label in DIRECTION_LABELS)
    per_direction: dict[str, dict[str, Any]] = {}
    for label in DIRECTION_LABELS:
        predicted_count = sum(matrix[label].values())
        actual_count = sum(matrix[predicted][label] for predicted in DIRECTION_LABELS)
        true_positive = matrix[label][label]
        precision = true_positive / predicted_count if predicted_count else None
        recall = true_positive / actual_count if actual_count else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall > 0
            else None
        )
        per_direction[label] = {
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "support": actual_count,
            "predicted_count": predicted_count,
        }
    return {
        "confusion_matrix": matrix,
        "per_direction": per_direction,
        "accuracy": round(correct / total, 4) if total else None,
        "correct": correct,
        "total": total,
    }


def _summarize(
    starts: list[float],
    actuals: list[float],
    predicted_prices: list[float],
    predicted_directions: list[str],
    probabilities: list[float | None] | None = None,
) -> dict[str, Any]:
    count = len(actuals)
    if not count:
        return {
            "count": 0,
            "evaluated_trials": 0,
            "mean_absolute_error_pct": None,
            "median_absolute_error_pct": None,
            "mean_absolute_percentage_error_pct": None,
            "root_mean_squared_error_pct": None,
            "direction_accuracy_pct": None,
            "confusion_matrix": None,
            "per_direction": None,
            "within_tolerance_count": 0,
            "within_tolerance_rate_pct": None,
            "brier_score": None,
            "brier_sample_count": 0,
        }
    errors_of_start: list[float] = []
    errors_of_actual: list[float] = []
    squared_errors: list[float] = []
    pairs: list[tuple[str, str]] = []
    tolerance_passes = 0
    for start, actual, predicted_price, predicted_direction in zip(
        starts, actuals, predicted_prices, predicted_directions
    ):
        error_of_start = abs(predicted_price - actual) / start * 100
        errors_of_start.append(error_of_start)
        errors_of_actual.append(abs(predicted_price - actual) / actual * 100)
        squared_errors.append(((predicted_price - actual) / start * 100) ** 2)
        actual_direction = direction_for_return((actual / start - 1) * 100)
        pairs.append((predicted_direction, actual_direction))
        if predicted_direction == actual_direction and error_of_start <= TOLERANCE_PCT:
            tolerance_passes += 1

    classification = classification_metrics(pairs)
    brier_score = None
    brier_sample_count = 0
    if probabilities is not None:
        brier_values: list[float] = []
        for probability, actual, start in zip(probabilities, actuals, starts):
            if probability is None:
                continue
            actual_up = 1.0 if (actual / start - 1) * 100 > DIRECTION_THRESHOLD_PCT else 0.0
            brier_values.append((probability - actual_up) ** 2)
        if brier_values:
            brier_score = round(statistics.mean(brier_values), 6)
            brier_sample_count = len(brier_values)

    return {
        "count": count,
        "evaluated_trials": count,
        "mean_absolute_error_pct": round(statistics.mean(errors_of_start), 6),
        "median_absolute_error_pct": round(statistics.median(errors_of_start), 6),
        "mean_absolute_percentage_error_pct": round(statistics.mean(errors_of_actual), 6),
        "root_mean_squared_error_pct": round(math.sqrt(statistics.mean(squared_errors)), 6),
        "direction_accuracy_pct": round(classification["accuracy"] * 100, 2),
        "confusion_matrix": classification["confusion_matrix"],
        "per_direction": classification["per_direction"],
        "within_tolerance_count": tolerance_passes,
        "within_tolerance_rate_pct": round(100 * tolerance_passes / count, 2),
        "brier_score": brier_score,
        "brier_sample_count": brier_sample_count,
        "tolerance_pct": TOLERANCE_PCT,
    }


def evaluate(
    trials: list[dict[str, Any]],
    weights: tuple[float, float, float] | None,
) -> dict[str, Any]:
    """Evaluate a candidate (or the zero-return baseline when weights is None)."""
    if not trials:
        return _summarize([], [], [], [])
    starts: list[float] = []
    actuals: list[float] = []
    predicted_prices: list[float] = []
    predicted_directions: list[str] = []
    probabilities: list[float | None] = []
    for trial in trials:
        predicted_return = 0.0 if weights is None else predict_return(trial, weights)
        starts.append(trial["start"])
        actuals.append(trial["actual"])
        predicted_prices.append(trial["start"] * (1 + predicted_return / 100.0))
        predicted_directions.append(direction_for_return(predicted_return))
        probabilities.append(trial.get("probability_up") if weights is not None else None)
    return _summarize(starts, actuals, predicted_prices, predicted_directions, probabilities)


def evaluate_original_forecast(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate the frozen forecast that was stored before each outcome."""
    usable = [trial for trial in trials if trial.get("forecast_price") is not None]
    if not usable:
        return _summarize([], [], [], [])
    starts = [trial["start"] for trial in usable]
    actuals = [trial["actual"] for trial in usable]
    predicted_prices = [trial["forecast_price"] for trial in usable]
    predicted_directions = [
        trial.get("forecast_direction") or direction_for_return((trial["forecast_price"] / trial["start"] - 1) * 100)
        for trial in usable
    ]
    probabilities = [trial.get("probability_up") for trial in usable]
    summary = _summarize(starts, actuals, predicted_prices, predicted_directions, probabilities)
    summary["reported_almost_same_count"] = sum(
        trial.get("almost_same_reported") is True for trial in usable
    )
    return summary


def _baseline_payload(trials: list[dict[str, Any]]) -> dict[str, Any]:
    zero = evaluate(trials, None)
    momentum = evaluate(trials, (1.0, 0.0, 0.0))
    technical = evaluate(trials, (0.0, 0.01, 0.0))
    return {
        "zero_return": zero,
        "momentum_only": momentum,
        "technical_bias_only": technical,
        "always_flat_direction": {
            "count": len(trials),
            "direction_accuracy_pct": zero["direction_accuracy_pct"],
            "note": "Predicting FLAT for every trial has the same price error as zero-return; its direction accuracy is the share of FLAT outcomes.",
        },
    }


def evaluate_models(directory: str | Path, train_fraction: float = 0.70) -> dict[str, Any]:
    """Choose weights on the chronological training split and report holdout once."""
    if not 0.5 <= train_fraction < 1:
        raise ValueError("train_fraction must be >= 0.5 and < 1")
    dataset = load_trial_dataset(directory)
    trials = dataset["trials"]
    split = int(len(trials) * train_fraction)
    if split < 5 or len(trials) - split < 2:
        raise ValueError("Need at least 7 valid trials, with 5 training and 2 holdout trials")
    train, holdout = trials[:split], trials[split:]

    # Mean absolute price error is the primary selection metric. Simpler models win ties.
    candidates = candidate_weights()
    selected = min(
        candidates,
        key=lambda weights: (
            evaluate(train, weights)["mean_absolute_error_pct"],
            sum(abs(value) for value in weights),
        ),
    )
    selected_metrics = evaluate(holdout, selected)
    baselines = _baseline_payload(holdout)
    baseline_metrics = baselines["zero_return"]
    candidate_error = selected_metrics["mean_absolute_error_pct"]
    baseline_error = baseline_metrics["mean_absolute_error_pct"]
    relative_improvement = (
        (baseline_error - candidate_error) / baseline_error * 100
        if baseline_error not in (None, 0) and candidate_error is not None
        else None
    )
    beats_baseline = bool(
        candidate_error is not None
        and baseline_error not in (None, 0)
        and relative_improvement is not None
        and relative_improvement >= MIN_RELATIVE_IMPROVEMENT_PCT
    )
    decision = "CANDIDATE_MERITS_NEW_EVALUATION" if beats_baseline else "DO_NOT_DEPLOY"
    coverage = (
        round(100 * len(trials) / dataset["discovered_files"], 2)
        if dataset["discovered_files"]
        else None
    )
    observation_differences = [
        float(trial["observation_time_difference_seconds"])
        for trial in trials
        if trial.get("observation_time_difference_seconds") is not None
    ]

    return {
        "report_directory": str(directory),
        "valid_trials": len(trials),
        "discovered_trial_files": dataset["discovered_files"],
        "invalid_trial_files": dataset["invalid_files"],
        "invalid_trial_count": len(dataset["invalid_files"]),
        "coverage_pct": coverage,
        "observation_timing": {
            "trials_with_recorded_offset": len(observation_differences),
            "median_time_difference_seconds": (
                round(statistics.median(observation_differences), 3)
                if observation_differences else None
            ),
            "max_time_difference_seconds": (
                round(max(observation_differences), 3)
                if observation_differences else None
            ),
        },
        "training_trials": len(train),
        "holdout_trials": len(holdout),
        "split_policy": "chronological; earliest trials train, latest trials holdout",
        "selection_metric": "training mean absolute price error percentage",
        "selection_note": "Candidate selected on training data only; holdout is reported once and never used for tuning.",
        "minimum_relative_improvement_pct": MIN_RELATIVE_IMPROVEMENT_PCT,
        "selected_candidate_weights": {
            "momentum": selected[0],
            "technical_bias": selected[1],
            "news_sentiment": selected[2],
        },
        "training_metrics": evaluate(train, selected),
        "holdout_candidate_metrics": selected_metrics,
        "holdout_zero_return_baseline_metrics": baseline_metrics,
        "holdout_baselines": baselines,
        "original_forecast_holdout_metrics": evaluate_original_forecast(holdout),
        "relative_improvement_vs_zero_return_pct": (
            round(relative_improvement, 4) if relative_improvement is not None else None
        ),
        "candidate_beats_baseline_on_holdout": beats_baseline,
        "deployment_decision": decision,
        "deployment_recommendation": (
            "candidate merits a new independent live evaluation; do not deploy from this holdout alone"
            if beats_baseline else
            "do not replace the current model; candidate did not beat the zero-return baseline by the predeclared margin on holdout"
        ),
        "metrics_definitions": METRICS_DEFINITIONS,
        "bias_note": (
            "Trials are temporally dependent. A candidate that only beats the baseline by a "
            "small margin is not evidence of exploitable skill after fees and slippage."
        ),
        "warning": "Exploratory offline evaluation only; trials may be temporally dependent and are not proof of predictive skill.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="forecast-batch-reports")
    parser.add_argument("--train-fraction", type=float, default=0.70)
    parser.add_argument("--output", default="forecast-model-evaluation.json")
    args = parser.parse_args()
    result = evaluate_models(args.directory, args.train_fraction)
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
