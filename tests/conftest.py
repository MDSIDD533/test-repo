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
