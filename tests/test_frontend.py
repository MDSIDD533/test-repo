import re
import shutil
import subprocess
import tempfile
from pathlib import Path


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
        subprocess.run([node, "--check", handle.name], check=True, capture_output=True, text=True)
