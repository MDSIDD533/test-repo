"""Verify the platform against real providers and a running server.

This script exists because the development sandbox cannot reach Binance. It is
written to run on GitHub Actions (or any machine with internet access) and to
report evidence honestly: a failed provider check is recorded as a failure, not
skipped.

Usage:
    python scripts/live_verification.py --out evidence/provider.json
    python scripts/live_verification.py --base-url http://127.0.0.1:8000

Exit code is 0 only when every critical check passes.
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests

from app.market import COINBASE_GRANULARITIES

class ProviderBlocked(Exception):
    """The provider refused this network location (HTTP 451). Not a code defect."""


INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400,
}


# --------------------------------------------------------------------------
# Pure validators (unit-tested without network access)
# --------------------------------------------------------------------------

def candle_spacing(times: list[float], interval: str) -> dict[str, Any]:
    """Check that a candle series is exactly spaced and aligned for its interval."""
    expected = INTERVAL_SECONDS.get(interval)
    if not expected:
        raise ValueError(f"Unknown interval: {interval}")
    if len(times) < 2:
        return {
            "interval": interval,
            "expected_seconds": expected,
            "candles": len(times),
            "deltas": [],
            "spacing_ok": False,
            "aligned_ok": False,
            "reason": "need at least two candles",
        }
    deltas = [round(times[index + 1] - times[index]) for index in range(len(times) - 1)]
    deltas = [int(delta) for delta in deltas]
    misaligned = [value for value in times if int(value) % expected != 0]
    return {
        "interval": interval,
        "expected_seconds": expected,
        "candles": len(times),
        "deltas": sorted(set(deltas)),
        "spacing_ok": all(delta == expected for delta in deltas),
        "aligned_ok": not misaligned,
        "misaligned_count": len(misaligned),
    }


def validate_ohlc(row: dict[str, Any]) -> list[str]:
    """Return the list of OHLC problems for one candle (empty means valid)."""
    problems: list[str] = []
    try:
        values = {key: float(row[key]) for key in ("open", "high", "low", "close")}
    except (KeyError, TypeError, ValueError):
        return ["missing or non-numeric OHLC fields"]
    for key, value in values.items():
        if not math.isfinite(value):
            problems.append(f"{key} is not finite")
    if not problems:
        if values["high"] < max(values["open"], values["close"]):
            problems.append("high is below open/close")
        if values["low"] > min(values["open"], values["close"]):
            problems.append("low is above open/close")
        if values["low"] > values["high"]:
            problems.append("low exceeds high")
        if values["low"] <= 0:
            problems.append("non-positive price")
    return problems


def validate_ws_kline(message: dict[str, Any], symbol: str, interval: str) -> list[str]:
    """Validate one Binance kline WebSocket message using the chart's rules."""
    problems: list[str] = []
    if message.get("e") != "kline":
        problems.append(f"unexpected event type {message.get('e')!r}")
    payload = message.get("k")
    if not isinstance(payload, dict):
        return problems + ["missing kline payload"]
    if str(payload.get("s", "")).upper() != symbol.upper():
        problems.append(f"symbol {payload.get('s')!r} does not match {symbol}")
    if payload.get("i") != interval:
        problems.append(f"interval {payload.get('i')!r} does not match {interval}")
    start = payload.get("t")
    if not isinstance(start, int) or start % (INTERVAL_SECONDS[interval] * 1000) != 0:
        problems.append("kline start is not aligned to the interval")
    problems.extend(
        validate_ohlc({"open": payload.get("o"), "high": payload.get("h"),
                       "low": payload.get("l"), "close": payload.get("c")})
    )
    return problems


def summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold check results into counts. Only ``critical`` checks gate the exit code."""
    critical = [check for check in checks if check.get("critical", True)]
    failed = [check["name"] for check in critical if check["status"] != "pass"]
    return {
        "total": len(checks),
        "passed": sum(1 for check in checks if check["status"] == "pass"),
        "failed": sum(1 for check in checks if check["status"] == "fail"),
        "skipped": sum(1 for check in checks if check["status"] == "skip"),
        "critical_failed": failed,
        "ok": not failed,
    }


# --------------------------------------------------------------------------
# Network checks
# --------------------------------------------------------------------------

def run_check(name: str, fn: Callable[[], Any], *, critical: bool = True) -> dict[str, Any]:
    started = time.time()
    try:
        detail = fn()
        return {
            "name": name,
            "status": "pass",
            "critical": critical,
            "duration_seconds": round(time.time() - started, 3),
            "detail": detail,
        }
    except ProviderBlocked as exc:
        # The provider geo-blocks this runner. That is an environment fact, not
        # a defect, so it is recorded as a skip with its reason attached. It is
        # never silently reported as a pass.
        return {
            "name": name,
            "status": "skip",
            "critical": critical,
            "duration_seconds": round(time.time() - started, 3),
            "reason": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - every failure is evidence here
        return {
            "name": name,
            "status": "fail",
            "critical": critical,
            "duration_seconds": round(time.time() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _raise_for_status(response: requests.Response, provider: str) -> None:
    if response.status_code == 451:
        raise ProviderBlocked(
            f"{provider} returned HTTP 451 (unavailable for legal reasons) for this network location"
        )
    response.raise_for_status()


def check_binance_ticker(session: requests.Session) -> dict[str, Any]:
    response = session.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": "BTCUSDT"},
        timeout=(5, 12),
    )
    _raise_for_status(response, "Binance")
    price = float(response.json()["price"])
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"implausible ticker price {price!r}")
    return {"symbol": "BTCUSDT", "price": price, "observed_at": datetime.now(timezone.utc).isoformat()}


def check_candles(session: requests.Session, interval: str, limit: int = 120) -> dict[str, Any]:
    response = session.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "BTCUSDT", "interval": interval, "limit": limit},
        timeout=(5, 15),
    )
    _raise_for_status(response, "Binance")
    raw = response.json()
    if not isinstance(raw, list) or len(raw) < 35:
        raise ValueError(f"expected at least 35 klines, received {len(raw) if isinstance(raw, list) else type(raw).__name__}")
    times = [row[0] / 1000.0 for row in raw]
    spacing = candle_spacing(times, interval)
    if not spacing["spacing_ok"]:
        raise ValueError(f"{interval} spacing was not uniform: {spacing['deltas']}")
    if not spacing["aligned_ok"]:
        raise ValueError(f"{spacing['misaligned_count']} {interval} candles were not aligned")
    invalid = []
    for row in raw[-20:]:
        problems = validate_ohlc({"open": row[1], "high": row[2], "low": row[3], "close": row[4]})
        if problems:
            invalid.append(problems)
    if invalid:
        raise ValueError(f"{len(invalid)} candles had invalid OHLC: {invalid[0]}")
    return {
        "interval": interval,
        "candles": len(raw),
        "expected_seconds": spacing["expected_seconds"],
        "distinct_deltas": spacing["deltas"],
        "first_close": float(raw[0][4]),
        "last_close": float(raw[-1][4]),
        "latest_open_ms": raw[-1][0],
    }


def check_websocket(symbol: str = "BTCUSDT", interval: str = "1m", timeout: float = 20.0) -> dict[str, Any]:
    from websockets.sync.client import connect

    url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@kline_{interval}"
    try:
        with connect(url, open_timeout=timeout, close_timeout=5) as connection:
            deadline = time.time() + timeout
            received = 0
            while time.time() < deadline:
                message = json.loads(connection.recv(timeout=timeout))
                received += 1
                if message.get("e") == "kline":
                    problems = validate_ws_kline(message, symbol, interval)
                    if problems:
                        raise ValueError(f"invalid kline message: {problems}")
                    payload = message["k"]
                    return {
                        "url": url,
                        "messages_received": received,
                        "symbol": payload["s"],
                        "interval": payload["i"],
                        "is_closed": bool(payload.get("x")),
                        "close": float(payload["c"]),
                        "matched_validation": True,
                    }
            raise TimeoutError(f"no kline message within {timeout}s (received {received})")
    except ProviderBlocked:
        raise
    except Exception as exc:  # noqa: BLE001
        if "451" in str(exc):
            raise ProviderBlocked(
                f"Binance rejected the WebSocket with HTTP 451 for this network location: {exc}"
            ) from exc
        raise


def check_coinbase_candles(interval: str, limit: int = 200) -> dict[str, Any]:
    """Verify the fallback provider's real candles are correctly shaped.

    This is the check that can actually run on networks where Binance is
    geo-blocked, and it proves the app's fallback path against live data.
    """
    granularity = COINBASE_GRANULARITIES[interval]
    response = requests.get(
        f"https://api.exchange.coinbase.com/products/BTC-USD/candles",
        params={"granularity": granularity},
        headers={"Accept": "application/json", "User-Agent": "SiddTradingPro-verify/1.0"},
        timeout=(5, 20),
    )
    _raise_for_status(response, "Coinbase")
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 35:
        raise ValueError(f"expected at least 35 rows, received {len(payload) if isinstance(payload, list) else type(payload).__name__}")
    rows = sorted(payload, key=lambda item: item[0])
    times = [float(row[0]) for row in rows]
    spacing = candle_spacing(times, interval)
    if not spacing["spacing_ok"]:
        raise ValueError(f"Coinbase {interval} spacing was not uniform: {spacing['deltas']}")
    if not spacing["aligned_ok"]:
        raise ValueError(f"Coinbase {interval} rows were not aligned")
    invalid = []
    for row in rows[-20:]:
        problems = validate_ohlc({"open": row[3], "high": row[2], "low": row[1], "close": row[4]})
        if problems:
            invalid.append(problems)
    if invalid:
        raise ValueError(f"{len(invalid)} Coinbase candles were invalid: {invalid[0]}")
    return {
        "provider": "coinbase:BTC-USD",
        "interval": interval,
        "candles": len(rows),
        "distinct_deltas": spacing["deltas"],
        "latest_close": float(rows[-1][4]),
    }


def check_news() -> dict[str, Any]:
    from app.news_provider import fetch_crypto_news

    items = fetch_crypto_news("BTCUSDT", datetime.now(timezone.utc).isoformat())
    if not items:
        raise ValueError("provider returned no items (a quiet feed is recorded as a failure here, not fabricated)")
    item = items[0]
    return {
        "items": len(items),
        "sources": sorted({str(entry.get("source")) for entry in items}),
        "first_title": str(item.get("title"))[:120],
        "first_url": str(item.get("url"))[:200],
        "first_published_at": item.get("published_at"),
    }


def make_http_checks(base_url: str) -> list[tuple[str, Callable[[], Any]]]:
    session = requests.Session()

    def get(path: str, **params: Any) -> requests.Response:
        response = session.get(base_url + path, params=params or None, timeout=(5, 90))
        return response

    def health() -> dict[str, Any]:
        response = get("/api/health")
        response.raise_for_status()
        body = response.json()
        if body.get("live_trading_enabled") is not False:
            raise ValueError("live trading is not reported as disabled")
        return body

    def candles_endpoint() -> dict[str, Any]:
        response = get("/api/candles/BTCUSDT", interval="1h", limit=120)
        response.raise_for_status()
        body = response.json()
        times = [candle["time"] for candle in body["candles"]]
        spacing = candle_spacing(times, "1h")
        if not (spacing["spacing_ok"] and spacing["aligned_ok"]):
            raise ValueError(f"1h candles from the API were not uniform: {spacing}")
        return {"interval": body["interval"], "candles": len(times), "deltas": spacing["deltas"]}

    def preview_endpoint() -> dict[str, Any]:
        response = get("/api/forecast/two-minute/BTCUSDT")
        response.raise_for_status()
        body = response.json()
        if body.get("recorded") is not False or body.get("trade_authorized") is not False:
            raise ValueError("preview endpoint is not correctly marked as unrecorded/unauthorized")
        if not body.get("forecast_created_at"):
            raise ValueError("preview did not expose a forecast creation time")
        return {
            "direction": body.get("direction"),
            "forecast_price": body.get("forecast_price"),
            "baseline_price": body.get("baseline_price"),
            "calibrated": body.get("calibrated"),
            "recorded": body.get("recorded"),
            "research_status": body.get("research_status"),
        }

    def agent_pipeline() -> dict[str, Any]:
        response = get("/api/agents/run/BTCUSDT", interval="5m", simulation_count=100)
        response.raise_for_status()
        body = response.json()
        if body.get("action") != "NO TRADE":
            raise ValueError(f"an uncalibrated pipeline produced {body.get('action')!r}")
        stages = {stage["stage"]: stage["status"] for stage in body.get("stages", [])}
        prediction = next(
            (stage for stage in body.get("stages", []) if stage["stage"] == "prediction"), {}
        )
        ledger_id = (prediction.get("result") or {}).get("forecast_ledger_id")

        # If the market-data stage failed, no forecast can honestly exist. That
        # is correct fail-closed behaviour, not a defect: report it as such.
        data_ok = stages.get("market_data") == "completed"
        if data_ok and not ledger_id:
            raise ValueError("market data was available but the pipeline did not freeze a forecast")
        return {
            "status": body.get("status"),
            "action": body.get("action"),
            "stages": stages,
            "forecast_frozen": bool(ledger_id),
            "forecast_ledger_id": ledger_id,
        }

    def ledger_endpoint() -> dict[str, Any]:
        response = get("/api/forecast/ledger", limit=5)
        response.raise_for_status()
        body = response.json()
        return {
            "storage": body.get("storage"),
            "total": body["summary"]["total"],
            "pending": body["summary"]["pending"],
            "corrupt": body["summary"]["corrupt_event_count"],
        }

    def chart_page() -> dict[str, Any]:
        response = get("/chart")
        response.raise_for_status()
        if "/chart-core.js" not in response.text:
            raise ValueError("chart page does not reference its core script")
        return {"bytes": len(response.text)}

    return [
        ("server: /api/health", health),
        ("server: /api/candles 1h uniformity", candles_endpoint),
        ("server: /api/forecast/two-minute preview", preview_endpoint),
        ("server: /api/agents/run freezes a forecast", agent_pipeline),
        ("server: /api/forecast/ledger", ledger_endpoint),
        ("server: /chart page", chart_page),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default=None)
    parser.add_argument("--symbol", default="BTCUSDT")
    arguments = parser.parse_args(argv)

    socket.setdefaulttimeout(20)
    session = requests.Session()
    checks: list[dict[str, Any]] = []

    checks.append(run_check("binance: spot ticker", lambda: check_binance_ticker(session)))
    for interval in ("1m", "5m", "1h", "4h", "1d"):
        checks.append(
            run_check(
                f"binance: {interval} klines uniform and aligned",
                lambda value=interval: check_candles(session, value),
            )
        )
    checks.append(run_check("binance: kline websocket", check_websocket))
    for interval in ("5m", "1h", "1d"):
        checks.append(
            run_check(
                f"coinbase fallback: {interval} klines uniform and aligned",
                lambda value=interval: check_coinbase_candles(value),
            )
        )
    # A quiet or unreachable news feed is reported as a failed check but does
    # not gate the run: research unavailability already forces NO TRADE.
    checks.append(run_check("research: live news provider", check_news, critical=False))
    for name, fn in make_http_checks(arguments.base_url):
        checks.append(run_check(name, fn))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": arguments.base_url,
        "summary": summarize(checks),
        "checks": checks,
    }
    if arguments.out:
        destination = Path(arguments.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    for check in checks:
        marker = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[check["status"]]
        extra = ""
        if check["status"] == "fail":
            extra = " — " + str(check.get("error", ""))[:220]
        elif check["status"] == "pass":
            detail = check.get("detail") or {}
            interesting = {
                key: detail[key]
                for key in ("candles", "distinct_deltas", "direction", "action", "total", "messages_received")
                if key in detail
            }
            extra = " — " + json.dumps(interesting) if interesting else ""
        print(f"[{marker}] {check['name']}{extra}")

    print(json.dumps(report["summary"], indent=2))
    return 0 if report["summary"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
