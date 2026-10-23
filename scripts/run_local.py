"""One-command local runner for VS Code (Windows, macOS, Linux).

What it does, in order:
  1. Creates .venv if it is missing and installs requirements.txt.
  2. Checks whether an exchange is reachable and tells you which mode to use.
  3. Starts uvicorn and opens the dashboard in your browser.

Usage (from the repository root, in any terminal):
    python scripts/run_local.py                 # live market data
    python scripts/run_local.py --mode replay   # recorded data, no network
    python scripts/run_local.py --port 9000 --no-browser
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
PROBES = (
    ("Binance", "https://api.binance.com/api/v3/ping"),
    ("Coinbase", "https://api.exchange.coinbase.com/products/BTC-USD"),
)


def venv_python(venv: Path) -> Path:
    if platform.system() == "Windows":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def run(command: list[str], **kwargs) -> None:
    print("+ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run(command, check=True, **kwargs)


def ensure_environment() -> Path:
    python = venv_python(VENV)
    if not python.exists():
        print("Creating .venv ...", flush=True)
        import venv as venv_module

        venv_module.EnvBuilder(with_pip=True).create(str(VENV))
        python = venv_python(VENV)
        run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])

    probe = subprocess.run(
        [str(python), "-c", "import fastapi, uvicorn, requests"],
        capture_output=True,
    )
    if probe.returncode != 0:
        print("Installing requirements (first run only, this takes a minute) ...", flush=True)
        run([str(python), "-m", "pip", "install", "--quiet", "-r", str(REQUIREMENTS)])
    else:
        print("Environment already ready: " + str(python), flush=True)
    return python


def probe_network() -> list[tuple[str, bool, str]]:
    results = []
    for name, url in PROBES:
        try:
            with urllib.request.urlopen(url, timeout=6) as response:
                results.append((name, 200 <= response.status < 400, f"HTTP {response.status}"))
        except urllib.error.HTTPError as exc:
            results.append((name, False, f"HTTP {exc.code}"))
        except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
            results.append((name, False, type(exc).__name__))
    return results


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def wait_for_health(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - server is still starting
            time.sleep(0.5)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "replay"), default="live")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reload", action="store_true", help="auto-reload on file changes")
    arguments = parser.parse_args(argv)

    python = ensure_environment()

    reachable = probe_network()
    print("\nExchange reachability from this machine:", flush=True)
    for name, ok, detail in reachable:
        print(f"  {name:<9} {'reachable' if ok else 'NOT reachable'}  ({detail})", flush=True)

    mode = arguments.mode
    if mode == "live" and not any(ok for _, ok, _ in reachable):
        print(
            "\n  WARNING: no exchange is reachable from this network.\n"
            "  The app will start, but charts will show a data-unavailable error.\n"
            "  To run on the bundled recording instead:  "
            "python scripts/run_local.py --mode replay\n",
            flush=True,
        )
    if mode == "replay":
        fixture = ROOT / "fixtures" / "candles.json"
        if not fixture.exists():
            print(f"Replay mode needs {fixture}, which is missing.", file=sys.stderr)
            return 1
        print(f"\nRunning on the recording at {fixture} — this is NOT live data.", flush=True)

    if not port_is_free(arguments.port):
        print(
            f"Port {arguments.port} is already in use. "
            f"Try: python scripts/run_local.py --port {arguments.port + 1}",
            file=sys.stderr,
        )
        return 1

    environment = dict(os.environ)
    environment["MARKET_DATA_MODE"] = mode
    command = [
        str(python), "-m", "uvicorn", "app.main:app",
        "--host", arguments.host, "--port", str(arguments.port),
    ]
    if arguments.reload:
        command.append("--reload")

    print("\nStarting the server ...", flush=True)
    server = subprocess.Popen(command, cwd=str(ROOT), env=environment)

    if not wait_for_health(arguments.port):
        server.terminate()
        print("The server did not become healthy. See the output above.", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{arguments.port}"
    print(
        f"\n  Ready: {url}\n"
        f"  Mode:  {mode.upper()}\n"
        f"  Chart: {url}/chart\n"
        "  Press Ctrl+C to stop.\n",
        flush=True,
    )
    if not arguments.no_browser:
        import webbrowser

        webbrowser.open(url)

    try:
        return server.wait()
    except KeyboardInterrupt:
        print("\nStopping ...", flush=True)
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
