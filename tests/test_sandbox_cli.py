"""`cooperation-lab`: binds this computer only by default, uses the home data folder, falls back to a free port."""
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from sandbox import cli

ROOT = Path(__file__).resolve().parents[1]


def _health(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
        return json.load(r)


def test_defaults_are_local_and_flags_parse():
    a = cli.parse_args([])
    assert a.host == "127.0.0.1" and a.port is None and a.no_browser is False
    b = cli.parse_args(["--host", "0.0.0.0", "--port", "9000", "--no-browser"])
    assert (b.host, b.port, b.no_browser) == ("0.0.0.0", 9000, True)
    with pytest.raises(SystemExit):
        cli.parse_args(["--port", "70000"])


def test_data_dir_is_home_or_override(tmp_path):
    assert cli.data_dir({}) == Path.home() / ".cooperation-lab"
    assert cli.data_dir({cli.HOME_ENV: str(tmp_path)}) == tmp_path


def test_taken_default_port_falls_back_to_a_free_one():
    class FakeServer:
        def __init__(self, address, handler):
            if address[1] == cli.DEFAULT_PORT:
                raise OSError(cli.errno.EADDRINUSE, "taken")
            self.server_address = address
    assert cli.bind(FakeServer, None, "127.0.0.1", None).server_address[1] == 0
    with pytest.raises(OSError):  # an explicitly requested port is not silently replaced
        cli.bind(FakeServer, None, "127.0.0.1", cli.DEFAULT_PORT)


def test_command_serves_on_loopback_and_keeps_data_out_of_the_package(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**__import__("os").environ, cli.HOME_ENV: str(tmp_path), "PYTHONPATH": str(ROOT)}
    env.pop("PUBLIC_DEMO", None)
    proc = subprocess.Popen([sys.executable, "-m", "sandbox.cli", "--port", str(port), "--no-browser"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                assert _health(port) == {"status": "ok"}
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("the server did not start")
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/workbench/overview")
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200
        assert (tmp_path / "workbench.db").exists()  # the store opened in the home data folder
        # Reachable on loopback only: no connection on this machine's other addresses.
        names = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), port, socket.AF_INET)} - {"127.0.0.1"}
        for addr in names:
            with socket.socket() as s:
                s.settimeout(0.5)
                assert s.connect_ex((addr, port)) != 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_installed_app_makes_one_lab_secret_and_keeps_it(tmp_path, monkeypatch):
    from lab import evaluation
    monkeypatch.delenv(evaluation.SECRET_ENV, raising=False)
    cli.ensure_lab_secret(tmp_path, evaluation)
    first = __import__("os").environ[evaluation.SECRET_ENV]
    assert len(first) == 64 and evaluation.ready() is None
    monkeypatch.delenv(evaluation.SECRET_ENV)
    cli.ensure_lab_secret(tmp_path, evaluation)
    assert __import__("os").environ[evaluation.SECRET_ENV] == first
