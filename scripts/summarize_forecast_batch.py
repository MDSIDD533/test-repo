"""Summarize saved two-minute forecast trial reports without changing the model.

Usage: python scripts/summarize_forecast_batch.py forecast-batch-reports
The summary distinguishes data/runner failures from completed market comparisons.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any


def summarize_reports(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    json_files = sorted(root.glob("trial-*.json"))
    exit_files = sorted(root.glob("trial-*.exitcode"))
    trials: list[dict[str, Any]] = []
    malformed_reports = 0
    for path in json_files:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("report is not a JSON object")
        except (OSError, json.JSONDecodeError, ValueError):
            malformed_reports += 1
            continue
        comparison = report.get("comparison")
        trials.append({
            "file": path.name,
            "completed_comparison": isinstance(comparison, dict),
            "provider": report.get("market_data_provider"),
            "provider_at_finish": report.get("market_data_provider_at_finish"),
            "direction_match": comparison.get("direction_match") if isinstance(comparison, dict) else None,
            "almost_same": comparison.get("almost_same") if isinstance(comparison, dict) else None,
            "absolute_price_error_pct": comparison.get("absolute_price_error_pct_of_start") if isinstance(comparison, dict) else None,
            "error": report.get("error"),
        })

    completed = [trial for trial in trials if trial["completed_comparison"]]
    direction_results = [trial["direction_match"] for trial in completed if isinstance(trial["direction_match"], bool)]
    passes = sum(trial["almost_same"] is True for trial in completed)
    errors = [float(trial["absolute_price_error_pct"]) for trial in completed
              if isinstance(trial["absolute_price_error_pct"], (int, float))]
    baseline_errors: list[float] = []
    model_beats_baseline = 0
    model_ties_baseline = 0
    baseline_direction_matches: list[bool] = []
    for trial in completed:
        try:
            report = json.loads((root / trial["file"]).read_text(encoding="utf-8"))
            comparison = report.get("comparison", {})
            start = float(comparison["baseline_price"])
            actual = float(comparison["actual_price"])
            predicted = float(comparison["forecast_price"])
            if not all(math.isfinite(value) and value > 0 for value in (start, actual, predicted)):
                continue
            baseline_error = abs(actual - start) / start * 100.0
            model_error = abs(predicted - actual) / start * 100.0
            baseline_errors.append(baseline_error)
            model_beats_baseline += model_error < baseline_error
            model_ties_baseline += math.isclose(model_error, baseline_error, rel_tol=0.0, abs_tol=1e-12)
            actual_return = (actual / start - 1.0) * 100.0
            actual_direction = "UP" if actual_return > 0.005 else "DOWN" if actual_return < -0.005 else "FLAT"
            baseline_direction_matches.append(actual_direction == "FLAT")
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            continue
    exit_codes: list[int] = []
    for path in exit_files:
        try:
            exit_codes.append(int(path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            continue

    return {
        "report_directory": str(root),
        "report_json_files_found": len(json_files),
        "valid_report_files": len(trials),
        "malformed_report_files": malformed_reports,
        "exit_code_files_found": len(exit_files),
        "exit_code_zero_count": sum(code == 0 for code in exit_codes),
        "exit_code_nonzero_count": sum(code != 0 for code in exit_codes),
        "completed_market_comparisons": len(completed),
        "direction_matches": sum(value is True for value in direction_results),
        "direction_accuracy_pct": round(100 * sum(value is True for value in direction_results) / len(direction_results), 2) if direction_results else None,
        "almost_same_passes": passes,
        "almost_same_pass_rate_pct": round(100 * passes / len(completed), 2) if completed else None,
        "mean_absolute_price_error_pct": round(statistics.mean(errors), 6) if errors else None,
        "median_absolute_price_error_pct": round(statistics.median(errors), 6) if errors else None,
        "zero_return_baseline_mean_absolute_error_pct": round(statistics.mean(baseline_errors), 6) if baseline_errors else None,
        "model_beats_zero_return_baseline_count": model_beats_baseline,
        "model_ties_zero_return_baseline_count": model_ties_baseline,
        "model_vs_baseline_comparisons": len(baseline_errors),
        "zero_return_baseline_direction_accuracy_pct": round(100 * sum(baseline_direction_matches) / len(baseline_direction_matches), 2) if baseline_direction_matches else None,
        "failed_or_incomplete_trials": [trial["file"] for trial in trials if not trial["completed_comparison"]],
        "errors": [{"file": trial["file"], "error": trial["error"]} for trial in trials if trial["error"]],
        "interpretation": "Operational summary only. A pass is not proof of predictive skill; compare against a zero-return baseline and evaluate on unseen trials.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="forecast-batch-reports")
    parser.add_argument("--output", default="forecast-batch-summary.json")
    args = parser.parse_args()
    summary = summarize_reports(args.directory)
    output = Path(args.output)
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["completed_market_comparisons"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
