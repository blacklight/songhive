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
from typing import Any, Awaitable, Callable, Optional, cast

from .config import SonghiveConfig, load_config
from .migrations import ensure_migrated
from .models.base import init_db
from .services.redis import close_redis_client, get_redis_client

logger = logging.getLogger(__name__)


def _build_tornado_app(config: SonghiveConfig, fastapi_app) -> Any:
    """Build the Tornado application without binding a socket."""
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

    return Application(
        [
            (r"/ws/events", EventWebSocket),
            (r"/ws/", EventWebSocket),
            (r"/api/v1/stream/(?P<track_id>[^/]+)", StreamHandler),
            (r".*", FallbackHandler, {"fallback": container}),
        ],
        debug=config.server.debug,
        config=config,
        redis=fastapi_app.state.redis,
    )


def _run_tornado(config: SonghiveConfig):
    """Run with Tornado as the top-level server (preferred)."""
    from tornado.httpserver import HTTPServer

    from .api.app import create_app

    fastapi_app = create_app(config)
    fastapi_app.state.redis = get_redis_client(config)

    tornado_app = _build_tornado_app(config, fastapi_app)

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
        # a2wsgi may run FastAPI in a dedicated event loop. The shared Redis
        # client is bound to that loop, so close it there to avoid
        # "Future attached to a different loop" on shutdown.
        a2wsgi_loop: Optional[asyncio.AbstractEventLoop] = getattr(fastapi_app.state, "_a2wsgi_loop", None)
        if a2wsgi_loop is not None and a2wsgi_loop is not loop:
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

    banner = dedent(
        rf"""
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
        """
    )

    logger.info(banner)


def main():
    """
    Songhive application entry point.
    """
    # Check if this is an admin command
    if len(sys.argv) > 1 and sys.argv[1] == "admin":
        from .cli.admin import admin_main

        admin_main(sys.argv[2:])
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
