# Multi-Agent Crypto Decision Pipeline

## Goal

Turn the current rule-based spot-analysis starter into a staged, auditable research and simulation system. The system produces a ranked **decision report**, not a promise of profit. It must be allowed to return **NO TRADE** when data is stale, evidence conflicts, liquidity is poor, or simulated risk is unacceptable.

## Implementation status (2026-10-22)

The code now follows the stage order in the diagram below. A stage that cannot run is recorded with an explicit status; expected unavailability is not treated as a software success.

| Stage | Implementation | Known limitation |
|---|---|---|
| Market Data | Validates injected candle payloads: fields, finite values, OHLC, ordering, gaps, freshness | Live provider availability is not verified in the sandbox |
| Market Analysis | Rule-based EMA/RSI/MACD/ATR/structure and levels from validated candles | Heuristic, not a calibrated probability |
| Research | CoinDesk RSS provider with source URL, timezone and 72h freshness checks; `ResearchUnavailable` becomes an `unavailable` stage | Live RSS could not be reached in the sandbox |
| Prediction | Direction only, one-candle horizon, frozen creation time, reference price, features and model version; no point price or probability is fabricated. A separate 120-second preview endpoint returns an explicitly unrecorded point forecast. | Not calibrated; no persistent pipeline forecast ledger |
| Risk & Decision | Reward/risk, ATR, configured limits, explicit authorization checks; fails closed | Always NO TRADE until a calibrated model is independently validated |
| TimeTraveler | Reproducible historical-return bootstrap; labelled `simulation_only` and `not_an_observation` | Scenario frequencies are not win probabilities; costs not modeled |
| Report | Combines validation, analysis, research, prediction, risk metrics, simulation summary, blockers and errors | Does not persist a durable report |
| Outcome Tracker | Two-minute experiment runner records forecasts and outcomes as workflow artifacts | No database or in-app outcome ledger |

The confidence policy is unchanged: no probability is displayed as calibrated unless it was trained and validated out of sample. The recorded 100-trial experiment concluded **DO_NOT_DEPLOY**; see `reports/forecast-model-evaluation-2026-10-21.json` and `docs/project_status.md`.

## Pipeline

```text
Scheduler / User Request
        |
        v
1. Market Data Agent
        | validated candles, ticker, volume, timestamps
        v
2. Market Analysis Agent
        | trend, structure, momentum, volatility, support/resistance
        v
3. Research Agent
        | timestamped news/events, project/exchange notices, source quality
        v
4. Prediction Agent
        | direction + defined horizon; calibrated probabilities only after
        | out-of-sample validation; uncertainty + evidence
        v
5. Risk & Decision Agent
        | expected value after costs, liquidity, SL/TP, position sizing, NO TRADE gate
        v
6. TimeTraveler Simulation Agent
        | configurable 100 / 500 / 1,000+ scenarios, walk-forward checks,
        | bootstrap/Monte Carlo paths, adverse cases, drawdown and sensitivity
        v
7. Coordinator / Report Agent
        | ranked coin, time window, action, entry zone, invalidation, targets,
        | probability estimates, evidence, risks, freshness, simulation summary
        v
8. Outcome Tracker
        | record forecast, later outcome, calibration, drift and agent health
        +-------------------------------------> improve validation data
```

## Agent contracts

Every stage returns structured data with: `run_id`, `symbol`, `as_of`, `data_freshness`, `status`, `result`, `evidence`, `warnings`, and `errors`. Stages must not silently invent missing information. Research findings must include source URL, published time, retrieval time, and a short factual summary. If a source cannot be verified, label it unverified.

- **Market Data Agent:** only allowlisted spot markets; validate candle order, missing bars, duplicates, zero/negative prices and stale timestamps. Respect API limits.
- **Market Analysis Agent:** calculate trend, momentum, structure, volatility, levels, volume/liquidity and multi-timeframe agreement. Keep descriptive facts separate from interpretation.
- **Research Agent:** gather relevant, timestamped market/project/exchange news when a reliable source connector is available. No news is a valid result; do not fabricate a narrative.
- **Prediction Agent:** predict a specific event and horizon (for example, probability TP1 is reached before SL within 4 hours). Probabilities must be calibrated from out-of-sample historical forecasts, not mapped from a hand-written score. Return uncertainty and sample size.
- **Risk & Decision Agent:** calculate expected value after estimated fees/slippage, reward/risk, liquidity and exposure. Reject trades with weak evidence, insufficient data, excessive risk or conflicting agents. Spot-only policy means SELL is an exit/avoid recommendation, not a short position.
- **TimeTraveler Simulation Agent:** run a user-selected number of deterministic/reproducible simulations (initial presets: 100, 500, 1,000; allow larger only within compute limits). Use historical/walk-forward data, block bootstrap or Monte Carlo assumptions documented in each report. Include fees/slippage assumptions, adverse scenarios, max drawdown and distribution of outcomes. Simulations are stress tests, not independent proof that a forecast is correct.
- **Coordinator / Report Agent:** compare eligible coins and return a ranked shortlist, best observed time window, action or NO TRADE, entry zone, stop/invalidation, TP1-TP3, horizon, estimated probabilities, expected value, evidence, data timestamps and reasons. Never force a trade just to fill a list.
- **Outcome Tracker:** store forecasts before outcomes are known, compare them at their defined horizon, report Brier score/calibration, win rate, expectancy, drawdown and drift. Do not tune on the same holdout period used to report performance.

## Required final report

For each candidate:
1. Symbol and spot-only market.
2. Suggested action: BUY / HOLD / EXIT / NO TRADE.
3. Valid-until time and forecast horizon, in UTC.
4. Entry zone, stop/invalidation, TP1/TP2/TP3 and risk per trade.
5. Separate calibrated estimates such as P(TP1 before SL), P(SL before TP1), and P(neither within horizon), with sample size and validation period.
6. Estimated fees/slippage, expected value and risk/reward.
7. Market, research and multi-timeframe evidence, with links and timestamps.
8. TimeTraveler simulation count, assumptions, median outcome, downside quantiles, probability of loss and max drawdown.
9. Clear reasons to abstain, stale-data warnings and model version.

## Confidence and validation policy

- A displayed "90% certainty" is not an allowed default or goal by itself. The system may display 90% only if a sufficiently large, relevant, out-of-sample validation set supports that probability and calibration checks pass.
- Show **probability estimate**, **validation sample size**, **forecast horizon** and **uncertainty** separately. If evidence is insufficient, display "NOT CALIBRATED" and do not imply 90% certainty.
- Measure results using time-ordered train/validation/test splits and walk-forward evaluation. Avoid look-ahead leakage and overlapping-label leakage. Include delisted/failed pairs where data is available to reduce survivorship bias.
- Backtests must model fees, spread/slippage, candle gaps and conservative handling when SL and TP can both be touched in one candle. Use out-of-sample periods; simulations do not replace backtesting.
- No real orders: paper-only mode remains enforced until explicitly designed, tested and reviewed as a separate safety-critical project.

## Implementation phases

1. **Pipeline foundation:** schemas, orchestration, stage status, report format, deterministic fake-data tests.
2. **Reliable market data:** freshness/quality validation, rate limiting, caching and retry policy.
3. **Market analyst:** structured indicators and multi-timeframe evidence.
4. **Research connector:** reliable sources and provenance; gracefully handle unavailable news.
5. **Prediction evaluation:** define forecast events/horizons, historical dataset and walk-forward calibration before exposing probabilities.
6. **TimeTraveler:** configurable simulations, reproducible seeds, realistic costs, risk metrics and benchmark comparisons.
7. **Coordinator dashboard:** ranked candidates, reasoned NO TRADE, evidence drill-down and update timestamps.
8. **Outcome tracking:** persist forecasts and outcomes, monitor calibration/drift and scheduled reports.
9. **Paper-only end-to-end validation:** run unattended in paper mode and review results before considering any further automation.

## Initial engineering acceptance criteria

- Every agent can fail independently; one unavailable source does not crash the full run.
- No stale or invalid data reaches prediction/simulation without an explicit warning or rejection.
- Same inputs and seed reproduce the same simulation results.
- Simulation count is configurable and echoed in the report.
- A forecast is saved before its outcome; no future candle is used to create an earlier prediction.
- Tests cover each agent contract, failure path, probability calibration guard, simulation reproducibility, and report schema.
- GitHub Actions passes before the pipeline phase is marked complete.
