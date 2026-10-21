"""Run a live 2-minute forecast using public spot candles.

Usage: python scripts/two_minute_forecast_test.py BTCUSDT
The script waits for the full 120-second horizon and prints a JSON report.
If Binance is blocked, BTCUSDT falls back to Coinbase BTC-USD public candles.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market import candles
from app.analysis import analyze_candles
from app.news_provider import fetch_crypto_news
from app.short_horizon_forecast import compare_forecast, make_two_minute_forecast


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coinbase_btc_candles(limit: int = 40) -> list[dict[str, float]]:
    """Fetch Coinbase BTC-USD 1-minute candles in the app's candle format."""
    response = requests.get(
        "https://api.exchange.coinbase.com/products/BTC-USD/candles",
        params={"granularity": 60},
        headers={"Accept": "application/json", "User-Agent": "SiddTradingPro-forecast-test/1.0"},
        timeout=(5, 12),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Coinbase returned an unexpected candle payload")
    rows = []
    # Coinbase candle order: [time, low, high, open, close, volume], newest first.
    for item in reversed(payload):
        if not isinstance(item, list) or len(item) < 6:
            continue
        rows.append({
            "time": float(item[0]),
            "low": float(item[1]),
            "high": float(item[2]),
            "open": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    if len(rows) < 35:
        raise RuntimeError(f"Coinbase returned only {len(rows)} usable candles")
    return rows[-limit:]


def fetch_experiment_candles(symbol: str, limit: int) -> tuple[list[dict[str, float]], str]:
    """Use Binance first; fall back to Coinbase for BTCUSDT observation trials."""
    try:
        return candles(symbol, "1m", limit), "binance"
    except Exception as binance_error:
        if symbol != "BTCUSDT":
            raise RuntimeError(
                f"Binance failed and no fallback is configured for {symbol}: {binance_error}"
            ) from binance_error
        try:
            return _coinbase_btc_candles(limit), "coinbase:B​​TC-USD".replace("\u200b", "")
        except Exception as fallback_error:
            raise RuntimeError(
                f"All market data providers failed. Binance: {binance_error}; "
                f"Coinbase BTC-USD: {type(fallback_error).__name__}: {fallback_error}"
            ) from fallback_error


def main() -> int:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").strip().upper()
    started_at = utc_now()
    report: dict[str, Any] = {
        "symbol": symbol,
        "started_at": started_at,
        "horizon_seconds": 120,
        "mode": "OBSERVATION ONLY",
        "live_orders_enabled": False,
    }
    try:
        initial, data_provider = fetch_experiment_candles(symbol, 40)
        report["market_data_provider"] = data_provider
        if len(initial) < 35:
            raise RuntimeError("Not enough 1-minute candles for analysis")
        now = time.time()
        closed = [row for row in initial if float(row["time"]) + 60 <= now]
        if len(closed) < 35:
            raise RuntimeError("Not enough completed 1-minute candles")
        analysis = analyze_candles(closed, symbol, "1m")
        research_items = []
        research_status = "unavailable"
        research_error = None
        try:
            research_items = fetch_crypto_news(symbol, started_at)
            research_status = "available" if research_items else "no_matching_recent_headlines"
        except Exception as exc:
            research_error = f"{type(exc).__name__}: {exc}"

        forecast = make_two_minute_forecast(
            initial, symbol, analysis.get("signal", "WAIT"), research_items
        )
        forecast["baseline_price"] = float(initial[-1]["close"])
        forecast["forecast_price"] = round(
            forecast["baseline_price"] * (1.0 + forecast["expected_return_pct"] / 100.0), 8
        )
        report.update({
            "research_status": research_status,
            "research_error": research_error,
            "research_headlines": [
                {"title": item.get("title"), "source": item.get("source"),
                 "published_at": item.get("published_at"), "url": item.get("url")}
                for item in research_items[:5]
            ],
            "technical_signal": analysis.get("signal"),
            "technical_reasons": analysis.get("reasons", []),
            "forecast": forecast,
        })
        print(json.dumps({"phase": "FORECAST_LOCKED", **report}, indent=2), flush=True)
        time.sleep(120)

        final_candles, final_provider = fetch_experiment_candles(symbol, 5)
        actual_price = float(final_candles[-1]["close"])
        comparison = compare_forecast(forecast, actual_price)
        report.update({
            "finished_at": utc_now(),
            "market_data_provider_at_finish": final_provider,
            "actual_price_after_120s": actual_price,
            "comparison": comparison,
            "algorithm_upgrade_recommended": not comparison["almost_same"],
            "upgrade_note": (
                "Mismatch detected. Do not tune from one sample; collect 30-100 timestamped "
                "out-of-sample trials, then compare baseline models and tune only on training "
                "data before a separate holdout evaluation."
                if not comparison["almost_same"] else
                "This sample met the predefined tolerance; repeated out-of-sample tests are still required."
            ),
        })
        output_path = ROOT / "two_minute_forecast_report.json"
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"phase": "FINAL_COMPARISON", **report, "report_file": str(output_path)}, indent=2))
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        output_path = ROOT / "two_minute_forecast_report.json"
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
