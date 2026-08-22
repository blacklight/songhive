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
import signal
import sys
from collections.abc import Iterable
from typing import Any, Awaitable, Callable, cast

from .config import SonghiveConfig, load_config
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

    wsgi_app = ASGIMiddleware(cast(Callable[[Any, Any, Any], Awaitable[None]], fastapi_app))
    container = WSGIContainer(cast(Callable[[dict[str, Any], Any], Iterable[bytes]], cast(object, wsgi_app)))

    return Application(
        [
            (r"/ws/events", EventWebSocket),
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

    logger.info(
        "Songhive server (Tornado) starting on %s:%d",
        config.server.host,
        config.server.port,
    )

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

    # Initialize database
    init_db(config.database.url)

    # Try Tornado (preferred), fall back to uvicorn
    try:
        from a2wsgi import ASGIMiddleware  # noqa: F401

        _run_tornado(config)
    except ImportError:
        logger.warning(
            "a2wsgi not available, using uvicorn (install a2wsgi for " "native Tornado WebSocket/streaming support)"
        )
        _run_uvicorn(config)
