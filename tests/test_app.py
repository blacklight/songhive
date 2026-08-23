"""
Integration tests for the application entry point.
"""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

import songhive.app as app_module
from songhive.api.app import create_app
from songhive.app import _build_tornado_app, _run_tornado, _run_uvicorn, main
from songhive.config.schema import SonghiveConfig


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


class _FakeLoop:
    """Minimal asyncio loop stand-in for _run_tornado tests."""

    def __init__(self, raise_on_signal=False):
        self.signal_handlers = {}
        self.raise_on_signal = raise_on_signal
        self.run_forever_called = False
        self.stopped = False
        self.closed = False
        self.ran_until_complete = []

    def add_signal_handler(self, sig, callback):
        if self.raise_on_signal:
            raise RuntimeError("signal handlers not supported")
        self.signal_handlers[sig] = callback

    def run_forever(self):
        self.run_forever_called = True

    def run_until_complete(self, coro):
        self.ran_until_complete.append(coro)

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _FakeSocket:
    def getsockname(self):
        return ("127.0.0.1", 12345)


class _FakeHTTPServer:
    def __init__(self, app, **kwargs):
        self.app = app
        self._sockets = {0: _FakeSocket()}
        self.port = None
        self.address = None
        self.stopped = False

    def listen(self, port, address=None):
        self.port = port
        self.address = address

    def stop(self):
        self.stopped = True


def _fake_create_app(config: SonghiveConfig):
    app = SimpleNamespace()
    app.state = SimpleNamespace(redis=None, config=config)
    return app


@pytest.fixture
def _minimal_config(tmp_path):
    """Return a minimal config backed by a throw-away database."""
    return SonghiveConfig(
        server={
            "host": "127.0.0.1",
            "port": 8000,
            "debug": True,
            "cors_origins": [],
        },
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"},
        federation={"enabled": False},
        auth={"secret_key": "a" * 64},
    )


def test_run_tornado(monkeypatch, tmp_path, _minimal_config):
    """Test _run_tornado binding, port file, signal handlers, and shutdown."""
    port_file = tmp_path / "port.txt"
    monkeypatch.setenv("SONGHIVE_WRITE_PORT_TO", str(port_file))
    monkeypatch.setattr("songhive.api.app.create_app", _fake_create_app)
    monkeypatch.setattr("tornado.httpserver.HTTPServer", _FakeHTTPServer)
    monkeypatch.setattr(app_module, "_build_tornado_app", lambda cfg, fa: fa)
    monkeypatch.setattr(app_module, "get_redis_client", lambda cfg: "redis")
    monkeypatch.setattr(app_module, "close_redis_client", lambda: None)

    fake_loop = _FakeLoop()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: fake_loop)

    _run_tornado(_minimal_config)

    assert port_file.read_text() == "12345"
    assert fake_loop.run_forever_called
    assert signal.SIGINT in fake_loop.signal_handlers
    assert signal.SIGTERM in fake_loop.signal_handlers

    fake_loop.signal_handlers[signal.SIGINT]()
    assert fake_loop.stopped


def test_run_tornado_signal_fallback(monkeypatch, tmp_path, _minimal_config):
    """Test the signal.signal fallback when add_signal_handler is unsupported."""
    recorded = []

    def fake_signal_handler(sig, handler):
        recorded.append((sig, handler))

    monkeypatch.setattr("songhive.api.app.create_app", _fake_create_app)
    monkeypatch.setattr("tornado.httpserver.HTTPServer", _FakeHTTPServer)
    monkeypatch.setattr(app_module, "_build_tornado_app", lambda cfg, fa: fa)
    monkeypatch.setattr(app_module, "get_redis_client", lambda cfg: "redis")
    monkeypatch.setattr(app_module, "close_redis_client", lambda: None)
    monkeypatch.setattr(app_module.signal, "signal", fake_signal_handler)

    fake_loop = _FakeLoop(raise_on_signal=True)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: fake_loop)

    _run_tornado(_minimal_config)

    assert len(recorded) == 2
    for _, handler in recorded:
        handler()
    assert fake_loop.stopped


def test_run_uvicorn(monkeypatch, _minimal_config):
    """Test _run_uvicorn invokes uvicorn with the correct log level."""
    calls = []

    def fake_uvicorn_run(app, **kwargs):
        calls.append((app, kwargs))

    monkeypatch.setattr("songhive.api.app.create_app", _fake_create_app)
    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    _run_uvicorn(_minimal_config)

    assert len(calls) == 1
    app, kwargs = calls[0]
    assert app is not None
    assert app.state.config is _minimal_config
    assert kwargs["host"] == _minimal_config.server.host
    assert kwargs["port"] == _minimal_config.server.port
    assert kwargs["log_level"] == "debug"


def test_main_without_admin(monkeypatch, _minimal_config):
    """Test main() dispatching to the Tornado path."""
    calls = []

    def fake_run_tornado(config):
        calls.append(("tornado", config))

    def fake_run_uvicorn(config):
        pytest.fail("uvicorn should not be called when a2wsgi is available")

    monkeypatch.setattr(app_module, "load_config", lambda: _minimal_config)
    monkeypatch.setattr(app_module, "init_db", lambda url: None)
    monkeypatch.setattr(app_module, "_run_tornado", fake_run_tornado)
    monkeypatch.setattr(app_module, "_run_uvicorn", fake_run_uvicorn)
    monkeypatch.setattr(sys, "argv", ["songhive"])

    main()

    assert calls == [("tornado", _minimal_config)]


def test_main_admin_subcommand(monkeypatch):
    """Test main() dispatching to the admin CLI."""
    calls = []

    def fake_admin_main(argv):
        calls.append(argv)

    monkeypatch.setattr("songhive.cli.admin.admin_main", fake_admin_main)
    monkeypatch.setattr(sys, "argv", ["songhive", "admin", "init-db"])

    main()

    assert calls == [["init-db"]]


def test_main_import_error_fallback(monkeypatch, _minimal_config, tmp_path):
    """Test main() falling back to uvicorn when a2wsgi cannot be imported."""
    calls = []

    def fake_run_uvicorn(config):
        calls.append(("uvicorn", config))

    def fake_run_tornado(config):
        pytest.fail("tornado should not be called when a2wsgi is unavailable")

    monkeypatch.setattr(app_module, "load_config", lambda: _minimal_config)
    monkeypatch.setattr(app_module, "init_db", lambda url: None)
    monkeypatch.setattr(app_module, "_run_tornado", fake_run_tornado)
    monkeypatch.setattr(app_module, "_run_uvicorn", fake_run_uvicorn)
    monkeypatch.setattr("songhive.api.app.create_app", _fake_create_app)
    monkeypatch.setattr(sys, "argv", ["songhive"])

    # Force the runtime a2wsgi import in main() to fail.
    import builtins

    orig_import = builtins.__import__
    orig_a2wsgi = sys.modules.pop("a2wsgi", None)

    def _fake_import(name, *args, **kwargs):
        if name == "a2wsgi" or name.startswith("a2wsgi."):
            raise ImportError("a2wsgi is not available")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    try:
        main()
    finally:
        if orig_a2wsgi is not None:
            sys.modules["a2wsgi"] = orig_a2wsgi

    assert calls == [("uvicorn", _minimal_config)]
