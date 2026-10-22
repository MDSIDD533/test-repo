from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "static" / "chart-core.js"
CORE_TESTS = ROOT / "static" / "chart-core.test.js"


def test_chart_core_has_no_dom_dependencies():
    source = CORE.read_text(encoding="utf-8")
    for forbidden in ("document.", "window.", "getElementById"):
        assert forbidden not in source
    assert "module.exports" in source


def test_chart_core_covers_every_supported_interval():
    source = CORE.read_text(encoding="utf-8")
    for interval in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"):
        assert f"'{interval}':" in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_chart_core_javascript_unit_tests_pass():
    checked = subprocess.run(
        ["node", "--check", str(CORE)],
        capture_output=True,
        text=True,
    )
    assert checked.returncode == 0, checked.stderr

    completed = subprocess.run(
        ["node", "--test", str(CORE_TESTS)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "# fail 0" in completed.stdout
