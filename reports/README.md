# Reports

## `forecast-model-evaluation-2026-10-21.json`

Recorded evaluation of workflow run `39610582675` (100 live two-minute BTC trials).

Key conclusions, preserved without modification:

- Original forecast direction accuracy: 44%.
- Original strict tolerance passes: 37/100.
- Original mean absolute price error: 0.063793% of starting price.
- Zero-return baseline mean absolute price error: 0.048120%.
- Chronological holdout candidate mean absolute price error: 0.027016%.
- Holdout zero-return baseline mean absolute price error: 0.024944%.
- Decision: `DO_NOT_DEPLOY`.

The individual `trial-NNN.json` artifacts from that workflow run are **not stored in this repository**. Therefore this file is the historical record, and the enhanced evaluator (`scripts/evaluate_forecast_models.py`) has **not** been re-run on the original artifacts.

To reproduce the enhanced evaluation, obtain the artifacts from the completed workflow run:

```bash
gh run download 39610582675 -R MDSIDD533/test-repo -D forecast-batch-reports
python scripts/evaluate_forecast_models.py forecast-batch-reports --output forecast-model-evaluation-recomputed.json
```

The enhanced evaluator adds MAE, MAPE, RMSE, direction precision/recall/F1, a 3×3 confusion matrix, coverage, invalid-trial reporting, observation-timing offsets, Brier score when probabilities exist, and zero-return/momentum/technical baselines. Holdout outcomes are never used to select weights.

## Reproducibility rules

- Trial files are read in `trial-NNN` filename order.
- The earliest 70% of valid trials are training data; the latest 30% are holdout.
- Candidate weights are selected on training MAE only.
- Deployment requires at least a 5% relative MAE improvement over the zero-return holdout baseline.
- A candidate that does not clear that predeclared margin is reported as `DO_NOT_DEPLOY`.
