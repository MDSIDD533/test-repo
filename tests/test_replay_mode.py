"""Replay mode must serve real recorded candles and never pretend to be live."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import market
from app.market_data_agent import CandleValidationError, make_market_data_agent, validate_candles

ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def write_fixture(path, intervals=None, symbol="BTCUSDT"):
    intervals = intervals or {
        "1m": [[1760000000 + i * 60, 100.0, 101.0, 99.0, 100.5, 1.0] for i in range(40)],
        "1h": [[1760000000 + i * 3600, 100.0, 101.0, 99.0, 100.5, 1.0] for i in range(40)],
    }
    payload = {
        "symbol": symbol,
        "fetched_at": "2026-05-01T11:00:00+00:00",
        "newest_candle": "2026-05-01T11:59:00+00:00",
        "providers": ["coinbase:BTC-USD"],
        "intervals": intervals,
        "failures": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


@pytest.fixture()
def replay(monkeypatch, tmp_path):
    fixture_path = tmp_path / "candles.json"
    write_fixture(fixture_path)
    monkeypatch.setattr(market, "REPLAY_MODE", True)
    monkeypatch.setattr(market, "REPLAY_FIXTURE", fixture_path)
    market._REPLAY_CACHE.clear()
    market._PROVIDER_USED.clear()
    yield fixture_path
    market._REPLAY_CACHE.clear()
    market._PROVIDER_USED.clear()


def test_replay_serves_recorded_rows_with_their_source(replay):
    rows = market.candles("BTCUSDT", "1m", 30)

    assert len(rows) == 30
    assert rows[-1]["close"] == 100.5
    assert market.provider_used("BTCUSDT", "1m") == "replay:BTCUSDT@1m"


def test_replay_metadata_states_it_is_not_live(replay):
    metadata = market.replay_metadata()

    assert metadata["mode"] == "replay"
    assert metadata["available"] is True
    assert "Not live" in metadata["note"]
    assert metadata["providers"] == ["coinbase:BTC-USD"]


def test_derived_intervals_are_labelled_and_resampled_not_invented(replay, monkeypatch):
    monkeypatch.setitem(market.REPLAY_DERIVED, "4h", "1h")

    rows = market.candles("BTCUSDT", "4h", 10)

    assert market.provider_used("BTCUSDT", "4h") == "replay:BTCUSDT@1h->4h"
    steps = {rows[i + 1]["time"] - rows[i]["time"] for i in range(len(rows) - 1)}
    assert steps == {14400}
    # aggregation keeps the real high/low envelope of the underlying candles
    assert rows[0]["open"] == 100.0
    assert rows[0]["high"] == 101.0
    assert rows[0]["volume"] == pytest.approx(4.0)


def test_an_interval_with_no_source_is_refused(replay):
    with pytest.raises(market.MarketDataError, match="no 5m candles"):
        market.candles("BTCUSDT", "5m", 30)


def test_a_missing_recording_is_an_error_not_silent_live_data(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "REPLAY_MODE", True)
    monkeypatch.setattr(market, "REPLAY_FIXTURE", tmp_path / "missing.json")
    market._REPLAY_CACHE.clear()

    with pytest.raises(market.MarketDataError, match="no recording was found"):
        market.candles("BTCUSDT", "1m", 30)


def test_a_symbol_outside_the_recording_is_refused(replay):
    with pytest.raises(market.MarketDataError, match="only contains BTCUSDT"):
        market.candles("ETHUSDT", "1m", 30)


def test_live_mode_is_untouched(monkeypatch):
    monkeypatch.setattr(market, "REPLAY_MODE", False)

    assert market.replay_metadata() == {"mode": "live"}


def test_stale_candles_still_fail_outside_replay():
    rows = [
        {"time": NOW - 3600 + i * 60, "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "volume": 1.0}
        for i in range(40)
    ]

    with pytest.raises(CandleValidationError, match="stale"):
        validate_candles(rows, "1m", now=NOW)

    # the same payload is analysable only when replay is explicitly declared
    result = validate_candles(rows, "1m", now=NOW, replay=True)
    assert result["replay"] is True
    assert result["freshness_skipped"] is True
    assert result["live_data"] is False
    # 40 candles of 1m ending an hour before "now" is ~21 minutes stale
    assert result["stale_by_seconds"] > 1200
    assert "freshness_skipped_for_replay" in result["checks"]


def test_the_agent_marks_replayed_data_and_warns():
    rows = [
        {"time": NOW - 3600 + i * 60, "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "volume": 1.0}
        for i in range(40)
    ]
    agent = make_market_data_agent(
        lambda symbol, interval, limit: rows,
        interval="1m",
        limit=40,
        clock=lambda: NOW,
        replay=True,
    )

    result = agent({"symbol": "BTCUSDT"})

    assert result["validation"]["replay"] is True
    assert result["evidence"][0]["source"] == "replayed_recording"
    assert any("REPLAY" in warning for warning in result["warnings"])


def test_the_agent_still_rejects_stale_data_by_default():
    rows = [
        {"time": NOW - 3600 + i * 60, "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.5, "volume": 1.0}
        for i in range(40)
    ]
    agent = make_market_data_agent(
        lambda symbol, interval, limit: rows, interval="1m", limit=40, clock=lambda: NOW
    )

    with pytest.raises(CandleValidationError, match="stale"):
        agent({"symbol": "BTCUSDT"})


def test_scanner_is_limited_to_the_recording_and_says_so(replay, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "REPLAY_MODE", True)
    response = TestClient(main.app).get("/api/scanner")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_mode"] == "replay"
    assert "REPLAY" in payload["note"]
    assert payload["total_pairs"] == 1
    assert payload["returned"][0]["symbol"] == "BTCUSDT"
    assert payload["returned"][0]["replay"] is True


def test_replay_tickers_are_derived_from_the_recording(replay):
    rows = market.replay_tickers()

    assert len(rows) == 1
    assert rows[0]["price"] == 100.5
    # the renderer expects the same keys the live ticker endpoint returns
    assert set(rows[0]) >= {"symbol", "price", "change_percent", "quote_volume", "high", "low"}
    assert rows[0]["high"] == 101.0
    assert rows[0]["low"] == 99.0
    assert rows[0]["window_hours"] == 40


def test_the_stream_endpoint_refuses_to_pretend_a_recording_is_live(monkeypatch):
    """Replay mode must never open a live stream, even if a client asks for one."""
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "REPLAY_MODE", True)
    client = TestClient(main.app)

    with client.websocket_connect("/ws/candles?symbol=BTCUSDT&interval=5m") as websocket:
        message = websocket.receive()
        # Starlette reports the server's close frame; no candle may ever arrive.
        assert message["type"] == "websocket.close"
        assert "Replay mode" in message.get("reason", "")
        assert message.get("code") == 1008


def test_the_stream_endpoint_rejects_an_unsupported_interval(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "REPLAY_MODE", False)
    client = TestClient(main.app)

    with client.websocket_connect("/ws/candles?symbol=BTCUSDT&interval=7m") as websocket:
        message = websocket.receive()
        assert message["type"] == "websocket.close"
        assert message.get("code") == 1008


def test_paper_refresh_uses_the_recording_instead_of_failing(replay, monkeypatch):
    """A 502 here made the dashboard show a console error during replay."""
    from fastapi.testclient import TestClient
    from app import main

    monkeypatch.setattr(main, "REPLAY_MODE", True)
    response = TestClient(main.app).post("/api/paper/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["updated"] is True
    assert payload["account"]["equity"] > 0


def test_the_replay_banner_spans_the_page_grid():
    """As a plain grid child the banner took column 1 and squeezed the chart."""
    html = (ROOT / "static" / "chart.html").read_text(encoding="utf-8")

    start = html.index('id="replayBanner"')
    tag = html[start:start + 260]
    assert "grid-column:1/-1" in tag
    assert "align-self:start" in tag


def test_the_chart_is_resized_when_the_layout_changes():
    html = (ROOT / "static" / "chart.html").read_text(encoding="utf-8")

    assert "applyOptions({ width: host.clientWidth, height: host.clientHeight })" in html
    assert "window.addEventListener('resize'" in html
