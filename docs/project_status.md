# Project Status — 2026-10-22

Branch: `arena/a852519f-test-repo` (branched from `main` at `d0101156acc5d23801a7ddc9d98bf084c90359d8`).

Overall status: **PARTIALLY COMPLETE**.

The checklist score is **95.7%** (89 / 93 items), but the system is not complete because several items that require live external evidence are explicitly **NOT VERIFIED**. A high percentage is not a substitute for missing browser and provider verification.

## 1. Inspection findings

- The local checkout was initially shallow (one visible commit). `git fetch --unshallow origin` recovered the real history and confirmed both referenced commits exist: `1209f207dca9ff775df8e67ddc061ad88dc00c27` and `d0101156acc5d23801a7ddc9d98bf084c90359d8`.
- The pre-existing chart (`1209f20`) added a Binance kline WebSocket but:
  - displayed `LIVE` after a successful REST load even when the stream was disconnected;
  - had no reconnection/backoff;
  - did not validate the kline's symbol, interval, timestamp alignment or OHLC relationships;
  - did not reject stale callbacks after a symbol/timeframe change (it compared only the socket identity, which the new code still improves with a generation token);
  - had no forming-vs-closed candle indicator;
  - could silently ignore a symbol/timeframe change while a request was in flight;
  - reset zoom on every manual refresh;
  - had only string-presence tests, not behavioural tests.
- The backend already forwarded the selected interval directly to Binance. No evidence was found that 1h candles were constructed from 5m data. What was missing was validation and evidence: there was no test that a 1h request produced exactly 3600-second candles, and no live-provider verification.
- The research stage treated a quiet news day as a hard failure. It now raises a distinct `ResearchUnavailable`, recorded as an `unavailable` stage instead of a software failure.
- The prediction stage returned only a rule-based direction and did not expose the required forecast metadata. It now exposes creation time, horizon, reference price, model version, features, freshness and limitations, while explicitly refusing to invent a point price or probability.
- The report stage did not combine actual stage evidence and could have reported an action that the coordinator would force to `NO TRADE`. It now derives `NO TRADE` whenever stages are incomplete, the prediction is uncalibrated, or the risk gate did not authorize.
- Paper trading had no fees, no slippage, no duplicate-position guard, no maximum-position guard and no fee-aware position sizing. These are now implemented and tested.
- The backtest excluded all costs. It now accepts `fee_bps` and `slippage_bps`, records gross/net return and applies the stop first when both levels are touched in one candle.
- The evaluator only reported mean absolute error and direction accuracy. It now reports the full metric set below.
- The stored 100-trial experiment results are preserved exactly; no new live trials were run in this sandbox.

The Testing category gained one item this batch (provider-layer tests), so the denominator is now 82 rather than 81. Earlier percentages in git history used 81 items and are not directly comparable.

## 1a. Why some checks cannot run here, and where they do run

**First live run (2026-10-22, run `39739425882`) found the real blocker:** Binance answers GitHub-hosted runners with **HTTP 451 Unavailable For Legal Reasons** — a geo-block, not a code defect. Every Binance check failed, `/api/candles` returned 502, and 5 of 7 browser tests could not load candles. The dashboard browser tests **passed (2/7)** against real Chromium.

Consequences, all reflected in the code:

- `app/market.py` now falls back to Coinbase BTC-USD for BTCUSDT on the intervals Coinbase serves (1m, 5m, 15m, 1h, 6h, 1d). Candle responses report the provider that actually served them; unsupported symbols/intervals fail rather than being fabricated.
- HTTP 451 is recorded by the verifier as a **skip with its reason**, never as a pass.
- The verifier now checks the Coinbase path against live data, which is the path that can be verified from a GitHub runner.



The development sandbox has no browser and cannot reach Binance or CoinDesk. Rather than leave those claims permanently unverified, they are automated in `.github/workflows/live-verification.yml`, which runs on GitHub-hosted runners where both are available:

- `scripts/live_verification.py` — live spot ticker, 1m/5m/1h/4h/1d kline uniformity and alignment, a real kline WebSocket message, the news feed, and every local endpoint (including that the pipeline froze a forecast and returned `NO TRADE`).
- `tests/browser/chart.spec.js` — real Chromium: candles render, the badge never says `LIVE` unless a socket opened, 1h/4h/1d candles are exactly 3600/14400/86400 seconds apart, timeframe switching updates every label, and a manual refresh preserves the zoomed viewport.
- `tests/browser/dashboard.spec.js` — no JavaScript errors, the rejected experiment is labelled, the ledger and paper panels disclose real state, and the agent pipeline shows real stage statuses.
- One real frozen two-minute trial via the existing runner.

The workflow posts its results as a pull-request comment. Until it has run against a commit, the corresponding items are scored 0.5 (implemented, not yet verified) rather than 1.

## 2. Checklist scoring method

Each item scores:

- **1** — implemented and covered by an automated test;
- **0.5** — implemented but only partially verifiable (for example, no browser/provider);
- **0** — not implemented or not verified.

The overall percentage is the sum of item scores divided by the number of items. Category percentages use the same method. This is a checklist measure, not an estimate of effort or time.

## 3. Chart

| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | WebSocket live kline stream | 1 | `static/chart-core.js`, Node tests |
| 2 | In-place forming-candle update | 1 | `upsertCandle`, `handleLiveCandle` |
| 3 | REST refresh every 15s as fallback | 1 | `AUTO_REFRESH_MS`, page test |
| 4 | Bounded exponential reconnection | 1 | `LiveStream._scheduleReconnect`, 3 Node tests |
| 5 | Close old stream on symbol/timeframe change | 1 | `stopStream`, `load`, Node switch test |
| 6 | Ignore stale callbacks from old sockets | 1 | generation token test |
| 7 | Validate kline fields/OHLC/interval | 1 | `normalizeKline`, 3 Node tests |
| 8 | Chronological order and duplicate prevention | 1 | `upsertCandle`, `buildCandleSeries` |
| 9 | Preserve zoom/pan during refresh | 0.5 | avoids `fitContent` except new selection; not browser-verified |
| 10 | Connection state, source and last update | 1 | `setConnection`, `setLastUpdate`, DOM IDs |
| 11 | Stale-feed indication | 1 | `STALE_AFTER_MS`, `checkStreamHealth` |
| 12 | Never show LIVE while disconnected | 1 | `rest: 'REST ONLY'`, `offline`, tests |
| 13 | All nine timeframes sent exactly to backend | 1 | `test_market_intervals` parametrized over 9 intervals |
| 14 | 1h candle width verified against a live provider | 1 | `live-verification` run 39743024896: 1h candles 3600s apart on live data (Coinbase; Binance is 451-blocked) |
| 15 | Title, symbol, timeframe and price update | 1 | `updateTitle`, DOM tests |
| 16 | Analysis refreshed on interval change | 1 | `/api/analyze?interval=` plus response check |
| 17 | Old markers/levels cleared on interval change | 1 | `load` clears series and state |
| 18 | Browser-level chart verification | 1 | Playwright run 39743024896: 7/7 passed — candles render, labels switch, zoom preserved |
| 19 | Live WebSocket updates verified in a browser | 0 | Binance WS returns HTTP 451 from GitHub runners; REST path verified instead |

**Chart: 17.5 / 19 = 92.1%**

Replay mode (added after the chart audit) is tracked separately because it is a data-availability feature, not a chart feature:

| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Recorded data served with its true source and timestamps | 1 | `fixtures/candles.json` from `coinbase:BTC-USD`; `/api/candles` returns `replay:BTCUSDT@5m` |
| 2 | Missing intervals resampled, never invented | 1 | `3m`/`30m`/`2h`/`4h` derived and labelled (`@1h->4h`), 12 tests |
| 3 | UI never claims to be live | 1 | REPLAY badge, no WebSocket, banner on chart and dashboard |
| 4 | Freshness rule relaxed only for a declared recording | 1 | `validate_candles(replay=True)` records `freshness_skipped`; live path still raises |
| 5 | Browser-proven rendering | 1 | `Replay browser check` run `39900034198`: **3 passed** in real Chromium |
| 6 | Live path unchanged after the change | 1 | live-verification run `39901160307`: providers PASS, live browser **7 passed** |

**Replay mode: 6 / 6 = 100%**

## 4. Agents

| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Market Data validates real candles/freshness | 1 | `test_market_data_agent` |
| 2 | Market Analysis computes indicators and evidence | 1 | RSI/MACD/ATR/structure fields, tests |
| 3 | Research uses real provider + provenance | 1 | CoinDesk RSS, URL/timezone checks |
| 4 | Research reports unavailability without inventing news | 1 | `ResearchUnavailable` + API test |
| 5 | Prediction has a defined target/horizon | 1 | one candle, interval seconds |
| 6 | Prediction supplies a point price or return | 0.5 | two-minute preview + experiment do; pipeline stage deliberately does not |
| 7 | Pipeline stores forecasts before outcomes | 1 | `app/forecast_ledger.py`; frozen before report stage; append-only tests |
| 8 | Prediction exposes model, features, freshness, limits | 1 | `prediction_agent` metadata |
| 9 | Risk computes reward/risk, ATR and limits | 1 | `test_decision_agents` |
| 10 | Risk fails closed on weak/unvalidated evidence | 1 | always NO TRADE without calibration/authorization |
| 11 | TimeTraveler clearly labelled simulation | 1 | `simulation_only`, `not_an_observation` |
| 12 | Report combines validation, analysis, research, prediction, risk, errors | 1 | `report_agent`, blockers tests |
| 13 | Independent stage failure/status handling | 1 | `test_agents` |
| 14 | Dashboard shows actual stage state | 1 | `runAgentAnalysis`, `UNAVAILABLE` label |
| 15 | End-to-end live-provider verification | 0 | Binance/CoinDesk unreachable here |

**Agents: 13.5 / 15 = 90.0%**

## 5. Prediction evaluation

| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | 100 recorded two-minute trials | 1 | workflow `39610582675`, stored report |
| 2 | Forecast frozen before outcome | 1 | runner writes forecast then sleeps 120s |
| 3 | Exact/nearest target-timestamp rule | 1 | spot ticker preferred across providers; nearest completed candle before the target used within 90s; offset and `candle_fallback_used` recorded |
| 4 | Mean absolute price error | 1 | evaluator + report |
| 5 | MAPE with defined denominator | 1 | `mean_absolute_percentage_error_pct` |
| 6 | RMSE | 1 | `root_mean_squared_error_pct` |
| 7 | Direction accuracy | 1 | evaluator + report |
| 8 | UP/DOWN precision and recall | 1 | `per_direction` |
| 9 | Confusion matrix | 1 | 3×3 matrix |
| 10 | Coverage and invalid-trial rate | 1 | `coverage_pct`, `invalid_trial_count` |
| 11 | Brier score when probabilities exist | 0.5 | implemented; original model emitted no probability |
| 12 | Zero-return/momentum/technical/flat baselines | 1 | `holdout_baselines` (offline evaluator) + always-FLAT comparison on the live ledger |
| 13 | Chronological split with no holdout tuning | 1 | 70/30 split; selection on train only |
| 14 | Enhanced evaluator re-run on original 100 artifacts | 0 | artifacts are not in the repository |
| 15 | New independent live trials | 1 | second live batch on 2026-10-22 (run `39761203609`): 150 trials, 149 valid, 44.3% direction accuracy. Sampled every 60 s, so only 49 are non-overlapping windows (38.8%); both figures are reported |
| 16 | Candidate not deployed after losing to baseline | 1 | `DO_NOT_DEPLOY`, dashboard banner |
| 17 | Results documented without hiding failure | 1 | README/status/report |
| 18 | Sample integrity: repeated start prices are counted, not hidden | 1 | `distinct_reference_prices`, `duplicate_reference_rows`, `sample_has_duplicates`; CI warns "3 of 8 resolved rows share a reference price" |

Recorded results, first batch (unchanged):

- Direction matches: 44/100 (44%).
- Strict predefined tolerance passes: 37/100 (37%).
- Original MAE: 0.063793% of starting price.
- Zero-return baseline MAE: 0.048120%.
- Holdout candidate MAE: 0.027016% versus baseline 0.024944%.
- Decision: `DO_NOT_DEPLOY`.

Second batch, 2026-10-22, run `39761203609` (150 trials, 3 shards, one trial per 60 s):

| metric | value |
|---|---|
| valid trials | 149 / 150 (99.33%) |
| direction accuracy | **44.3%** (66/149, 95% CI 36.6–52.3%) |
| strict tolerance pass | 58 / 149 (38.9%) |
| majority-class baseline | **49.66%** |
| always-FLAT baseline | 8.05% |
| MAE | 0.045871% of start |
| zero-return MAE | **0.036681%** (lower is better) |
| RMSE | 0.058723% |
| non-overlapping subset | 49 trials, 38.8% accuracy |
| observation source | spot ticker for 149 / 149 |
| invalid trial | 1 (`trial-038.json`, `KeyError: 'forecast'`) |

The second batch **replicated the first** (44.3% versus 44%) and failed the same way: below the 49.66% majority-class baseline, above the zero-return error, with a confidence interval that includes 50%. The chronological 70/30 re-evaluation again returned `DO_NOT_DEPLOY` (candidate relative improvement 0.0%). Reporting the majority baseline is important: a 44.3% headline sounds close to a coin flip, but the model is also *below the naive constant predictor*.

Evidence files: `docs/evidence/batch-150-summary.json`, `docs/evidence/batch-150-evaluation.json`.

**Prediction evaluation: 17 / 18 = 94.4%**

## 6. Paper trading

| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Balance and equity | 1 | `test_paper` |
| 2 | Open positions and closed trades | 1 | account/list tests |
| 3 | Realized and per-trade P&L | 1 | TP/SL tests |
| 4 | Configurable fees | 1 | entry/exit fee test |
| 5 | Configurable slippage | 1 | entry/exit fill adjustment |
| 6 | Risk-based position sizing | 1 | cash/risk budget test |
| 7 | Duplicate-position prevention | 1 | same-symbol rejection test |
| 8 | Maximum open positions / risk limits | 1 | max-trade rejection test |
| 9 | SL/TP simulation with conservative priority | 1 | paper + backtest both-levels test |
| 10 | Complete trade record | 1 | entry, exit, fees, reason, gross/net P&L |
| 11 | Costs included in backtest | 1 | fee/slippage comparison test |
| 12 | Persistence across restart | 1 | atomic JSON state; `test_paper_state` restart tests; cross-process server check |
| 13 | Live trading disabled | 1 | constant false, no order path |

**Paper trading: 13 / 13 = 100%**

## 7. Testing and CI

Verified locally with Python 3.11.2 and Node 22.22.3:

```text
python -m pytest -q
309 passed, 1 warning in 1.47s

node --test static/chart-core.test.js
# tests 19
# pass 19
# fail 0
```

The one warning is a Starlette test-client deprecation advising `httpx2`; the project already lists `httpx2>=1.0` and CI installs successfully.

GitHub Actions `tests` runs on this branch:

| Commit | Workflow run | Result |
|---|---|---|
| `c7ad9ff` | 39722221887 | success |
| `1fff9fb` | 39722386870 | success |
| `4d0a8dd` | 39722594591 | success |
| `e5c207f` | 39722964519 | success |
| `f0712a3` | 39723150937 | success |
| `44ff229` | 39723243323 | success |
| `172fcb7` | 39723354741 | success |
| `1da9e25` | 39723431429 | success |
| `dc7cba3` | 39723563170 | success |
| `47e416e` | 39723671534 | success |
| `b4c6427` | 39724822738 | success |
| `0726103` | 39734185577 | success |
| `9c4e4e0` | 39739425752 | success |
| `ee5d22b` | 39740927014 | success |
| `c5b0c62` | 39741273287 | success |
| `9c6f2cc` | 39741592115 | success |
| `13acd95` | 39742247533 | success |
| `acd02bd` | 39743024864 | success |
| `a8eb5c3` | 39753750793 | success |
| `3dfdfc3` | 39754817436 | success |
| `f1671b5` | 39756740709 | success |
| `369dc23` | 39757341320 | success |
| `4fdee89` | 39758488496 | success |
| `fdb8fbb` | 39759307738 | success |
| `9ff7db9` | 39759313605 | success |
| `806dff1` | 39781332462 | success |
| `26ffb64` | 39781580225 | success |
| `dd88ec9` | 39781713504 | success |
| PR #1 (`dd88ec9`) | 39781713504 | success |

The latest verified code commit is `dd88ec9`; the live-verification workflow also passed on it (run `39781707473`: providers 10 pass / 0 fail / 7 skip, browser 7/7 in 1.1 min), and the pull request check on the same commit is green. The 150-trial batch ran on `806dff1` (run `39761203609`, all jobs green). Later documentation-only commits do not change code or test counts and are re-verified by the same workflow, but the exact current status should be re-checked with `gh run list`.

Each run executes `node --check static/chart-core.js`, `node --test static/chart-core.test.js` and `python -m pytest -q` on Python 3.12 / Node 22.

The 100-trial live batch was accidentally triggered by a changed script path during this work. It was cancelled (run `39722964529`), and the workflow is now **manual-only** with an explicit `RUN-100-TRIALS` confirmation input. No trial results were produced by the cancelled run.

| # | Item | Score | Evidence |
|---|---|---|---|
| 1 | Backend unit tests | 1 | 309 passing |
| 2 | Chart logic tests | 1 | 19 passing |
| 3 | Mocked API tests | 1 | no live Binance dependency |
| 4 | Tests do not require live Binance | 1 | monkeypatched providers; `requests.get` replaced in provider tests |
| 5 | CI green on latest intended commit | 1 | dd88ec9 runs 39781713504 (push) and 39781707473 (live) success |
| 6 | Evaluator tests | 1 | metrics, coverage, Brier, holdout |
| 11 | Provider-layer parsing and error contract | 1 | `test_market_provider`: endpoint fallback, schema drift → `MarketDataError` |
| 7 | Paper/backtest tests | 1 | fees, limits, priority |
| 8 | Application startup verified | 1 | uvicorn; `/api/health`, `/chart`, `/chart-core.js` returned 200 |
| 9 | Browser-level chart test | 1 | `tests/browser/*.spec.js`, 7 passed in 1.1m in run 39781707473 |
| 10 | Failure logs inspected | 1 | chart string mismatch and metric expectations fixed |
| 12 | Structured logging with an operator-visible tail | 1 | `app/observability.py`, `/api/logs/recent`, 15 tests |
| 13 | End-to-end live evidence loop, honestly attributed | 1 | `scripts/freeze_and_resolve.py` (10 tests) freezes, waits the forecast's own horizon, resolves, and names whether this run's forecast was the row resolved |
| 14 | Reproducible batch measurement with published summary | 1 | `scripts/summarize_trial_batch.py` (8 tests) reports accuracy + Wilson CI + baselines + non-overlapping subset; batch run `39761203609` published it from CI |

**Testing: 14 / 14 = 100%**

## 8. Documentation

| # | Item | Score |
|---|---|---|
| 1 | README describes actual behavior | 1 |
| 2 | Checklist-based project status | 1 |
| 3 | Agent pipeline status documented | 1 |
| 4 | Setup instructions | 1 |
| 5 | Known limitations | 1 |
| 6 | Recorded experiment documented | 1 |
| 7 | VS Code synchronization instructions | 1 |
| 8 | No secrets committed | 1 |

**Documentation: 8 / 8 = 100%**

Secret scan: no API keys, private keys, passwords or personal access tokens were found. The only token reference is `${{ secrets.GITHUB_TOKEN }}` in the workflow, which is provided by GitHub.

## 9. Verified working now

- **Replay mode is real, not a mock.** `scripts/export_candles_fixture.py` ran on a CI runner that can reach Coinbase and recorded 1,500 candles (5 intervals x 300, BTCUSD 79,676–84,856) into `fixtures/candles.json`. Replaying it, `/api/candles` answered 120 valid 1h candles with `provider: replay:BTCUSDT@1h`, and all nine app timeframes produced correctly spaced rows (60/180/300/900/1800/3600/7200/14400/86400 s).
- The agent pipeline completes on the recording: `market_data`, `market_analysis`, `prediction`, `risk_decision`, `timetraveler` and `report` all `completed`; `research` fails because the news provider is unreachable here; action stays `NO TRADE`.
- A real Chromium run (`Replay browser check` `39900034198`) proved the chart renders the recording, shows the REPLAY badge instead of LIVE, never opens a stream, and that `/api/health` reports `data_mode: replay`.

- The 150-trial live batch ran to completion in CI (run `39761203609`, three shards, ~2.5 h) and both the batch summary and the chronological evaluation were published by the workflow itself. All 149 usable trials observed the outcome through a real spot ticker with a 0.0 s median offset.

- `uvicorn app.main:app` starts; `/api/health` returns `PAPER ONLY`, live disabled.
- `/chart` (22,950 bytes), `/chart-core.js` (16,400 bytes, `application/javascript`), `/` and `/api/reports/forecast-evaluation` return 200.
- With Binance unreachable, `/api/candles/BTCUSDT?interval=1h` and `/api/forecast/two-minute/BTCUSDT` return 502 rather than a misleading 400.
- Chart core behaves correctly in Node tests for validation, 1h alignment, switching, stale callbacks and reconnect limits.
- Backend forwards all nine intervals exactly, and a mocked 1h fixture produces candles spaced 3600 seconds apart.
- All seven agent stages run in tests; missing research is `unavailable`, not fabricated; action is `NO TRADE`.
- Report includes validation, research status, prediction metadata, risk metrics, blockers and warnings.
- Paper account applies fees/slippage/risk limits and records costs.
- Backtest includes configurable costs and conservative stop priority.
- Evaluator computes MAE, MAPE, RMSE, direction metrics, confusion matrix, coverage, Brier and baselines.
- The ledger reports direction accuracy against an always-FLAT baseline on the same resolved sample, with an edge figure, per-direction accuracy and a small-sample flag.
- The live evidence loop is now real: CI run `39759307781` froze forecast `e980f33b…`, waited **63.3s for its own 60s horizon**, resolved **its own** forecast through `spot_ticker` at `+0.05875172%`, and said so explicitly. Earlier runs could not show this — run `39753751064` resolved against a candle close and produced a degenerate `0.0%` (fixed by preferring a ticker), and run `39757341361` silently capped a 300s wait at 240s so the row it reported belonged to an *inherited* forecast (fixed by capping loudly and attributing the row).
- The ledger now accumulates across runs through `actions/cache` instead of resetting each time: an earlier run inherited 11 forecasts with 8 resolved. Because repeated forecasts against the same start price are not independent trials, the summary counts `distinct_reference_prices` and warns "3 of 8 resolved rows share a reference price with another row". The surviving edge (+12.5% in that run) is therefore explicitly labelled as flattered by duplicates, not evidence of skill.
- The resolver prefers a real spot ticker across providers (Binance, then Coinbase) before any candle fallback, and flags runs that had to fall back. This matters: CI run `39753751064` produced a **degenerate 0.0% return** because the reference price and the resolved candle close were the same series, so the comparison could not fail. That run's `beats_always_flat: false` / edge 0.0% is the honest outcome, and the flag now makes the limitation explicit.
- Every request and pipeline run emits a structured event; `/api/logs/recent` and the dashboard panel expose the recent tail, and secret-bearing fields are redacted before writing.
- The pipeline freezes each prediction in an append-only JSONL ledger before the report stage; tests confirm ordering, immutability and that a failed freeze is surfaced.
- Dashboard shows the recorded failed experiment and the rejected deployment decision.
- The paper account survives a real process restart: a trade seeded in one process was read back from a separately started uvicorn process (`/api/paper/account` showed `open_trades: 1`, `equity: 999.0`, matching the saved file).
- A deliberately corrupt state file caused the server to start with a fresh balance, return a `state_warning` explaining why, and preserve the damaged file as `*.corrupt-<timestamp>`.
- `GET /api/forecast/ledger` returned 200 with an empty, honest ledger state; resolving an unknown id returned 400 `unknown forecast_id`; the dashboard serves the ledger panel.
- A live-server smoke test wrote one **synthetic** ledger record through the API (never real market data), resolved it, saw `direction_accuracy_pct = 100.0` for that single record, then deleted the temporary `runtime/` file. This verifies the HTTP path only; it is not evidence of forecast skill.

## 10. NOT VERIFIED / blockers

0c. **Replay is a recording, not live market data.** It exists because this sandbox cannot reach any exchange. The recording is a snapshot from `2026-10-23T04:12Z`; it does not update, and the freshness rule is skipped for it *by declaration only* (the live path still rejects stale candles, proven by `test_stale_candles_still_fail_outside_replay`).

0. **Binance itself remains unverified.** Binance answers GitHub-hosted runners with HTTP 451 (geo-block) and is unreachable from the development sandbox. The chart, intervals and server endpoints are verified through the labelled Coinbase fallback. Verifying Binance's own candles and WebSocket requires a network Binance serves.
0b. **A defect the browser run caught:** `LiveStream` stored host timer functions and called them as object methods, which Chrome rejects with `TypeError: Illegal invocation`. Node does not enforce the receiver rule, so 18 unit tests passed while the first real browser run failed. Fixed in `acd02bd` and covered by a regression test.

1. **Live browser chart:** no browser or display server is available. Smoothness, zoom preservation, and real socket reconnect UX are not browser-verified.
2. **Live Binance data:** Binance is unreachable from this sandbox (TLS/connection failure). Real 1h stream behavior and live provider latency are not verified.
3. **Live research:** CoinDesk RSS could not be reached; parsing is unit-tested, live availability is not.
4. **Original artifacts:** the 100 trial JSON files are not in the repository; the enhanced evaluator has not been re-run on them.
5. **New experiments:** a second 150-trial batch was run and it did not show skill (44.3% accuracy, below a 49.66% majority baseline). The 60-second cadence means adjacent trials overlap; the independent subset is 49 trials at 38.8%. No fee/slippage model is applied to this accuracy figure because no trade is executed.
6. **Persistence:** paper trades and forecasts are now written to local files, but neither is a database: there is no cross-process locking, access control, replication or backup.
7. **Automated resolution:** `scripts/resolve_forecasts.py` now runs against live Coinbase tickers inside the CI job (verified in `39759307781`), but nothing schedules it outside that workflow and it is not a production cron.
8. **1-minute candles from Coinbase lag.** One pipeline run failed `market_data` with `Market data is stale (189s old; limit 180s)` for interval `1m`; the same run succeeded on `5m`, and a later run succeeded on `1m`. The freshness rule (3 intervals) was **not** loosened for this, so 1-minute evidence depends on the provider publishing promptly. Binance, which publishes faster, is geo-blocked from the runners.

## 11. Next highest-priority tasks

1. Verify Binance natively (candles and WebSocket) from a network Binance serves; the CI runner is geo-blocked, so only the Coinbase path is proved there.
2. Download the original 100 trial artifacts and re-run the enhanced evaluator to replace the aggregate-only report with full metrics on the real data.
3. Grow the live ledger to at least 30 *independent* start prices (the current guard counts duplicates), then re-read the always-FLAT comparison — the current edge is not yet meaningful.
4. Replace the local JSON files with a real datastore (SQLite) if multi-process access is ever needed; today two servers on the same file would not coordinate.
6. Add an integration test against a recorded Binance fixture.
7. Review and merge pull request #1 (`https://github.com/MDSIDD533/test-repo/pull/1`) to `main`, then pull locally in VS Code; no automatic sync exists.

## 12. Git and synchronization

- All work is on `arena/a852519f-test-repo`; it is pushed to origin.
- Pull request #1 is open against `main`. Until it is reviewed and merged, `main` is unchanged.
- VS Code does not update automatically. Use `git pull --ff-only` after merge. No sync workflow is configured or verified.
