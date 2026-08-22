"""
Integration tests for the application entry point.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from songhive.api.app import create_app
from songhive.app import _build_tornado_app


def _write_config(tmp_path: Path, port: int) -> Path:
    """Create a minimal temporary config for the server subprocess."""
    config = tmp_path / "config.toml"
    secret_key = "a" * 64
    config_text = f"""[server]
host = "127.0.0.1"
port = {port}
debug = false
cors_origins = []

[database]
url = "sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"
pool_size = 1
max_overflow = 1

[redis]
url = "redis://localhost:6379/0"

[federation]
enabled = false

[auth]
secret_key = "{secret_key}"
"""
    config.write_text(config_text)
    return config


def _wait_for_port_file(port_file: Path, timeout: float = 5.0) -> int:
    """Wait for the server to write its bound port to *port_file*."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_file.exists():
            text = port_file.read_text(encoding="utf-8").strip()
            if text:
                return int(text)
        time.sleep(0.05)
    raise AssertionError(f"Server did not write bound port to {port_file}")


def _wait_for_listen(host: str, port: int, timeout: float = 5.0) -> None:
    """Wait until the server is listening on the given host/port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"Server did not start listening on {host}:{port}")


def _send_probe(host: str, port: int, timeout: float = 5.0) -> None:
    """Send a minimal HTTP request to confirm the server is handling traffic."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        s.recv(4096)


def _raise_with_output(exc: Exception, log_file: Path) -> None:
    """Re-raise *exc*, appending captured server output if available."""
    if log_file.exists():
        output = log_file.read_text(encoding="utf-8", errors="replace")
        if output:
            raise AssertionError(f"{exc}\n\nServer output:\n{output}") from exc
    raise exc


def test_build_tornado_app_settings(config, fake_redis):
    """_build_tornado_app passes config and redis through Tornado settings."""
    fastapi_app = create_app(config)
    fastapi_app.state.redis = fake_redis

    tornado_app = _build_tornado_app(config, fastapi_app)

    assert tornado_app.settings["config"] is config
    assert tornado_app.settings["redis"] is fake_redis


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_tornado_stops_promptly_on_signal(tmp_path, sig):
    """The Tornado server should exit quickly on SIGINT and SIGTERM."""
    port_file = tmp_path / "port.txt"
    log_file = tmp_path / "server.log"
    config = _write_config(tmp_path, 0)
    env = {**os.environ, "SONGHIVE_WRITE_PORT_TO": str(port_file)}

    proc = None
    try:
        with log_file.open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "songhive", "--config", str(config)],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )

        port = _wait_for_port_file(port_file)
        _wait_for_listen("127.0.0.1", port)
        _send_probe("127.0.0.1", port)

        sent = time.monotonic()
        proc.send_signal(sig)
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired as exc:
            raise AssertionError(f"Server did not exit on signal {sig}") from exc

        elapsed = time.monotonic() - sent
        assert elapsed < 5.0, f"Server took {elapsed:.2f}s to stop"
        assert proc.returncode == 0, f"Server exited with code {proc.returncode}"
    except (AssertionError, subprocess.TimeoutExpired) as exc:
        _raise_with_output(exc, log_file)
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
