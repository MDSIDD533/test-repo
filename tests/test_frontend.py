import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client():
    from app import main

    return TestClient(main.app)


def test_dashboard_inline_javascript_parses():
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    assert scripts, "Dashboard must contain its application script"

    # Node is installed in CI; local developers may not have it on PATH.
    # Skip the syntax-check only when the JavaScript runtime is unavailable.
    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("Node.js is not installed or not available on PATH")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", encoding="utf-8") as handle:
        handle.write("\n".join(scripts))
        handle.flush()
        checked = subprocess.run([node, "--check", handle.name], capture_output=True, text=True)
        if checked.returncode:
            error_lines = checked.stderr.splitlines()
            source_line = error_lines[1] if len(error_lines) > 1 else ""
            caret_line = error_lines[2] if len(error_lines) > 2 else ""
            caret = caret_line.find("^")
            context = source_line[max(0, caret - 100):caret + 100] if caret >= 0 else source_line[:200]
            raise AssertionError(
                "Node.js syntax check failed. Context near parser location: "
                + context + "\\n" + checked.stderr[-500:]
            )


def test_dashboard_shows_the_forecast_ledger_honestly():
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    assert 'id="ledgerPanel"' in html
    assert "Forecasts are frozen before an outcome exists" in html
    assert "append-only" in html
    assert "No frozen forecasts yet" in html
    assert "/api/forecast/ledger" in html
    assert "loadForecastLedger" in html
    # Accuracy must never be presented as skill by default.
    assert "not evidence of skill" in html
    # A missing freeze must be visible in the prediction card, not implied.
    assert "NOT FROZEN" in html


def test_dashboard_surfaces_paper_state_persistence_and_warnings():
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    assert 'id="paperStateNote"' in html
    assert "state_warning" in html
    assert "A restart reloads this record" in html
    assert "not a database" in html


def test_dashboard_shows_the_operator_log_and_the_flat_baseline():
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")

    assert 'id="logsPanel"' in html
    assert "/api/logs/recent" in html
    assert "Secrets are redacted" in html
    assert "not durable across restarts" in html

    # The ledger must compare against always-FLAT rather than report accuracy alone.
    assert "Always-FLAT baseline" in html
    assert "Edge vs always-FLAT" in html
    assert "LOSING to always-FLAT" in html
    assert "not evidence of skill" in html


def test_the_dashboard_shows_the_recorded_batch_with_its_baseline():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert 'id="batchPanel"' in html
    assert "loadBatchSummary" in html
    assert "/api/reports/batch-summary" in html
    # it must show the comparison, not just a headline accuracy
    assert "Majority-class baseline" in html
    assert "95% confidence interval" in html
    assert "no demonstrated skill" in html


def test_batch_summary_endpoint_serves_stored_evidence(client):
    response = client.get("/api/reports/batch-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_live_now"] is False
    assert payload["trials_requested"] == 150
    summary = payload["summary"]
    assert summary["valid_trials"] == 149
    assert summary["direction_accuracy_pct"] == 44.3
    assert summary["baselines"]["majority_class_direction_accuracy_pct"] > summary["direction_accuracy_pct"]
    assert payload["evaluation"]["deployment_decision"] == "DO_NOT_DEPLOY"


def test_batch_summary_reports_a_missing_file_honestly(monkeypatch, client, tmp_path):
    from app import main as main_module

    monkeypatch.setattr(main_module, "ROOT", tmp_path)
    response = client.get("/api/reports/batch-summary")

    assert response.status_code == 404
    assert "No recorded live batch summary" in response.json()["detail"]
