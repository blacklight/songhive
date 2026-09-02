"""
Songhive application entry point.

Bootstraps the server using Tornado as the main HTTP server. FastAPI is
wrapped via a2wsgi (ASGI-to-WSGI bridge) and served through Tornado's
FallbackHandler. Tornado natively handles WebSocket connections and audio
streaming.

If a2wsgi is unavailable, falls back to running everything via uvicorn
(loses native Tornado WebSocket support but is still functional).
"""

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Iterable
from textwrap import dedent
from typing import Any, Awaitable, Callable, Collection, Dict, Optional, cast

from .config import SonghiveConfig, load_config
from .migrations import ensure_migrated
from .models.base import init_db
from .services.redis import close_redis_client, create_redis_client, get_redis_client

logger = logging.getLogger(__name__)


def _build_tornado_app(config: SonghiveConfig, fastapi_app, tornado_redis=None) -> Any:
    """Build the Tornado application without binding a socket.

    ``tornado_redis`` is an optional Redis client dedicated to the Tornado
    event loop. When Tornado and FastAPI run on different event loops (the
    default when bridged via ``a2wsgi``), the shared Redis client stored on
    ``fastapi_app.state.redis`` is bound to the ``a2wsgi`` loop and cannot be
    used from Tornado request handlers. When provided, this client is used for
    Tornado-side Redis access (e.g. token revocation checks in the streaming
    handler); otherwise the FastAPI client is used as a fallback (suitable for
    tests where everything runs on a single loop).
    """
    from a2wsgi import ASGIMiddleware
    from tornado.web import Application, FallbackHandler
    from tornado.wsgi import WSGIContainer

    from .streaming.handler import StreamHandler
    from .ws.events import EventWebSocket

    EventWebSocket._allowed_origins = set(config.server.cors_origins)

    wsgi_app = ASGIMiddleware(cast(Callable[[Any, Any, Any], Awaitable[None]], fastapi_app))
    container = WSGIContainer(cast(Callable[[dict[str, Any], Any], Iterable[bytes]], cast(object, wsgi_app)))

    # a2wsgi runs the ASGI app in a dedicated event loop; FastAPI routes (and
    # the shared Redis client) are bound to that loop, so store a reference so
    # shutdown can close Redis on the correct loop.
    a2wsgi_loop = getattr(wsgi_app, "loop", None)
    if a2wsgi_loop is not None:
        fastapi_app.state._a2wsgi_loop = a2wsgi_loop

    redis = tornado_redis if tornado_redis is not None else fastapi_app.state.redis

    return Application(
        [
            (r"/ws/events", EventWebSocket),
            (r"/ws/", EventWebSocket),
            (r"/api/v1/stream/(?P<track_id>[^/]+)", StreamHandler),
            (r".*", FallbackHandler, {"fallback": container}),
        ],
        debug=config.server.debug,
        config=config,
        redis=redis,
    )


def _run_tornado(config: SonghiveConfig):
    """Run with Tornado as the top-level server (preferred)."""
    from tornado.httpserver import HTTPServer

    from .api.app import create_app

    fastapi_app = create_app(config)
    fastapi_app.state.redis = get_redis_client(config)

    # Tornado request handlers (e.g. the streaming handler) run on the main
    # Tornado event loop, while FastAPI — bridged via a2wsgi — runs on a
    # separate loop. The shared Redis client binds its connection pool to the
    # first loop that uses it (the a2wsgi loop), so Tornado needs its own
    # client to avoid "Future attached to a different loop" errors.
    tornado_redis = create_redis_client(config)

    tornado_app = _build_tornado_app(config, fastapi_app, tornado_redis=tornado_redis)

    server = HTTPServer(tornado_app)
    server.listen(config.server.port, address=config.server.host)

    # If port is 0 the kernel picked an ephemeral port; report the actual one.
    bound_socket = next(iter(server._sockets.values()))
    actual_host, actual_port = bound_socket.getsockname()[:2]

    logger.info(
        "Songhive server (Tornado) starting on %s:%d",
        actual_host,
        actual_port,
    )

    port_file = os.environ.get("SONGHIVE_WRITE_PORT_TO")
    if port_file:
        with open(port_file, "w", encoding="utf-8") as f:
            f.write(str(actual_port))

    loop = asyncio.get_event_loop()

    def _shutdown():
        logger.info("Shutting down...")
        server.stop()
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except (RuntimeError, OSError):
            # Fallback for platforms that don't support add_signal_handler.
            signal.signal(sig, lambda *_: _shutdown())

    try:
        loop.run_forever()
    finally:
        # The Tornado-side Redis client is bound to this loop; close it here.
        loop.run_until_complete(close_redis_client(tornado_redis))

        # a2wsgi may run FastAPI in a dedicated event loop. The shared Redis
        # client is bound to that loop, so close it there to avoid
        # "Future attached to a different loop" on shutdown.
        a2wsgi_loop: Optional[asyncio.AbstractEventLoop] = getattr(fastapi_app.state, "_a2wsgi_loop", None)
        if a2wsgi_loop is not None and a2wsgi_loop is not loop:
            assert a2wsgi_loop  # for mypy
            future = asyncio.run_coroutine_threadsafe(close_redis_client(), a2wsgi_loop)
            try:
                future.result()
            finally:
                a2wsgi_loop.call_soon_threadsafe(a2wsgi_loop.stop)
        else:
            loop.run_until_complete(close_redis_client())
        loop.close()
        logger.info("Server stopped.")


def _run_uvicorn(config):
    """Fallback: run with uvicorn (pure ASGI, no native Tornado handlers)."""
    import uvicorn

    from .api.app import create_app

    fastapi_app = create_app(config)

    logger.info(
        "Songhive server (uvicorn) starting on %s:%d",
        config.server.host,
        config.server.port,
    )

    uvicorn.run(
        fastapi_app,
        host=config.server.host,
        port=config.server.port,
        log_level="debug" if config.server.debug else "info",
    )


def _print_cli_banner():
    from .version import __version__

    # fmt: off
    logger.info(
        dedent(rf"""
               ┏━[━━]━┓                                                ┏━[━━]━┓
               ┃      ┃                                                ┃      ┃
            ━━━┫      ┣━━━━━━━━━━━━━━━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━┫      ┣━━━

            ⟦▸⟧   _______  _____  __   _  ______ _     _ _____ _    _ _______  ⟦▸⟧
            ⟦▹⟧   |______ |     | | \  | |  ____ |_____|   |    \  /  |______  ⟦▹⟧
            ⟦▪⟧   ______| |_____| |  \_| |_____| |     | __|__   \/   |______  ⟦▪⟧
            ⟦▫⟧                                                                ⟦▫⟧
                  version: {__version__}
            ━━━┫      ┣━━━━━━━━━━━━━━━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━┫      ┣━━━
               ┃      ┃                                                ┃      ┃
               ┗━[━━]━┛                                                ┗━[━━]━┛
        """)
    )
    # fmt: on


def _admin_main(args: Collection[str]):
    from .cli.admin import admin_main

    admin_main(args)


def _watch_main(args: Collection[str]):
    from .cli.watch import watch_main

    watch_main(args)


_entry_points: Dict[str, Callable[[Collection[str]], None]] = {
    "admin": _admin_main,
    "watch-external-libraries": _watch_main,
}


def main():
    """
    Songhive application entry point.
    """
    # Check for dedicated CLI commands before starting the web server.
    if len(sys.argv) > 1:
        entry_point = _entry_points.get(sys.argv[1])
        if entry_point:
            entry_point(sys.argv[2:])
            return

    config = load_config()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if config.server.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Initialize database and apply any pending migrations
    init_db(config.database.url)
    ensure_migrated(config.database.url)

    # Try Tornado (preferred), fall back to uvicorn
    try:
        from a2wsgi import ASGIMiddleware  # noqa: F401

        _print_cli_banner()
        _run_tornado(config)
    except ImportError:
        logger.warning(
            "a2wsgi not available, using uvicorn (install a2wsgi for " "native Tornado WebSocket/streaming support)"
        )
        _run_uvicorn(config)
