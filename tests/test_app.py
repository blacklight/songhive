"""
Integration tests for the application entry point.
"""

import asyncio
import signal
import sys
from types import SimpleNamespace

import pytest
import uvicorn

import songhive.app as app_module
from songhive.api.app import create_app
from songhive.app import _build_tornado_app, _run_tornado, _run_uvicorn, main
from songhive.config.schema import SonghiveConfig


def test_build_tornado_app_settings(config, fake_redis):
    """_build_tornado_app passes config and redis through Tornado settings."""
    fastapi_app = create_app(config)
    fastapi_app.state.redis = fake_redis

    tornado_app = _build_tornado_app(config, fastapi_app)

    assert tornado_app.settings["config"] is config
    assert tornado_app.settings["redis"] is fake_redis


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
    def __init__(self, app, **_):
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
    monkeypatch.setattr(app_module, "_build_tornado_app", lambda _, fa: fa)
    monkeypatch.setattr(app_module, "get_redis_client", lambda _: "redis")
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
    monkeypatch.setattr(app_module, "_build_tornado_app", lambda _, fa: fa)
    monkeypatch.setattr(app_module, "get_redis_client", lambda _: "redis")
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
    monkeypatch.setattr(app_module, "init_db", lambda _: None)
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
    monkeypatch.setattr(app_module, "init_db", lambda _: None)
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
