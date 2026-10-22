"""Tests for the freeze -> wait -> resolve loop used as CI evidence."""
from datetime import datetime, timedelta, timezone

import pytest

from scripts import freeze_and_resolve
from app.forecast_ledger import read_ledger, record_forecast

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def pipeline_payload(target, ledger_id="ledger-1", horizon=60):
    return {
        "status": "completed_no_trade",
        "action": "NO TRADE",
        "stages": [
            {"stage": "market_data", "status": "completed", "result": {}},
            {"stage": "prediction", "status": "completed",
             "result": {"forecast_ledger_id": ledger_id,
                        "forecast_target_time": target.isoformat(),
                        "horizon_seconds": horizon}},
        ],
    }


class FakeClock:
    def __init__(self):
        self.moment = NOW
        self.slept = []

    def now(self):
        return self.moment

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.moment = self.moment + timedelta(seconds=seconds)


def make_get(clock, pipeline, ledger_summaries):
    calls = []
    queue = list(ledger_summaries)
    payloads = pipeline if isinstance(pipeline, list) else [pipeline]

    def get(url, params=None):
        calls.append({"url": url, "params": params})
        if "agents/run" in url:
            item = payloads.pop(0) if len(payloads) > 1 else payloads[0]
            if isinstance(item, Exception):
                raise item
            return item
        return {"summary": queue.pop(0) if queue else {"total": 0, "resolved": 0}}

    get.calls = calls
    return get


def test_it_waits_exactly_until_the_frozen_target_time():
    clock = FakeClock()
    target = NOW + timedelta(seconds=60)
    get = make_get(clock, pipeline_payload(target), [{"total": 0}, {"total": 1, "resolved": 1}])

    report = freeze_and_resolve.freeze_and_resolve(
        "http://test",
        get=get,
        sleep=clock.sleep,
        now=clock.now,
        max_wait_seconds=240,
    )

    # 60s horizon plus the settle margin, not a guess.
    assert clock.slept == [pytest.approx(65.0, abs=0.1)]
    assert report["wait_seconds"] == pytest.approx(65.0, abs=0.1)
    assert report["pipeline"]["forecast_ledger_id"] == "ledger-1"
    assert "error" not in report


def test_a_long_horizon_is_capped_so_the_job_cannot_hang():
    clock = FakeClock()
    target = NOW + timedelta(seconds=10_000)
    get = make_get(clock, pipeline_payload(target), [{"total": 0}, {"total": 1}])

    report = freeze_and_resolve.freeze_and_resolve(
        "http://test", get=get, sleep=clock.sleep, now=clock.now, max_wait_seconds=240
    )

    assert clock.slept == [240]
    assert report["wait_seconds"] == 240


def test_no_frozen_forecast_is_reported_without_waiting():
    clock = FakeClock()
    pipeline = {
        "status": "incomplete",
        "action": "NO TRADE",
        "stages": [
            {"stage": "market_data", "status": "failed", "result": {}},
            {"stage": "prediction", "status": "unavailable", "result": {}},
        ],
    }
    get = make_get(clock, pipeline, [{"total": 0}])

    report = freeze_and_resolve.freeze_and_resolve(
        "http://test", get=get, sleep=clock.sleep, now=clock.now
    )

    assert clock.slept == []
    assert "no forecast was frozen at any configured interval" in report["error"]
    assert report["pipeline_attempts"][0]["stages"]["market_data"]["status"] == "failed"
    # it retried the fallback interval instead of giving up on the first miss
    assert [attempt["interval"] for attempt in report["pipeline_attempts"]] == ["1m", "5m"]


def test_a_ledger_error_from_the_pipeline_is_surfaced():
    clock = FakeClock()
    pipeline = {
        "status": "completed_no_trade",
        "action": "NO TRADE",
        "stages": [
            {"stage": "prediction", "status": "completed",
             "result": {"ledger_error": "disk unavailable"}},
        ],
    }
    get = make_get(clock, pipeline, [{"total": 0}])

    report = freeze_and_resolve.freeze_and_resolve(
        "http://test", get=get, sleep=clock.sleep, now=clock.now
    )

    assert report["pipeline"]["ledger_error"] == "disk unavailable"
    assert "no forecast was frozen" in report["error"]


def test_an_unreachable_server_is_reported_not_raised():
    clock = FakeClock()

    def get(url, params=None):
        raise RuntimeError("connection refused")

    report = freeze_and_resolve.freeze_and_resolve(
        "http://test", get=get, sleep=clock.sleep, now=clock.now
    )

    assert "server is not reachable" in report["error"]
    assert "connection refused" in report["error"]


def test_a_pipeline_request_failure_after_a_successful_ledger_read_is_reported():
    clock = FakeClock()

    def get(url, params=None):
        if "agents/run" in url:
            raise RuntimeError("connection reset")
        return {"summary": {"total": 0}}

    report = freeze_and_resolve.freeze_and_resolve(
        "http://test", get=get, sleep=clock.sleep, now=clock.now
    )

    assert "no forecast was frozen" in report["error"]
    assert "connection reset" in report["pipeline_attempts"][0]["error"]
    assert report["ledger_before"] == {"total": 0}


def test_an_unusable_target_time_is_reported():
    clock = FakeClock()
    pipeline = pipeline_payload(NOW)
    pipeline["stages"][1]["result"]["forecast_target_time"] = "not-a-time"
    get = make_get(clock, pipeline, [{"total": 0}])

    report = freeze_and_resolve.freeze_and_resolve(
        "http://test", get=get, sleep=clock.sleep, now=clock.now
    )

    assert "unusable target time" in report["error"]


def test_it_actually_resolves_a_due_forecast_from_the_ledger(monkeypatch):
    entry = record_forecast(
        {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "forecast_created_at": (NOW - timedelta(minutes=5)).isoformat(),
            "horizon_seconds": 60,
            "reference_price": 100.0,
            "direction": "UP",
            "calibrated": False,
            "model_version": "rule_direction_v1",
        },
        run_id="freeze-resolve",
        symbol="BTCUSDT",
    )

    def fake_observation(symbol, target_time, **kwargs):
        return {
            "price": 101.0,
            "source": "spot_ticker",
            "provider": "coinbase:BTC-USD",
            "observed_at": target_time,
            "target_time": target_time,
            "time_difference_seconds": 0.0,
        }

    monkeypatch.setattr(freeze_and_resolve, "resolve_pending", lambda **kwargs: {
        "due_count": 1, "attempted": 1, "resolved_count": 1,
        "resolved": [{"forecast_id": entry["forecast_id"], "direction_correct": True}],
        "errors": [], "candle_fallback_count": 0,
    })

    clock = FakeClock()
    target = NOW + timedelta(seconds=60)
    get = make_get(clock, pipeline_payload(target), [{"total": 1}, {"total": 1, "resolved": 1}])

    report = freeze_and_resolve.freeze_and_resolve(
        "http://test", get=get, sleep=clock.sleep, now=clock.now
    )

    assert report["resolution"]["resolved_count"] == 1
    assert report["ledger_after"]["resolved"] == 1
    assert any(step["step"] == "resolve_due_forecasts" for step in report["steps"])


def test_it_falls_back_to_a_working_interval_and_records_the_failed_one():
    clock = FakeClock()
    target = NOW + timedelta(seconds=60)
    failed = {
        "status": "failed",
        "action": "NO TRADE",
        "stages": [
            {"stage": "market_data", "status": "failed",
             "errors": ["candles are stale: newest 1m candle is 240s old"]},
            {"stage": "prediction", "status": "unavailable", "result": {}},
        ],
    }
    get = make_get(clock, [failed, pipeline_payload(target)], [{"total": 0}, {"total": 1}])

    report = freeze_and_resolve.freeze_and_resolve(
        "http://test", get=get, sleep=clock.sleep, now=clock.now
    )

    assert report["interval"] == "5m"
    assert "error" not in report
    assert report["pipeline_attempts"][0]["interval"] == "1m"
    assert "stale" in report["pipeline_attempts"][0]["stages"]["market_data"]["errors"][0]
    # the wait is derived from the successful attempt, not the failed one
    assert clock.slept == [pytest.approx(65.0, abs=0.1)]
