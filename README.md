# Crypto Spot Trading Analysis Agent

A paper-only crypto spot analysis, market research, short-horizon forecasting and evaluation platform.

**Status: PARTIALLY COMPLETE.** The dashboard, chart pipeline, agent stages and forecast evaluator run and are covered by automated tests. Live browser verification and new live exchange trials are **NOT VERIFIED** in the development sandbox because Binance is unreachable there and no browser is available. See [`docs/project_status.md`](docs/project_status.md) for the measured checklist, test evidence and remaining gaps.

## Safety defaults

- Binance USDT spot pairs only; no futures and no short positions.
- **Live execution is disabled in code.** There is no order-submission path and no exchange API credentials are used.
- Paper trading uses configurable risk, fees and slippage; it never contacts an exchange to place an order.
- Heuristic signals and confidence scores are **not** calibrated profit probabilities.
- The recorded two-minute BTC experiment did **not** beat a zero-return baseline and was **not deployed**.

## What works

### Dashboard and chart

- `/` dashboard with scanner, signal analysis, paper account and per-stage agent output.
- `/chart` interactive candlestick chart with 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h and 1d timeframes.
- A 120-second forecast preview is available from the dashboard or `GET /api/forecast/two-minute/{symbol}`. It is explicitly unrecorded and uncalibrated; the frozen, evaluated experiment is the GitHub Actions trial runner.
- The agent pipeline freezes its prediction into an **append-only forecast ledger** before the report stage runs. Resolutions are appended later as separate events; a frozen forecast is never edited or deleted.
- Binance is the primary provider. When Binance refuses a location (HTTP 451) or is unreachable, **BTCUSDT** candles fall back to Coinbase BTC-USD for the intervals Coinbase really serves (1m, 5m, 15m, 1h, 6h, 1d). Responses state which provider was used; unsupported symbols/intervals still fail instead of inventing candles.
- Binance kline WebSocket updates the forming candle in place.
- The previous socket is closed when the symbol or timeframe changes; late callbacks from superseded sockets are ignored.
- Bounded exponential reconnection with a visible `RECONNECTING` / `OFFLINE` state.
- REST refresh every 15 seconds as a fallback, plus refresh when the tab becomes visible.
- Data validation rejects malformed, misaligned, duplicate or out-of-order candles. A 1h chart rejects candles whose timestamps are not aligned to one hour.
- `LAST CANDLE CLOSED` / `FORMING CANDLE` indicator, last-update time, source and connection state.
- A stale stream is labelled `STALE`; the page never says `LIVE` when only REST data is available.

### Agents

| Stage | Actual behaviour | Status |
|---|---|---|
| Market Data | Validates candle fields, OHLC consistency, ordering, gaps, freshness and status | Working |
| Market Analysis | Rule-based indicators: EMA 9/20/50/200, RSI, MACD, ATR, structure, support/resistance | Working |
| Research | CoinDesk RSS provider with source/freshness/URL checks; no evidence is reported as `UNAVAILABLE` | Working when network is available; **NOT VERIFIED** from this sandbox |
| Prediction | Direction only (`UP`/`DOWN`/`FLAT`), frozen `forecast_created_at`, horizon, reference price, model version and limitations. No point price or probability is fabricated. Each successful prediction is frozen in the ledger | Working, uncalibrated |
| Risk Decision | Computes reward/risk, ATR volatility and authorization checks; always `NO TRADE` without independently calibrated evidence | Fail-closed |
| TimeTraveler | Seeded historical-return bootstrap (100/500/1000 paths), explicitly labelled simulation | Working, not calibrated |
| Report | Combines real stage statuses, validation, research, forecast, risk metrics, blockers and errors | Working |

Every pipeline stage can fail independently. Missing research is recorded as `unavailable`, not as a fake success. The pipeline returns `NO TRADE` unless a prediction is calibrated **and** the risk gate explicitly authorizes a trade.

### Forecast evaluation

- 100 recorded live two-minute BTC trials (workflow run `39610582675`):
  - direction matches: **44/100**
  - strict predefined tolerance passes: **37/100**
  - original mean absolute price error: **0.063793%** of starting price
  - zero-return baseline mean absolute price error: **0.048120%**
- Chronological 70/30 evaluation: candidate holdout MAE **0.027016%** versus zero-return baseline **0.024944%**.
- **Second live batch, 2026-10-22, 150 trials (run `39761203609`): 149 valid**, 44.3% direction accuracy (66/149, 95% CI 36.6–52.3%), strict tolerance 58/149 (38.9%), MAE 0.045871% of start versus zero-return 0.036681%.
- That batch **did not beat the baselines**: the majority-class baseline is 49.66%, the model's error is higher than assuming no move, and the confidence interval covers a coin flip. The 149 trials span 150 minutes with a 60-second cadence, so only **49** are non-overlapping windows (38.8% accuracy) — the conservative read.
- Decision: **`DO_NOT_DEPLOY`** stands. Full numbers: `docs/evidence/batch-150-summary.json` and `docs/evidence/batch-150-evaluation.json`.
- `scripts/resolve_forecasts.py` appends observed outcomes for due ledger forecasts. It tries a real spot ticker across providers (Binance, then Coinbase) before any candle fallback, uses the same no-look-ahead candle rule as the experiment, and flags any run that had to fall back. `--dry-run` shows what would be resolved.
- Once outcomes exist, the ledger reports **direction accuracy next to an always-FLAT baseline on the same resolved sample**, plus per-direction accuracy, an edge figure and a small-sample flag. A model that does not beat always-FLAT is labelled as losing to it in the dashboard.
- The CI `live-verification` workflow runs `scripts/freeze_and_resolve.py`, which freezes a real forecast, **reads its own target time** and waits exactly that long, resolves it against a real spot ticker, and publishes the accuracy-versus-baseline numbers. It reports every step, says whether the row it reports is *this run's* forecast, and warns when a wait was capped.
- The resolved ledger is cached between workflow runs so the sample accumulates instead of resetting. Repeated rows that share a start price are counted and warned about — they are not independent trials.
- `scripts/evaluate_forecast_models.py` now reports MAE, MAPE, RMSE, direction accuracy, UP/DOWN/FLAT precision/recall/F1, confusion matrix, coverage, invalid-trial count, strict pass rate, Brier score when probabilities exist, and zero-return/momentum/technical baselines. Holdout outcomes are never used for tuning.

The stored aggregate report is `reports/forecast-model-evaluation-2026-10-21.json`. The individual trial artifacts of the first batch are not in this repository, so the enhanced evaluator has not been re-run on those 100 trials. The 150-trial batch was evaluated in full and its summaries are committed under `docs/evidence/`.

To run a batch yourself, `gh workflow run live-forecast-batch.yml -f confirmation=RUN-LIVE-BATCH -f trials=150`. A batch is expensive runner time: three shards, 50 trials each, 180 s apart, roughly 2.5 hours.

### Live streaming without Binance

The browser never connects to an exchange directly. It opens `/ws/candles` on the app's own origin, and the server relays:

1. **Binance** kline stream (preferred).
2. **Coinbase** `matches` stream, aggregated into the requested interval — used automatically when Binance is unreachable or geo-blocked (HTTP 451).

Every candle carries the provider that produced it (`k.provider`), and `GET /api/stream/providers?symbol=BTCUSDT` lists the order that will be tried. `STREAM_PROVIDER=coinbase` or `STREAM_PROVIDER=binance` pins one provider; `auto` is the default. A provider that connects but sends no candle within 12 s is treated as failed and the next one is tried, so an open-but-silent socket never looks healthy.

This is what makes the platform usable on networks Binance blocks: CI runner verification (run `39903074565`) reached `connection=live` with `provider=coinbase:BTC-USD` and a websocket message that actually moved the chart, while Binance answered HTTP 451.

### Offline replay mode (recorded data, never presented as live)

Some networks cannot reach an exchange at all — the development sandbox here cannot. Rather than invent candles, the app can replay a **recording of real provider data**:

```bash
MARKET_DATA_MODE=replay uvicorn app.main:app --port 8000
```

- `fixtures/candles.json` holds candles recorded from the real provider layer (BTCUSDT via `coinbase:BTC-USD`, five intervals, 300 candles each) with the provider's own timestamps.
- Every response states its source: `/api/candles` returns `"provider": "replay:BTCUSDT@5m"`, `/api/health` returns `"data_mode": "replay"` with the recording's `fetched_at`, and `/api/data-mode` describes the recording.
- Intervals the recording does not contain (`3m`, `30m`, `2h`, `4h`) are **resampled from a real coarser/finer interval** and labelled, e.g. `replay:BTCUSDT@1h->4h`. Nothing is synthesised.
- The chart shows a **REPLAY** badge, never `LIVE`, opens **no WebSocket**, and displays a banner with the recording time. The dashboard shows the same banner.
- The agent pipeline runs on the recording, but `validate_candles(..., replay=True)` skips **only** the freshness rule, records `freshness_skipped` / `live_data: false` / `stale_by_seconds`, and the action stays `NO TRADE`. The live path still rejects stale candles.
- Regenerate the recording on a machine that can reach an exchange: `python scripts/export_candles_fixture.py --output fixtures/candles.json`.
- Browser-verified by `tests/browser/replay.spec.js` in the `Replay browser check` workflow: real Chromium loads the chart from the recording and asserts the page never claims to be live.

### Paper trading and backtesting

- Paper account with balance, equity, open exposure, realized P&L and fees, persisted to an atomic JSON file (`runtime/paper_state.json` by default) and restored on restart.
- Configurable risk per trade, maximum open positions, per-side fee and slippage.
- Duplicate open positions for the same symbol are rejected.
- A damaged state file is quarantined as `*.corrupt-<timestamp>` and reported through `state_warning`; the account is never silently reset without a trace.
- Stop-loss and TP1 exits record requested/filled price, gross P&L, fees, net P&L, reason and timestamps.
- Backtest applies configurable fees/slippage and conservatively assumes the stop is hit first when one candle touches both stop and target.
- Backtests remain simplified: no order-book depth, spread, funding (spot) or intrabar tick data.

## How to verify this yourself

Three levels, in increasing order of what they can prove.

### 1. Offline (works anywhere, no network, no browser)

    python -m pytest -q
    node --test static/chart-core.test.js

This covers chart logic, agent contracts, the evaluator, paper accounting, the ledger and provider *parsing* (with `requests.get` replaced by a fake). It proves the code behaves correctly against fixtures. It cannot prove Binance behaves as documented, and it cannot see a browser render.

### 2. Real providers + real Chromium (GitHub Actions)

The development sandbox has no browser and cannot reach Binance, so the live claims are verified by `.github/workflows/live-verification.yml`. That workflow:

- installs Chromium via Playwright,
- runs `scripts/live_verification.py`, which contacts Binance for a spot ticker, 1m/5m/1h/4h/1d klines, a live kline WebSocket and the news feed, and checks every local endpoint,
- runs **one** real frozen two-minute trial with `scripts/two_minute_forecast_test.py`,
- drives the app in Chromium: loads `/chart`, asserts candles render, asserts the badge never says `LIVE` unless a socket opened, switches 1m/1h/4h, checks that 1h/4h/1d candles are exactly 3600/14400/86400 seconds apart and aligned, and checks that a manual refresh does not reset the zoom,
- posts the results as a comment on the pull request and uploads screenshots and JSON evidence as an artifact.

Trigger it with **Actions → Live verification (browser + real providers) → Run workflow**, or it runs automatically when the files it verifies change. A run takes a few minutes and posts its results as a pull-request comment.

Latest result (run `39743024896`): provider checks 10 passed / 7 skipped (Binance geo-blocked) / 0 failed, one real two-minute trial completed, and 7 of 7 browser tests passed. That run also caught a genuine bug — `LiveStream` invoked host timers as object methods, which Chrome rejects with `TypeError: Illegal invocation`; Node does not enforce that rule, so only the browser found it.

Read the numbers as: a single trial proves the freeze→observe→compare mechanics, not skill.

### 3. Your own machine (full fidelity)

    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m uvicorn app.main:app --reload
    # open http://127.0.0.1:8000/chart

    # in a second terminal, with the server running:
    python scripts/live_verification.py --out evidence/provider.json
    python scripts/two_minute_forecast_test.py BTCUSDT | tee evidence/trial.json
    python scripts/resolve_forecasts.py --dry-run

For the browser suite locally:

    npm install --no-save @playwright/test
    npx playwright install --with-deps chromium
    npx playwright test

Nothing in levels 2 or 3 places real orders. `scripts/resolve_forecasts.py` only reads public prices and appends to the local ledger.

## Run locally

Python 3.11+ recommended.

    python -m venv .venv
    .venv\Scripts\Activate.ps1        # Windows
    source .venv/bin/activate          # Linux/macOS
    python -m pip install -r requirements.txt
    uvicorn app.main:app --reload

Open http://127.0.0.1:8000. For phone access on the same Wi-Fi, run with `--host 0.0.0.0` and visit the computer's LAN IP.

Copy `.env.example` values into your environment if you want to change paper risk, fee, slippage or position limits. Do not put secrets in the repository.

## Tests

    python -m pytest -q
    node --test static/chart-core.test.js

Current verified baseline (sandbox, Python 3.11.2, Node 22.22.3):

- Python: **327 passed**.
- JavaScript chart core: **19 passed**.
- Browser and live-provider checks: **7 browser tests passed** and the provider checks passed in run `39743024896` (commit `acd02bd`). Binance returned HTTP 451 to that runner, so the verified intervals (`5m`, `1h`, `1d`) used the labelled Coinbase fallback; Binance itself is still unverified.
- GitHub Actions `tests` workflow: green on the latest pushed commit at time of writing; the status of the newest commit must always be checked separately.

Tests mock market data. They do not require live Binance availability. Provider-layer tests replace `requests.get`, so URL selection, endpoint fallback, kline parsing and the `MarketDataError` contract are covered without network access; every test also gets a private paper-state and ledger file. The chart-core tests cover message validation, 1h alignment, candle ordering/de-duplication, stale detection, symbol/timeframe switching, superseded callbacks, reconnect backoff and retry limits.

## API

- `GET /api/health`
- `GET /api/market/summary`
- `GET /api/scanner`
- `GET /api/analyze/{symbol}?interval=5m`
- `GET /api/candles/{symbol}?interval=5m&limit=200`
- `GET /api/candle-signals/{symbol}?interval=5m&limit=200`
- `GET /api/agents/run/{symbol}?interval=5m&simulation_count=100`
- `GET /api/forecast/two-minute/{symbol}` (unrecorded preview)
- `GET /api/forecast/ledger` (frozen forecasts, resolutions, always-FLAT baseline)
- `GET /api/logs/recent?limit=50&level=warning` (operator-visible structured event tail)
- `POST /api/forecast/ledger/{forecast_id}/resolve` (append an observed outcome)
- `GET /api/reports/forecast-evaluation`
- `GET /api/paper/account`
- `GET /api/paper/trades`
- `POST /api/paper/refresh`
- `POST /api/paper/open`
- `POST /api/backtest/{symbol}?interval=5m&limit=500&fee_bps=10&slippage_bps=0`
- `GET /chart`, `GET /chart-core.js`

## Known limitations and blockers

- Browser-level verification and live-provider checks are **NOT VERIFIED from the development sandbox**, which has no browser and cannot reach Binance. They are automated in `.github/workflows/live-verification.yml`; see "How to verify this yourself" above. The workflow has to have run at least once against the current commit before you may treat those claims as verified.
- Binance and CoinDesk are unreachable from the development sandbox. Binance additionally returns **HTTP 451 (geo-block)** to GitHub-hosted runners, so Binance-specific claims can only be verified from a network Binance serves. The Coinbase fallback covers BTCUSDT and is verified in CI.
- The original 100-trial artifacts are not stored in the repository; only the aggregate evaluation report is available. The 150-trial batch **is** stored as a summary, not as 150 raw files.
- The 150-trial batch was sampled every 60 seconds, so adjacent trials overlap by 60 seconds and are not independent. Only 49 of 149 trials are non-overlapping windows.
- Prediction in the agent pipeline is direction-only and uncalibrated. The two-minute experiment is the only point-forecast experiment, and it did not beat its baseline.
- Structured events are written as JSON lines to `runtime/app.log` and kept in a 500-event in-memory ring. Known secret-bearing fields are redacted; a logging failure never fails a request. This is a log tail, not a metrics or alerting system.
- The forecast ledger is an append-only local JSONL file (`runtime/forecast_ledger.jsonl` by default, git-ignored). It is not a database, has no access control, and is not replicated. Resolution is a manual/scripted step; nothing resolves forecasts automatically.
- The paper account is a local JSON file with no access control, locking across processes or replication. Two servers pointed at the same file would not coordinate. There is no database, authentication, alert delivery or multi-user isolation.
- No live trading remains a deliberate invariant. Any future live execution requires a separate safety-critical review, explicit authorization and tested order controls.

## VS Code synchronization

This repository is the canonical source. Local VS Code files update only after a normal Git operation:

    git pull --ff-only

There is no verified automatic sync workflow in this repository. Commits made here must be reviewed, merged and pulled locally; do not assume the local working tree updated itself.

## Documentation

- [`docs/project_status.md`](docs/project_status.md) — checklist-based status, evidence, percentages and next steps.
- [`docs/agent_pipeline.md`](docs/agent_pipeline.md) — stage contracts, confidence policy and implementation status.
- [`reports/README.md`](reports/README.md) — recorded experiment, reproduction command and evaluation rules.
