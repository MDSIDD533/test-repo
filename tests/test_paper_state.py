"""Restart and durability tests for the paper account.

These prove the paper record survives a process restart and that a damaged
state file is reported rather than silently replaced by a fresh balance.
"""
import importlib
import json
import os

import pytest

from app import paper


SIGNAL = {
    "signal": "BUY",
    "entry": 100.0,
    "sl": 99.0,
    "tp1": 102.0,
    "tp2": 103.0,
    "tp3": 104.0,
}


def restart_process():
    """Simulate a new process importing the module again."""
    importlib.reload(paper)
    return paper


def test_opening_a_trade_writes_the_state_file():
    paper.open_trade({**SIGNAL, "symbol": "SAVEUSDT"})

    path = paper.state_path()
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == paper.STATE_VERSION
    assert payload["equity"] == pytest.approx(paper.account()["equity"], abs=0.01)
    assert [trade["symbol"] for trade in payload["trades"]] == ["SAVEUSDT"]
    assert payload["trades"][0]["status"] == "OPEN"


def test_closing_a_trade_writes_the_realized_outcome():
    paper.open_trade({**SIGNAL, "symbol": "CLOSEUSDT"})
    paper.update_prices({"CLOSEUSDT": 102.0})

    payload = json.loads(paper.state_path().read_text(encoding="utf-8"))
    trade = payload["trades"][0]
    assert trade["status"] == "CLOSED"
    assert trade["exit_reason"] == "TP1"
    assert trade["pnl"] > 0


def test_restart_restores_equity_trades_and_realized_pnl():
    paper.open_trade({**SIGNAL, "symbol": "ETHUSDT"})
    paper.update_prices({"ETHUSDT": 102.0})
    before = paper.account()
    before_trades = paper.list_trades()

    restarted = restart_process()
    after = restarted.account()

    assert after["equity"] == before["equity"]
    assert after["realized_pnl"] == before["realized_pnl"]
    assert after["fees_paid"] == before["fees_paid"]
    assert after["closed_trades"] == 1
    assert after["open_trades"] == 0
    assert [trade["symbol"] for trade in restarted.list_trades()] == ["ETHUSDT"]
    assert restarted.list_trades()[0]["pnl"] == before_trades[0]["pnl"]
    assert after["state_warning"] is None


def test_restart_keeps_open_positions_and_trade_ids_continue():
    # A small notional keeps cash available for the second position.
    cheap = {"signal": "BUY", "entry": 10.0, "sl": 9.0, "tp1": 10.2, "tp2": 10.3, "tp3": 10.4}
    first = paper.open_trade({**cheap, "symbol": "AAAUSDT"})

    restarted = restart_process()
    assert restarted.account()["open_trades"] == 1
    second = restarted.open_trade({**cheap, "symbol": "BBBUSDT"})

    assert first["id"] == 1
    assert second["id"] == 2
    assert {trade["symbol"] for trade in restarted.list_trades()} == {"AAAUSDT", "BBBUSDT"}


def test_a_missing_state_file_starts_a_fresh_account_without_a_warning():
    info = paper.reload_state()

    assert info["trades"] == 0
    assert info["warning"] is None
    assert paper.account()["equity"] == paper.STARTING_BALANCE
    assert paper.account()["state_warning"] is None


def test_state_file_saved_before_a_restart_is_reported_as_the_source():
    paper.open_trade({**SIGNAL, "symbol": "SRCUSDT"})
    path = paper.state_path()

    restarted = restart_process()

    assert restarted.account()["state_file"] == str(path)
    assert restarted.account()["persisted"] is True


def test_corrupt_state_is_quarantined_and_reported_instead_of_silently_reset():
    path = paper.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    restarted = restart_process()
    account = restarted.account()

    assert account["equity"] == paper.STARTING_BALANCE
    assert account["open_trades"] == 0
    assert account["closed_trades"] == 0
    assert "could not be loaded" in account["state_warning"]
    assert "preserved" in account["state_warning"]
    quarantined = list(path.parent.glob("paper_state.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{not valid json"


@pytest.mark.parametrize(
    "payload, reason",
    [
        ("[]", "state must be a JSON object"),
        ('{"equity": "lots", "trades": []}', "equity must be a number"),
        ('{"equity": -5, "trades": []}', "equity must be finite and non-negative"),
        ('{"equity": 1000, "trades": {}}', "trades must be a list"),
        ('{"equity": 1000, "trades": [7]}', "trade 0 must be an object"),
        ('{"equity": 1000, "trades": [{"id": 1}]}', "is missing"),
        (
            '{"equity": 1000, "trades": [{"id": 1, "symbol": "X", "entry": 1, "quantity": 1, "sl": 1, "tp1": 2, "status": "HALF"}]}',
            "unknown status",
        ),
        (
            '{"equity": 1000, "trades": [{"id": 1, "symbol": "X", "entry": -1, "quantity": 1, "sl": 1, "tp1": 2, "status": "OPEN"}]}',
            "must be positive",
        ),
    ],
)
def test_invalid_state_files_are_rejected_rather_than_trusted(payload, reason):
    path = paper.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")

    restarted = restart_process()

    assert reason in restarted.account()["state_warning"]
    assert restarted.account()["equity"] == paper.STARTING_BALANCE
    assert restarted.list_trades() == []


def test_a_write_failure_is_surfaced_but_the_in_memory_account_stays_usable(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("PAPER_STATE_PATH", str(blocker / "nested" / "paper.json"))

    trade = paper.open_trade({**SIGNAL, "symbol": "FAILUSDT"})
    account = paper.account()

    assert trade["symbol"] == "FAILUSDT"
    assert len(paper.list_trades()) == 1
    assert "could not be saved" in account["state_warning"]


def test_state_is_not_rewritten_when_no_trade_closes(tmp_path):
    paper.open_trade({**SIGNAL, "symbol": "NOOPUSDT"})
    path = paper.state_path()
    before = path.read_text(encoding="utf-8")

    closed = paper.update_prices({"NOOPUSDT": 100.5})

    assert closed == 0
    assert path.read_text(encoding="utf-8") == before


def test_writes_are_atomic_and_leave_no_temporary_files_behind():
    paper.open_trade({**SIGNAL, "symbol": "ATOMICUSDT"})
    paper.update_prices({"ATOMICUSDT": 102.0})

    leftovers = [
        name for name in os.listdir(paper.state_path().parent) if name.endswith(".tmp")
    ]
    assert leftovers == []
