"""Summarize a live two-minute forecast batch into honest accuracy figures.

The headline number is the three-way direction match rate (UP / DOWN / FLAT),
reported next to the baselines it must beat and with a Wilson confidence
interval so a small sample cannot look decisive.

Usage:
    python scripts/summarize_trial_batch.py forecast-batch-reports --output summary.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.short_horizon_forecast import DIRECTION_THRESHOLD_PCT
from scripts.evaluate_forecast_models import load_trial_dataset

HORIZON_SECONDS = 120.0
DIRECTIONS = ("UP", "DOWN", "FLAT")


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval; honest for small samples and extreme rates."""
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _direction(return_pct: float) -> str:
    if return_pct > DIRECTION_THRESHOLD_PCT:
        return "UP"
    if return_pct < -DIRECTION_THRESHOLD_PCT:
        return "DOWN"
    return "FLAT"


def _start_time(trial: dict[str, Any]) -> datetime | None:
    text = trial.get("forecast_created_at")
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _independent_subset(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Greedily keep trials whose 120s windows do not overlap the previous keep."""
    kept: list[dict[str, Any]] = []
    last_start: datetime | None = None
    for trial in trials:
        start = _start_time(trial)
        if start is None:
            continue
        if last_start is None or (start - last_start).total_seconds() >= HORIZON_SECONDS:
            kept.append(trial)
            last_start = start
    return kept


def summarize_trials(trials: list[dict[str, Any]]) -> dict[str, Any]:
    usable = []
    for trial in trials:
        start = float(trial["start"])
        actual = float(trial["actual"])
        if start <= 0 or actual <= 0:
            continue
        actual_return = (actual / start - 1.0) * 100.0
        usable.append(
            {
                **trial,
                "actual_direction": _direction(actual_return),
                "actual_return_pct": actual_return,
                "error_pct": abs(float(trial["forecast_price"]) - actual) / start * 100.0
                if trial.get("forecast_price") is not None
                else None,
            }
        )

    total = len(usable)
    confusion = {predicted: {actual: 0 for actual in DIRECTIONS} for predicted in DIRECTIONS}
    correct = 0
    strict = 0
    flat_actual = 0
    direction_counts = {direction: 0 for direction in DIRECTIONS}
    for trial in usable:
        predicted = trial.get("forecast_direction") or "FLAT"
        actual = trial["actual_direction"]
        if predicted not in DIRECTIONS:
            predicted = "FLAT"
        confusion[predicted][actual] += 1
        direction_counts[actual] += 1
        if predicted == actual:
            correct += 1
        if trial.get("almost_same_reported") is True:
            strict += 1
        if actual == "FLAT":
            flat_actual += 1

    per_direction = {}
    for direction in DIRECTIONS:
        true_positive = confusion[direction][direction]
        predicted_count = sum(confusion[direction].values())
        actual_count = direction_counts[direction]
        precision = true_positive / predicted_count if predicted_count else None
        recall = true_positive / actual_count if actual_count else None
        per_direction[direction] = {
            "predicted": predicted_count,
            "actual": actual_count,
            "correct": true_positive,
            "precision_pct": _round(precision * 100, 2) if precision is not None else None,
            "recall_pct": _round(recall * 100, 2) if recall is not None else None,
        }

    errors = [trial["error_pct"] for trial in usable if trial["error_pct"] is not None]
    absolute_returns = [abs(trial["actual_return_pct"]) for trial in usable]
    accuracy = correct / total if total else None
    interval = wilson_interval(correct, total)
    majority = max(direction_counts.values()) / total if total else None

    independent = _independent_subset(usable)
    independent_correct = sum(
        1 for trial in independent
        if (trial.get("forecast_direction") or "FLAT") == trial["actual_direction"]
    )

    sources: dict[str, int] = {}
    for trial in usable:
        source = str(trial.get("observation_source") or "unknown")
        sources[source] = sources.get(source, 0) + 1

    return {
        "valid_trials": total,
        "direction_correct": correct,
        "direction_accuracy_pct": _round(accuracy * 100, 2) if accuracy is not None else None,
        "direction_accuracy_ci95_pct": (
            [_round(interval[0] * 100, 2), _round(interval[1] * 100, 2)] if interval else None
        ),
        "strict_tolerance_pass_count": strict,
        "strict_tolerance_pass_pct": _round(strict / total * 100, 2) if total else None,
        "confusion_matrix": confusion,
        "per_direction": per_direction,
        "mean_absolute_error_pct_of_start": _round(statistics.mean(errors), 6) if errors else None,
        "mean_absolute_percentage_error_pct": _round(statistics.mean(errors), 6) if errors else None,
        "root_mean_squared_error_pct": (
            _round(math.sqrt(statistics.mean([value ** 2 for value in errors])), 6) if errors else None
        ),
        "mean_absolute_actual_return_pct": (
            _round(statistics.mean(absolute_returns), 6) if absolute_returns else None
        ),
        "baselines": {
            "always_flat_direction_accuracy_pct": (
                _round(flat_actual / total * 100, 2) if total else None
            ),
            "majority_class_direction_accuracy_pct": (
                _round(majority * 100, 2) if majority is not None else None
            ),
            "zero_return_mae_pct": _round(statistics.mean(absolute_returns), 6)
            if absolute_returns else None,
        },
        "observation_sources": sources,
        "independence": {
            "window_seconds": HORIZON_SECONDS,
            "non_overlapping_trials": len(independent),
            "non_overlapping_direction_accuracy_pct": (
                _round(independent_correct / len(independent) * 100, 2) if independent else None
            ),
            "note": (
                "Trials whose 120-second windows overlap are not independent observations. "
                "The non-overlapping figure is the conservative read."
            ),
        },
        "warning": (
            "Mechanics and empirical accuracy only. No fees, no execution, no claim of tradable skill."
        ),
    }


def summarize_directory(directory: str | Path) -> dict[str, Any]:
    dataset = load_trial_dataset(directory)
    summary = summarize_trials(dataset["trials"])
    summary.update(
        {
            "report_directory": str(directory),
            "discovered_trial_files": dataset["discovered_files"],
            "valid_trials_from_files": len(dataset["trials"]),
            "invalid_trial_count": len(dataset["invalid_files"]),
            "invalid_trial_files": dataset["invalid_files"],
            "coverage_pct": (
                round(100 * len(dataset["trials"]) / dataset["discovered_files"], 2)
                if dataset["discovered_files"] else None
            ),
        }
    )
    return summary


def render(summary: dict[str, Any]) -> str:
    accuracy = summary.get("direction_accuracy_pct")
    interval = summary.get("direction_accuracy_ci95_pct")
    lines = [
        f"Valid trials: {summary['valid_trials']} of "
        f"{summary.get('discovered_trial_files', summary['valid_trials'])} files "
        f"(coverage {summary.get('coverage_pct')}%, "
        f"invalid {summary.get('invalid_trial_count', 0)})",
        f"Direction accuracy: {accuracy}%"
        + (f" (95% CI {interval[0]}–{interval[1]}%)" if interval else ""),
        f"Strict tolerance pass: {summary.get('strict_tolerance_pass_pct')}% "
        f"({summary['strict_tolerance_pass_count']})",
        f"Always-FLAT baseline: "
        f"{summary['baselines'].get('always_flat_direction_accuracy_pct')}% · "
        f"majority-class baseline: {summary['baselines'].get('majority_class_direction_accuracy_pct')}%",
        f"MAE: {summary.get('mean_absolute_error_pct_of_start')}% of start · "
        f"RMSE: {summary.get('root_mean_squared_error_pct')}% · "
        f"zero-return MAE: {summary['baselines'].get('zero_return_mae_pct')}%",
        f"Non-overlapping trials: {summary['independence']['non_overlapping_trials']} · "
        f"accuracy {summary['independence']['non_overlapping_direction_accuracy_pct']}%",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="forecast-batch-reports")
    parser.add_argument("--output", default="forecast-batch-summary.json")
    arguments = parser.parse_args(argv)

    summary = summarize_directory(arguments.directory)
    text = json.dumps(summary, indent=2, sort_keys=True)
    print(render(summary))
    print()
    print(text)
    Path(arguments.output).write_text(text, encoding="utf-8")
    return 0 if summary["valid_trials"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
