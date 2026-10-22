"""Tests for structured logging and the operator-visible event tail."""
import json
import os

import pytest

from app import observability


def read_log_file():
    path = observability.log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_an_event_is_written_to_the_file_and_the_ring():
    entry = observability.log_event("info", "test_event", symbol="BTCUSDT", count=3)

    assert entry["level"] == "info"
    assert entry["event"] == "test_event"
    assert entry["symbol"] == "BTCUSDT"
    assert entry["timestamp"].endswith("+00:00")

    on_disk = read_log_file()
    assert len(on_disk) == 1
    assert on_disk[0]["event"] == "test_event"
    assert on_disk[0]["count"] == 3

    recent = observability.recent()
    assert recent[0]["event"] == "test_event"


def test_secret_bearing_fields_are_redacted_before_anything_is_written():
    observability.log_event(
        "info",
        "auth_attempt",
        api_key="sk-live-abcdef123456",
        nested={"access_token": "tok_123", "symbol": "ETHUSDT"},
        password="hunter2",
    )

    written = read_log_file()[0]
    assert written["api_key"] == observability._REDACTED
    assert written["password"] == observability._REDACTED
    assert written["nested"]["access_token"] == observability._REDACTED
    assert written["nested"]["symbol"] == "ETHUSDT"

    raw = observability.log_path().read_text(encoding="utf-8")
    assert "sk-live-abcdef123456" not in raw
    assert "hunter2" not in raw
    assert "tok_123" not in raw


def test_very_long_values_are_truncated():
    observability.log_event("info", "big", detail="x" * 5000)

    written = read_log_file()[0]
    assert len(written["detail"]) < 2100
    assert written["detail"].endswith("…")


def test_recent_returns_newest_first_and_respects_the_limit():
    for index in range(5):
        observability.log_event("info", f"event_{index}")

    newest = observability.recent(limit=2)

    assert [entry["event"] for entry in newest] == ["event_4", "event_3"]


def test_level_filter_returns_that_level_and_worse():
    observability.log_event("info", "info_one")
    observability.log_event("warning", "warn_one")
    observability.log_event("error", "error_one")

    assert [e["event"] for e in observability.recent(level="warning")] == ["error_one", "warn_one"]
    assert [e["event"] for e in observability.recent(level="error")] == ["error_one"]
    assert len(observability.recent(level="debug")) == 3


def test_summary_counts_each_level():
    observability.log_event("info", "a")
    observability.log_event("warning", "b")
    observability.log_event("error", "c")
    summary = observability.summary()

    assert summary["retained"] == 3
    assert summary["counts"]["info"] == 1
    assert summary["counts"]["warning"] == 1
    assert summary["counts"]["error"] == 1
    assert summary["errors"] == 1
    assert summary["warnings"] == 1


def test_the_ring_buffer_is_bounded(monkeypatch):
    monkeypatch.setattr(observability, "RING_SIZE", 5)
    # The deque was built with the real size; rebuild it for the test.
    from collections import deque

    monkeypatch.setattr(observability, "_RING", deque(maxlen=5))
    for index in range(12):
        observability.log_event("info", f"e{index}")

    assert len(observability.recent(limit=100)) == 5
    assert observability.recent(limit=1)[0]["event"] == "e11"


def test_an_unwritable_log_path_never_breaks_the_caller(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("APP_LOG_PATH", str(blocker / "nested" / "app.log"))

    entry = observability.log_event("error", "still_recorded", detail="in memory")

    assert entry["event"] == "still_recorded"
    assert observability.recent(limit=1)[0]["event"] == "still_recorded"


def test_unknown_levels_fall_back_to_info():
    assert observability.log_event("catastrophe", "x")["level"] == "info"


def test_timed_adds_a_duration():
    import time

    started = time.perf_counter()
    entry = observability.timed("info", "slow_thing", started, detail="x")

    assert entry["duration_ms"] >= 0
    assert entry["detail"] == "x"


def test_log_path_can_be_overridden(tmp_path, monkeypatch):
    target = tmp_path / "custom" / "events.log"
    monkeypatch.setenv("APP_LOG_PATH", str(target))

    observability.log_event("info", "custom")

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8").splitlines()[0])["event"] == "custom"


# --------------------------------------------------------------------------
# HTTP integration
# --------------------------------------------------------------------------

def test_requests_are_logged_and_the_endpoint_exposes_them():
    from fastapi.testclient import TestClient

    from app import main

    observability.clear()
    client = TestClient(main.app)
    client.get("/api/health")

    response = client.get("/api/logs/recent?limit=10")
    assert response.status_code == 200
    data = response.json()

    paths = [entry.get("path") for entry in data["entries"]]
    assert "/api/health" in paths
    assert data["summary"]["log_file"].endswith("app.log")
    assert all(entry["level"] in observability.LEVELS for entry in data["entries"])


def test_a_client_error_is_logged_as_a_warning_with_its_status():
    from fastapi.testclient import TestClient

    from app import main

    observability.clear()
    client = TestClient(main.app)
    client.get("/api/candles/BTCUSDT?interval=nope")

    warnings = observability.recent(level="warning")
    match = [entry for entry in warnings if entry.get("path") == "/api/candles/BTCUSDT"]
    assert match, "the failing request must be visible"
    assert match[0]["status"] == 400


def test_an_upstream_failure_is_logged_as_an_error():
    from fastapi.testclient import TestClient

    from app import main
    from app.market import MarketDataError

    observability.clear()
    client = TestClient(main.app)

    def explode(*args, **kwargs):
        raise MarketDataError("Binance unreachable (test fixture)")

    original = main.candles
    main.candles = explode
    try:
        response = client.get("/api/candles/BTCUSDT?interval=5m")
    finally:
        main.candles = original

    assert response.status_code == 502
    errors = [e for e in observability.recent(level="error") if e.get("path") == "/api/candles/BTCUSDT"]
    assert errors and errors[0]["status"] == 502


def test_a_pipeline_run_is_logged_with_its_stage_statuses(monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from app import main

    def fresh_candles(symbol, interval, limit):
        now = time.time()
        return [
            {
                "time": now - (limit - index) * 300,
                "open": 100.0 + index * 0.15 - 0.04,
                "high": 100.0 + index * 0.15 + 0.2,
                "low": 100.0 + index * 0.15 - 0.2,
                "close": 100.0 + index * 0.15,
                "volume": 10.0,
            }
            for index in range(limit)
        ]

    observability.clear()
    client = TestClient(main.app)
    monkeypatch.setattr(main, "candles", fresh_candles)
    monkeypatch.setattr(main, "fetch_crypto_news", lambda symbol, as_of: [])

    response = client.get("/api/agents/run/BTCUSDT?interval=5m")
    assert response.status_code == 200

    events = [e for e in observability.recent(limit=200) if e["event"] == "agent_pipeline"]
    assert len(events) == 1
    logged = events[0]
    assert logged["symbol"] == "BTCUSDT"
    assert logged["action"] == "NO TRADE"
    assert logged["stages"]["prediction"] == "completed"
    # A quiet news day is a degraded stage and must be visible as a warning.
    assert logged["level"] == "warning"
    assert "research" in logged["degraded_stages"]
