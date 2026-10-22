from datetime import datetime, timezone

import pytest

from app.forecast_ledger import read_ledger, record_forecast, record_resolution
from scripts import resolve_forecasts


def freeze(direction="UP", created="2026-01-01T00:00:00+00:00", reference=100.0, symbol="BTCUSDT"):
    return record_forecast(
        {
            "symbol": symbol,
            "interval": "5m",
            "forecast_created_at": created,
            "horizon_seconds": 300,
            "reference_price": reference,
            "direction": direction,
            "calibrated": False,
            "model_version": "rule_direction_v1",
        },
        run_id="resolve-test",
        symbol=symbol,
    )


def observation(price):
    def fake(symbol, provider, target_time):
        return {
            "price": price,
            "source": "spot_ticker",
            "provider": provider,
            "observed_at": target_time,
            "target_time": target_time,
            "time_difference_seconds": 0.0,
        }

    return fake


def test_only_due_pending_forecasts_are_resolved():
    due = freeze(created="2026-01-01T00:00:00+00:00")
    future = freeze(created="2026-01-01T01:00:00+00:00")

    summary = resolve_forecasts.resolve_pending(
        now=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        observation=observation(101.0),
    )

    assert summary["attempted"] == 1
    assert summary["resolved_count"] == 1
    assert summary["resolved"][0]["forecast_id"] == due["forecast_id"]
    ledger = read_ledger()
    by_id = {item["forecast_id"]: item for item in ledger["forecasts"]}
    assert by_id[due["forecast_id"]]["status"] == "resolved"
    assert by_id[future["forecast_id"]]["status"] == "pending"


def test_dry_run_does_not_write_a_resolution():
    freeze()

    summary = resolve_forecasts.resolve_pending(
        now=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        observation=observation(101.0),
        dry_run=True,
    )

    assert summary["dry_run"] is True
    assert summary["planned_count"] == 1
    assert summary["resolved_count"] == 0
    ledger = read_ledger()
    assert ledger["summary"]["resolved"] == 0
    assert ledger["summary"]["pending"] == 1


def test_a_second_run_does_not_resolve_the_same_forecast_again():
    freeze()
    now = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)

    first = resolve_forecasts.resolve_pending(now=now, observation=observation(101.0))
    second = resolve_forecasts.resolve_pending(now=now, observation=observation(102.0))

    assert first["resolved_count"] == 1
    assert second["attempted"] == 0
    assert second["resolved_count"] == 0
    assert read_ledger()["events"] == 2


def test_provider_errors_are_reported_and_the_forecast_stays_pending():
    entry = freeze()

    def failing(symbol, provider, target_time):
        raise RuntimeError("Binance unavailable")

    summary = resolve_forecasts.resolve_pending(
        now=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        observation=failing,
    )

    assert summary["resolved_count"] == 0
    assert summary["errors"][0]["forecast_id"] == entry["forecast_id"]
    assert "Binance unavailable" in summary["errors"][0]["error"]
    assert read_ledger()["summary"]["pending"] == 1


def test_limit_bounds_how_many_forecasts_are_attempted():
    first = freeze(created="2026-01-01T00:00:00+00:00")
    second = freeze(created="2026-01-01T00:05:00+00:00")

    summary = resolve_forecasts.resolve_pending(
        now=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
        observation=observation(101.0),
        limit=1,
    )

    assert summary["attempted"] == 1
    assert summary["due_count"] == 2
    ledger = read_ledger()
    by_id = {item["forecast_id"]: item for item in ledger["forecasts"]}
    assert by_id[first["forecast_id"]]["status"] == "resolved"
    assert by_id[second["forecast_id"]]["status"] == "pending"


def test_observed_source_and_offset_are_preserved_in_the_resolution():
    entry = freeze(direction="DOWN")

    def candle_fallback(symbol, provider, target_time):
        return {
            "price": 99.0,
            "source": "nearest_completed_1m_candle_close",
            "provider": provider,
            "observed_at": target_time - 30.0,
            "target_time": target_time,
            "time_difference_seconds": 30.0,
        }

    resolve_forecasts.resolve_pending(
        now=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        observation=candle_fallback,
    )

    resolution = read_ledger()["forecasts"][0]["resolution"]
    assert resolution["source"] == "nearest_completed_1m_candle_close"
    assert resolution["time_difference_seconds"] == 30.0
    assert resolution["direction_correct"] is True


def test_main_returns_non_zero_when_an_observation_fails(monkeypatch, capsys):
    freeze()

    monkeypatch.setattr(
        resolve_forecasts,
        "resolve_pending",
        lambda **kwargs: {
            "checked_at": "2026-01-01T00:10:00+00:00",
            "dry_run": False,
            "due_count": 1,
            "attempted": 1,
            "resolved_count": 0,
            "planned_count": 0,
            "resolved": [],
            "errors": [{"forecast_id": "x", "symbol": "BTCUSDT", "error": "boom"}],
        },
    )
    assert resolve_forecasts.main(["--limit", "1"]) == 1
    assert '"errors"' in capsys.readouterr().out


def test_the_run_summary_includes_the_flat_baseline(monkeypatch, tmp_path, capsys):
    entry = freeze()
    record_resolution(entry["forecast_id"], observed_price=101.0)
    output = tmp_path / "resolution.json"

    code = resolve_forecasts.main(["--out", str(output)])

    assert code == 0
    import json

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["ledger_summary"]["resolved"] == 1
    assert written["ledger_summary"]["direction_accuracy_pct"] == 100.0
    assert written["ledger_summary"]["always_flat_accuracy_pct"] is not None
    assert written["ledger_summary"]["sample_is_small"] is True
