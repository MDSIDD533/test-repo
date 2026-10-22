"""Chronological train/holdout evaluation for short-horizon forecast variants.

This tool evaluates stored trial reports only. It never edits the live forecast model.
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


def load_trials(directory: str | Path) -> list[dict[str, Any]]:
    """Load complete trial reports in chronological trial-number order."""
    root = Path(directory)
    trials: list[dict[str, Any]] = []
    for path in sorted(root.glob("trial-*.json")):
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
                continue
            if technical not in {"BUY", "SELL", "WAIT", "HOLD", "FLAT"}:
                technical = "WAIT"
            trials.append({
                "trial": path.stem,
                "start": start,
                "actual": actual,
                "momentum": momentum,
                "technical_bias": 1 if technical == "BUY" else -1 if technical == "SELL" else 0,
                "sentiment": max(-1, min(1, sentiment)),
            })
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return trials


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


def evaluate(trials: list[dict[str, Any]], weights: tuple[float, float, float] | None) -> dict[str, Any]:
    if not trials:
        return {"count": 0, "mean_absolute_error_pct": None, "direction_accuracy_pct": None}
    errors: list[float] = []
    direction_matches = 0
    for trial in trials:
        predicted_return = 0.0 if weights is None else predict_return(trial, weights)
        predicted_price = trial["start"] * (1 + predicted_return / 100.0)
        actual_return = (trial["actual"] / trial["start"] - 1) * 100
        predicted_direction = (
            "UP" if predicted_return > DIRECTION_THRESHOLD_PCT
            else "DOWN" if predicted_return < -DIRECTION_THRESHOLD_PCT
            else "FLAT"
        )
        actual_direction = (
            "UP" if actual_return > DIRECTION_THRESHOLD_PCT
            else "DOWN" if actual_return < -DIRECTION_THRESHOLD_PCT
            else "FLAT"
        )
        direction_matches += predicted_direction == actual_direction
        errors.append(abs(predicted_price - trial["actual"]) / trial["start"] * 100)
    return {
        "count": len(trials),
        "mean_absolute_error_pct": round(statistics.mean(errors), 6),
        "direction_accuracy_pct": round(100 * direction_matches / len(trials), 2),
    }


def evaluate_models(directory: str | Path, train_fraction: float = 0.70) -> dict[str, Any]:
    """Choose weights on the chronological training split and report holdout only once."""
    if not 0.5 <= train_fraction < 1:
        raise ValueError("train_fraction must be >= 0.5 and < 1")
    trials = load_trials(directory)
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
    baseline_metrics = evaluate(holdout, None)
    beats_baseline = (
        selected_metrics["mean_absolute_error_pct"] is not None
        and baseline_metrics["mean_absolute_error_pct"] is not None
        and selected_metrics["mean_absolute_error_pct"] < baseline_metrics["mean_absolute_error_pct"]
    )
    return {
        "report_directory": str(directory),
        "valid_trials": len(trials),
        "training_trials": len(train),
        "holdout_trials": len(holdout),
        "split_policy": "chronological; earliest trials train, latest trials holdout",
        "selection_metric": "training mean absolute price error percentage",
        "selected_candidate_weights": {
            "momentum": selected[0],
            "technical_bias": selected[1],
            "news_sentiment": selected[2],
        },
        "training_metrics": evaluate(train, selected),
        "holdout_candidate_metrics": selected_metrics,
        "holdout_zero_return_baseline_metrics": baseline_metrics,
        "candidate_beats_baseline_on_holdout": beats_baseline,
        "deployment_recommendation": (
            "candidate merits a new independent live evaluation; do not deploy from this holdout alone"
            if beats_baseline else
            "do not replace the current model; candidate did not beat the zero-return baseline on holdout"
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
