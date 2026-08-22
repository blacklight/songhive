"""
Integration tests for the application entry point.
"""

import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from songhive.api.app import create_app
from songhive.app import _build_tornado_app


def _free_port() -> int:
    """Return an ephemeral TCP port that is currently free."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    port = _free_port()
    config = _write_config(tmp_path, port)

    proc = subprocess.Popen(
        [sys.executable, "-m", "songhive", "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_listen("127.0.0.1", port)
        _send_probe("127.0.0.1", port)

        sent = time.monotonic()
        proc.send_signal(sig)
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            raise AssertionError(f"Server did not exit on signal {sig}") from None

        elapsed = time.monotonic() - sent
        assert elapsed < 1.0, f"Server took {elapsed:.2f}s to stop"
        assert proc.returncode == 0, f"Server exited with code {proc.returncode}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
