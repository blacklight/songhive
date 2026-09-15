"""
Integration tests for the application entry point.
"""

import asyncio
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import tornado.testing
import uvicorn
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import songhive.app as app_module
from songhive.api.app import create_app
from songhive.app import _build_tornado_app, _run_tornado, _run_uvicorn, main
from songhive.config.schema import SonghiveConfig
from songhive.models.base import Base, init_db, reset_db
from songhive.services.redis import create_redis_client as _real_create_redis_client


def test_build_tornado_app_settings(config, fake_redis):
    """_build_tornado_app passes config and redis through Tornado settings."""
    fastapi_app = create_app(config)
    fastapi_app.state.redis = fake_redis

    tornado_app = _build_tornado_app(config, fastapi_app)

    assert tornado_app.settings["config"] is config
    # Without a dedicated tornado_redis, it falls back to the FastAPI client.
    assert tornado_app.settings["redis"] is fake_redis


def test_build_tornado_app_dedicated_redis(config, fake_redis):
    """_build_tornado_app uses the dedicated tornado_redis when provided."""
    fastapi_app = create_app(config)
    fastapi_app.state.redis = fake_redis

    dedicated = _real_create_redis_client(config)
    try:
        tornado_app = _build_tornado_app(config, fastapi_app, tornado_redis=dedicated)

        assert tornado_app.settings["redis"] is dedicated
        assert tornado_app.settings["redis"] is not fake_redis
    finally:
        import asyncio

        asyncio.get_event_loop().run_until_complete(dedicated.aclose())


class _FakeLoop:
    """Minimal asyncio loop stand-in for _run_tornado tests."""

    def __init__(self, raise_on_signal=False):
        self.signal_handlers = {}
        self.raise_on_signal = raise_on_signal
        self.run_forever_called = False
        self.stopped = False
        self.closed = False
        self.ran_until_complete = []
        self.tasks = []
        self.scheduled = []

    def add_signal_handler(self, sig, callback):
        if self.raise_on_signal:
            raise RuntimeError("signal handlers not supported")
        self.signal_handlers[sig] = callback

    def create_task(self, coro):
        coro.close()
        task = asyncio.Future(loop=self)
        self.tasks.append(task)
        return task

    def call_soon(self, callback, *args, context=None):
        self.scheduled.append((callback, args))

    def get_debug(self):
        return False

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
    monkeypatch.setattr(app_module, "_build_tornado_app", lambda _, fa, **kw: fa)
    monkeypatch.setattr(app_module, "get_redis_client", lambda _: "redis")
    monkeypatch.setattr(app_module, "create_redis_client", lambda _: "tornado_redis")
    monkeypatch.setattr(app_module, "close_redis_client", lambda *_: None)

    fake_loop = _FakeLoop()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: fake_loop)

    _run_tornado(_minimal_config)

    assert port_file.read_text() == "12345"
    assert fake_loop.run_forever_called
    assert signal.SIGINT in fake_loop.signal_handlers
    assert signal.SIGTERM in fake_loop.signal_handlers
    assert len(fake_loop.tasks) == 1
    assert fake_loop.tasks[0].cancelled()

    fake_loop.signal_handlers[signal.SIGINT]()
    assert fake_loop.stopped


def test_run_tornado_signal_fallback(monkeypatch, tmp_path, _minimal_config):
    """Test the signal.signal fallback when add_signal_handler is unsupported."""
    recorded = []

    def fake_signal_handler(sig, handler):
        recorded.append((sig, handler))

    monkeypatch.setattr("songhive.api.app.create_app", _fake_create_app)
    monkeypatch.setattr("tornado.httpserver.HTTPServer", _FakeHTTPServer)
    monkeypatch.setattr(app_module, "_build_tornado_app", lambda _, fa, **kw: fa)
    monkeypatch.setattr(app_module, "get_redis_client", lambda _: "redis")
    monkeypatch.setattr(app_module, "create_redis_client", lambda _: "tornado_redis")
    monkeypatch.setattr(app_module, "close_redis_client", lambda *_: None)
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


def test_frontend_spa_served(client):
    """Root and frontend routes fall back to the built index.html."""
    response = client.get("/verify-email?token=abc123")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Songhive</title>" in response.text

    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_static_assets_served(client):
    """Existing files in songhive/static are served directly."""
    response = client.get("/favicon.ico")
    assert response.status_code == 200


def test_swagger_ui_served(client):
    """Swagger UI assets bundled with the frontend build are served by FastAPI."""
    response = client.get("/swagger-ui/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="swagger-ui"' in response.text

    # The bundled initializer points at this instance's OpenAPI document.
    response = client.get("/swagger-ui/swagger-initializer.js")
    assert response.status_code == 200
    assert '"/openapi.json"' in response.text


def test_swagger_ui_bare_path_redirects(client):
    """/swagger-ui redirects to the trailing-slash URL so relative assets resolve."""
    response = client.get("/swagger-ui", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/swagger-ui/"


def test_openapi_has_no_duplicate_tag_groups(client):
    """
    No operation carries tags that collapse to the same Swagger UI section.

    Tags assigned both on the router and via ``include_router`` would produce
    duplicate groups (e.g. "sessions" + "Sessions") in the Swagger UI.
    """
    import re

    spec = client.get("/openapi.json").json()
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            tags = op.get("tags") or []
            normalized = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in tags]
            assert len(normalized) == len(set(normalized)), f"{method.upper()} {path} has duplicate tag groups: {tags}"


def test_unknown_api_route_is_404(client):
    """Unknown /api/... paths still produce a 404 problem detail."""
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_head_request_behaves_like_bodyless_get(client):
    """HEAD is answered as a GET with headers but no body.

    FastAPI's APIRoute does not register HEAD, so GET endpoints used to
    answer HEAD with 405; the HeadToGetMiddleware serves them instead.
    """
    get_response = client.get("/api/v1/instance")
    head_response = client.head("/api/v1/instance")
    assert get_response.status_code == 200
    assert head_response.status_code == 200
    assert head_response.content == b""

    response = client.head("/")
    assert response.status_code == 200
    assert response.content == b""


class TestHeadRequestsThroughBridge(tornado.testing.AsyncHTTPTestCase):
    """HEAD requests through the Tornado↔a2wsgi bridge must not fail.

    Regression test: FastAPI GET routes answered HEAD with 405, and response
    bodies emitted for HEAD made Tornado's ``WSGIContainer`` raise
    ``HTTPOutputError`` ("Tried to write more data than Content-Length") —
    every HEAD request surfaced as a 502, which breaks crawlers that probe
    ``og:image`` URLs (and any other URL) with HEAD.
    """

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self.config = SonghiveConfig(
            auth={"secret_key": "a" * 64},  # type: ignore
            database={"url": f"sqlite+aiosqlite:///{self._tmp / 'songhive.db'}"},  # type: ignore
        )
        self.engine = create_async_engine(self.config.database.url, poolclass=NullPool)
        init_db(engine=self.engine, force=True)
        super().setUp()

        async def _create_tables():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.io_loop.run_sync(_create_tables)

    def tearDown(self):
        self.io_loop.run_sync(self.engine.dispose)
        reset_db()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def get_app(self):
        from fakeredis.aioredis import FakeRedis

        app = create_app(self.config)
        app.state.redis = FakeRedis(decode_responses=True)
        return _build_tornado_app(self.config, app)

    def test_head_on_api_route_returns_headers_only(self):
        response = self.fetch("/openapi.json", method="HEAD")
        assert response.code == 200
        assert response.body == b""
        # Content-Length still advertises the GET body size.
        assert int(response.headers["Content-Length"]) > 0

    def test_head_on_get_route_behaves_like_get(self):
        # FastAPI APIRoutes only register GET; HEAD used to 405.
        get_response = self.fetch("/api/v1/instance", method="GET")
        head_response = self.fetch("/api/v1/instance", method="HEAD")
        assert get_response.code == 200
        assert head_response.code == 200
        assert head_response.body == b""

    def test_head_on_spa_page_returns_headers_only(self):
        response = self.fetch("/", method="HEAD")
        assert response.code == 200
        assert response.body == b""

    def test_head_on_unknown_api_route_returns_404(self):
        response = self.fetch("/api/v1/does-not-exist", method="HEAD")
        assert response.code == 404
        assert response.body == b""

    def test_get_still_returns_body(self):
        response = self.fetch("/openapi.json", method="GET")
        assert response.code == 200
        assert response.body
