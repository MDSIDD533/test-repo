"""Run a live 2-minute forecast experiment against public Binance spot data.

Usage: python scripts/two_minute_forecast_test.py BTCUSDT
The script waits for the full 120-second horizon and prints a JSON report.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Permit running this file directly from the repository root on Windows.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market import candles
from app.analysis import analyze_candles
from app.news_provider import fetch_crypto_news
from app.short_horizon_forecast import compare_forecast, make_two_minute_forecast


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").strip().upper()
    started_at = utc_now()
    report = {
        "symbol": symbol,
        "started_at": started_at,
        "horizon_seconds": 120,
        "mode": "OBSERVATION ONLY",
        "live_orders_enabled": False,
    }
    try:
        initial = candles(symbol, "1m", 40)
        if len(initial) < 35:
            raise RuntimeError("Not enough 1-minute candles for analysis")
        # Market endpoint's latest kline can still be forming; use only completed
        # candles to calculate the technical snapshot, while taking the latest
        # close as the observed baseline price.
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
            initial,
            symbol,
            analysis.get("signal", "WAIT"),
            research_items,
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

        final_candles = candles(symbol, "1m", 5)
        actual_price = float(final_candles[-1]["close"])
        comparison = compare_forecast(forecast, actual_price)
        report.update({
            "finished_at": utc_now(),
            "actual_price_after_120s": actual_price,
            "comparison": comparison,
            "algorithm_upgrade_recommended": not comparison["almost_same"],
            "upgrade_note": (
                "Mismatch detected. Do not tune from this single sample; collect at least 30-100 "
                "timestamped out-of-sample trials, then compare baseline models and tune only on "
                "training data before a separate holdout evaluation."
                if not comparison["almost_same"] else
                "This single sample met the predefined tolerance; repeated out-of-sample tests are still required."
            ),
        })
        output_path = ROOT / "two_minute_forecast_report.json"
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"phase": "FINAL_COMPARISON", **report, "report_file": str(output_path)}, indent=2))
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(report, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
