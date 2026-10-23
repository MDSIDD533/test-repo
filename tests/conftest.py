import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def isolated_forecast_ledger(monkeypatch):
    """Keep tests from writing forecast records into the repository.

    The ledger is append-only, so a test run must never share state with the
    real runtime file or with another test.
    """
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setenv(
            "FORECAST_LEDGER_PATH", os.path.join(directory, "forecast_ledger.jsonl")
        )
        yield


@pytest.fixture(autouse=True)
def isolated_event_log(monkeypatch):
    """Keep structured events out of the repository and out of other tests."""
    from app import observability

    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setenv("APP_LOG_PATH", os.path.join(directory, "app.log"))
        observability.clear()
        yield
        observability.clear()


@pytest.fixture(autouse=True)
def isolated_paper_state(monkeypatch):
    """Give every test a private paper account file and a clean in-memory state."""
    from app import paper

    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setenv(
            "PAPER_STATE_PATH", os.path.join(directory, "paper_state.json")
        )
        paper.reload_state()
        yield
