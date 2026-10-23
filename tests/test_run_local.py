"""The local runner must fail loudly instead of starting something misleading."""
import socket

import pytest

from scripts import run_local


def test_the_venv_python_path_matches_the_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(run_local.platform, "system", lambda: "Windows")
    assert run_local.venv_python(tmp_path).name == "python.exe"
    assert "Scripts" in str(run_local.venv_python(tmp_path))

    monkeypatch.setattr(run_local.platform, "system", lambda: "Linux")
    assert run_local.venv_python(tmp_path).name == "python"
    assert str(run_local.venv_python(tmp_path)).endswith("bin/python")


def test_port_is_free_reports_a_bound_port_honestly():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        busy = listener.getsockname()[1]

        assert run_local.port_is_free(busy) is False

    assert run_local.port_is_free(busy) is True


def test_probe_network_records_each_provider_instead_of_raising(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(url, timeout=None):
        if "binance" in url:
            raise ConnectionError("blocked")
        return Response()

    monkeypatch.setattr(run_local.urllib.request, "urlopen", fake_urlopen)

    results = dict((name, ok) for name, ok, _ in run_local.probe_network())

    assert results["Binance"] is False
    assert results["Coinbase"] is True


def test_an_http_error_is_not_treated_as_reachable(monkeypatch):
    import urllib.error

    def fake_urlopen(url, timeout=None):
        raise urllib.error.HTTPError(url, 451, "Unavailable For Legal Reasons", {}, None)

    monkeypatch.setattr(run_local.urllib.request, "urlopen", fake_urlopen)

    results = run_local.probe_network()

    assert all(ok is False for _, ok, _ in results)
    assert all("451" in detail for _, _, detail in results)


def test_a_busy_port_stops_the_runner_before_starting_anything(monkeypatch, capsys):
    monkeypatch.setattr(run_local, "ensure_environment", lambda: "/fake/python")
    monkeypatch.setattr(run_local, "probe_network", lambda: [("Binance", True, "HTTP 200")])
    monkeypatch.setattr(run_local, "port_is_free", lambda port: False)

    def no_server(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the server must not be started on a busy port")

    monkeypatch.setattr(run_local.subprocess, "Popen", no_server)

    exit_code = run_local.main(["--mode", "live", "--port", "8000"])

    assert exit_code == 1
    assert "already in use" in capsys.readouterr().err


def test_replay_mode_refuses_to_start_without_a_recording(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run_local, "ensure_environment", lambda: "/fake/python")
    monkeypatch.setattr(run_local, "probe_network", lambda: [])
    monkeypatch.setattr(run_local, "port_is_free", lambda port: True)
    monkeypatch.setattr(run_local, "ROOT", tmp_path)

    exit_code = run_local.main(["--mode", "replay", "--no-browser"])

    assert exit_code == 1
    assert "needs" in capsys.readouterr().err.lower()


def test_live_mode_warns_instead_of_silently_using_the_recording(monkeypatch, capsys):
    """A silent fallback would let someone believe they were seeing live data."""
    monkeypatch.setattr(run_local, "ensure_environment", lambda: "/fake/python")
    monkeypatch.setattr(run_local, "probe_network", lambda: [
        ("Binance", False, "URLError"), ("Coinbase", False, "URLError"),
    ])
    monkeypatch.setattr(run_local, "port_is_free", lambda port: True)

    started = {}

    class FakeServer:
        def __init__(self, command, cwd=None, env=None):
            started["env"] = env
            started["command"] = command

        def wait(self):
            return 0

    monkeypatch.setattr(run_local.subprocess, "Popen", FakeServer)
    monkeypatch.setattr(run_local, "wait_for_health", lambda port, timeout=0: True)

    exit_code = run_local.main(["--mode", "live", "--no-browser"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "WARNING: no exchange is reachable" in out
    # it warns, but it still runs what was asked for
    assert started["env"]["MARKET_DATA_MODE"] == "live"
