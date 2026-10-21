# Crypto Spot Trading Analysis Agent

Responsive crypto spot analysis dashboard for desktop and mobile.

## Safety defaults
- Binance USDT spot pairs only; no futures or short positions.
- Live execution is disabled in code. No real orders are submitted.
- Paper-trading risk defaults to 1% of virtual equity per trade.
- Signals and confidence are heuristics, not guarantees or financial advice.

## Current starter features
- English / Roman Urdu responsive web dashboard.
- Public Binance prices and 5-minute candles.
- BUY / SELL / WAIT, entry, TP1-TP3, stop loss, RSI, EMA, MACD and simple price-action context.
- 15m and 1h confirmation, scanner, paper account and basic backtest endpoint.
- News, notifications and AI chat deferred until a later phase.

## Run locally
Python 3.11+ recommended.

    python -m venv .venv
    .venv\\Scripts\\Activate.ps1
    python -m pip install -r requirements.txt
    uvicorn app.main:app --reload

Open http://127.0.0.1:8000. For phone access on the same Wi-Fi, run with --host 0.0.0.0 and visit the computer's LAN IP on the phone. Public hosting needs HTTPS and deployment configuration.

## Automation roadmap

The multi-agent architecture and acceptance criteria are documented in [`docs/agent_pipeline.md`](docs/agent_pipeline.md). Implemented building blocks now include candle validation, technical market analysis, a source-provenance/freshness-gated research agent interface, and reproducible TimeTraveler bootstrap simulations for 100, 500, or 1000 scenarios. Research requires a real provider to be injected; it does not invent headlines. TimeTraveler outputs scenario distributions, not calibrated win probabilities.

The partial pipeline endpoint `GET /api/agents/run/{symbol}?interval=5m&simulation_count=100` currently runs the market-data and market-analysis stages. It intentionally stops at the unconfigured research stage and returns `NO TRADE`. Prediction calibration, risk/decision, end-to-end TimeTraveler orchestration, reporting, and outcome tracking are not yet connected. The system must be allowed to return NO TRADE; 90% confidence is never assumed without out-of-sample calibration.

## API
- GET /api/health
- GET /api/market/summary
- GET /api/scanner
- GET /api/analyze/{symbol}
- GET /api/agents/run/{symbol} (partial pipeline; fails closed to NO TRADE)
- GET /api/candles/{symbol}
- GET /api/paper/account
- GET /api/paper/trades
- POST /api/paper/refresh
- POST /api/paper/open
- POST /api/backtest/{symbol}

## Known limitations
All eligible USDT pairs are discovered, but detailed candle analysis is limited to a configurable top-volume subset to respect exchange API rate limits. Paper trades are currently in memory and reset on restart. Open paper BUY trades are checked against the latest ticker price when the dashboard refreshes (about every 15 seconds); fast price moves between refreshes can be missed, and this is not an exchange-native stop order. Backtesting allows one simulated position at a time and models candle-open gaps, but excludes fees, slippage, and intrabar tick data, so results are approximate. Persistent storage, proper alert delivery, news/fundamentals, AI chat and production deployment hardening are future phases.