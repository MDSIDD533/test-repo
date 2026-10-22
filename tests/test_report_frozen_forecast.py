import json

import pytest

from scripts import report_frozen_forecast as reporter


def write(tmp_path, payload):
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_reports_the_frozen_forecast_and_target(tmp_path, capsys):
    path = write(
        tmp_path,
        {
            "run_id": "r1",
            "status": "completed_no_trade",
            "action": "NO TRADE",
            "stages": [
                {
                    "stage": "prediction",
                    "status": "completed",
                    "result": {
                        "forecast_ledger_id": "abc-123",
                        "horizon_seconds": 60,
                        "forecast_target_time": "2026-01-01T00:01:00+00:00",
                    },
                }
            ],
        },
    )

    assert reporter.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "frozen forecast: abc-123" in out
    assert "horizon seconds: 60" in out
    assert "action: NO TRADE" in out


def test_reports_none_when_no_forecast_was_frozen(tmp_path, capsys):
    path = write(tmp_path, {"stages": [{"stage": "prediction", "status": "unavailable", "result": {}}]})

    assert reporter.main([str(path)]) == 0
    assert "frozen forecast: NONE" in capsys.readouterr().out


def test_surfaces_a_ledger_error(tmp_path, capsys):
    path = write(
        tmp_path,
        {"stages": [{"stage": "prediction", "result": {"ledger_error": "disk unavailable"}}]},
    )

    reporter.main([str(path)])

    assert "ledger error: disk unavailable" in capsys.readouterr().out


def test_a_missing_or_unreadable_file_is_not_a_crash(tmp_path, capsys):
    assert reporter.main([str(tmp_path / "missing.json")]) == 0
    assert "could not read" in capsys.readouterr().out

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert reporter.main([str(broken)]) == 0
