"""Run a live 2-minute forecast using public spot candles.

Usage: python scripts/two_minute_forecast_test.py BTCUSDT
The script waits for the full 120-second horizon and prints a JSON report.
If Binance is blocked, BTCUSDT falls back to Coinbase BTC-USD public candles.
"""
from __future__ import annotations

import json
import math
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

def fetch_experiment_spot_price(symbol: str, provider: str) -> float:
    """Fetch a spot ticker price so the 120-second horizon starts at a precise observation."""
    if provider == "binance":
        response = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=(5, 12),
        )
        response.raise_for_status()
        price = float(response.json()["price"])
    elif provider == "coinbase:BTC-USD" and symbol == "BTCUSDT":
        response = requests.get(
            "https://api.exchange.coinbase.com/products/BTC-USD/ticker",
            headers={"Accept": "application/json", "User-Agent": "SiddTradingPro-forecast-test/1.0"},
            timeout=(5, 12),
        )
        response.raise_for_status()
        price = float(response.json()["price"])
    else:
        raise RuntimeError(f"No spot ticker is configured for {symbol} via {provider}")
    if not math.isfinite(price) or price <= 0:
        raise RuntimeError("Spot ticker returned a non-finite or non-positive price")
    return price


def fetch_actual_price_with_fallback(
    symbol: str,
    provider: str,
    target_time: float,
    *,
    tolerance_seconds: float = 90.0,
    candle_limit: int = 40,
) -> dict[str, Any]:
    """Observe the target-time price, with a documented nearest-candle fallback.

    A spot ticker is preferred. If it is unavailable, the closest completed
    1-minute candle close within ``tolerance_seconds`` is used and the time
    difference is returned. A wider gap raises instead of silently pretending
    the observation happened at the target timestamp.
    """
    try:
        price = fetch_experiment_spot_price(symbol, provider)
        return {
            "price": price,
            "source": "spot_ticker",
            "provider": provider,
            "observed_at": target_time,
            "target_time": target_time,
            "time_difference_seconds": 0.0,
        }
    except Exception as spot_error:
        rows, fallback_provider = fetch_experiment_candles(symbol, candle_limit)
        candidates = []
        for row in rows:
            close_time = float(row["time"]) + 60.0
            difference = abs(close_time - target_time)
            if close_time <= target_time + tolerance_seconds:
                candidates.append((difference, close_time, float(row["close"])))
        if not candidates:
            raise RuntimeError(
                f"No ticker and no completed candle near the target time. "
                f"Ticker error: {spot_error}"
            ) from spot_error
        difference, close_time, price = min(candidates, key=lambda item: item[0])
        if difference > tolerance_seconds:
            raise RuntimeError(
                f"Nearest completed candle is {difference:.1f}s from the target time, "
                f"beyond the {tolerance_seconds:.0f}s tolerance. Ticker error: {spot_error}"
            )
        if not math.isfinite(price) or price <= 0:
            raise RuntimeError("Nearest-candle fallback returned an invalid price")
        return {
            "price": price,
            "source": "nearest_completed_1m_candle_close",
            "provider": fallback_provider,
            "observed_at": close_time,
            "target_time": target_time,
            "time_difference_seconds": round(difference, 3),
            "ticker_error": f"{type(spot_error).__name__}: {spot_error}",
        }


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
            closed, symbol, analysis.get("signal", "WAIT"), research_items
        )
        # Freeze a spot-ticker baseline at the start, rather than an unfinished
        # one-minute candle close, to align the 120-second measurement horizon.
        baseline_price = fetch_experiment_spot_price(symbol, data_provider)
        baseline_observed_at = time.time()
        target_time = baseline_observed_at + 120.0
        forecast["baseline_price"] = baseline_price
        forecast["baseline_observed_at"] = datetime.fromtimestamp(
            baseline_observed_at, timezone.utc
        ).isoformat()
        forecast["target_time"] = datetime.fromtimestamp(target_time, timezone.utc).isoformat()
        forecast["forecast_price"] = round(
            baseline_price * (1.0 + forecast["expected_return_pct"] / 100.0), 8
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
        remaining = target_time - time.time()
        if remaining > 0:
            time.sleep(remaining)

        observation = fetch_actual_price_with_fallback(symbol, data_provider, target_time)
        actual_price = observation["price"]
        comparison = compare_forecast(forecast, actual_price)
        report.update({
            "finished_at": utc_now(),
            "market_data_provider_at_finish": observation["provider"],
            "actual_price_after_horizon": actual_price,
            "actual_observation": observation,
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
