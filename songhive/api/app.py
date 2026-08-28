"""
FastAPI application factory.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, MutableMapping

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.types import Receive, Send

from ..config.schema import SonghiveConfig
from ..models.base import get_session, init_db
from ..services.acl import audit_ownerless_private
from ..services.redis import close_redis_client, get_redis_client
from ..services.settings import apply_settings_overrides
from ..version import __version__
from .errors import install_error_handlers
from .routes import (
    admin,
    albums,
    api_tokens,
    artists,
    auth,
    favorites,
    federation,
    files,
    history,
    instance,
    libraries,
    playlists,
    radios,
    reports,
    share,
    share_urls,
    shares,
    tracks,
    users,
)

logger = logging.getLogger(__name__)


def _sync_settings_overlay(config: SonghiveConfig) -> tuple[SonghiveConfig, bool]:
    """
    Apply DB settings overrides synchronously when no event loop is running.

    ``create_app`` may be called before an asyncio loop exists (e.g. in a
    worker process or during CLI startup), so this path runs the overlay in a
    temporary event loop. When a loop is already running, it skips and lets the
    ``_lifespan`` hook apply the overlay in the real application loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return config, False

    async def _run() -> SonghiveConfig:
        async with get_session() as session:
            return await apply_settings_overrides(session, config)

    loop = None
    try:
        loop = asyncio.new_event_loop()
        # ``wait_for`` provides a hard ceiling so a stuck database query cannot
        # block server startup forever. Avoid ``asyncio.run`` here because it
        # sets/unsets the current event loop, which can break Tornado's
        # IOLoop when ``create_app`` is called from an async test context.
        return loop.run_until_complete(asyncio.wait_for(_run(), timeout=10.0)), True
    except asyncio.TimeoutError:
        logger.warning("Settings overlay timed out; using config file/defaults")
        return config, False
    except Exception:
        logger.exception("Failed to apply settings overrides at app creation")
        return config, False
    finally:
        if loop is not None:
            loop.close()


def _setup_static_routes(app: FastAPI):
    """
    Serve the built frontend SPA as the default handler.

    API/WebSocket/stream paths are excluded so unknown /api/... /ws/...
    /stream/... routes still 404. The default is only reached when no API route
    (or redirect-slashes partial match) matches, so it never shadows API
    endpoints.
    """
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if (static_dir / "index.html").is_file():

        async def _serve_static(scope: MutableMapping[str, Any], receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                raise HTTPException(status_code=404)

            path = scope["path"].lstrip("/")
            if path.startswith("api/") or path.startswith("ws/") or path.startswith("stream/"):
                raise HTTPException(status_code=404)

            # Federation/ActivityPub requests to disabled endpoints should 404,
            # not receive the SPA HTML shell.
            for name, value in scope.get("headers", []):
                if name.lower() == b"accept" and b"application/activity+json" in value:
                    raise HTTPException(status_code=404)

            requested = (static_dir / path).resolve()
            static_root = static_dir.resolve()
            if requested.is_file() and str(requested).startswith(str(static_root)):
                await FileResponse(requested)(scope, receive, send)
            else:
                await FileResponse(static_dir / "index.html")(scope, receive, send)

        app.router.default = _serve_static


def create_app(config: SonghiveConfig) -> FastAPI:
    """
    Create and configure the FastAPI application.

    :param config: The application configuration.
    :returns: A configured FastAPI instance.
    """
    init_db(config.database.url)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.redis = get_redis_client(config)

        # If the synchronous overlay in create_app already ran, there is no
        # need to re-read the same settings here. When it was skipped (because
        # a loop was already running), this is the first chance to apply DB
        # settings overrides using the real application loop and Redis.
        if not getattr(app.state, "_sync_overlay_applied", False):
            try:
                async with get_session() as session:
                    await audit_ownerless_private(session)
                    app.state.config = await apply_settings_overrides(session, app.state.config)
            except Exception:
                logger.exception("Failed to apply settings overrides during startup")

        yield
        await close_redis_client()

    app = FastAPI(
        title="Songhive",
        description="A federated and self-hosted music sharing service",
        version=__version__,
        debug=config.server.debug,
        lifespan=_lifespan,
    )

    # Store config in app state for dependency injection. The synchronous
    # overlay above handles the no-event-loop case; _lifespan handles the
    # rest. Both exist because create_app is called in different contexts.
    config, sync_overlay_applied = _sync_settings_overlay(config)
    app.state.config = config
    app.state._sync_overlay_applied = sync_overlay_applied
    app.state.storage_service = None
    app.state.storage_service_config = None

    # CORS middleware
    allow_credentials = True
    if "*" in config.server.cors_origins:
        logger.warning(
            "Wildcard CORS origin (['*']) cannot be used with credentials; "
            "disabling allow_credentials. Specify explicit origins to enable credentials."
        )
        allow_credentials = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.server.cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register RFC 7807 problem detail exception handlers
    install_error_handlers(app)

    # Register API routes
    api_prefix = "/api/v1"
    app.include_router(auth.router, prefix=api_prefix, tags=["auth"])
    app.include_router(api_tokens.router, prefix=api_prefix, tags=["api-tokens"])
    app.include_router(users.router, prefix=api_prefix, tags=["users"])
    app.include_router(artists.router, prefix=api_prefix, tags=["artists"])
    app.include_router(albums.router, prefix=api_prefix, tags=["albums"])
    app.include_router(tracks.router, prefix=api_prefix, tags=["tracks"])
    app.include_router(playlists.router, prefix=api_prefix, tags=["playlists"])
    app.include_router(libraries.router, prefix=api_prefix, tags=["libraries"])
    app.include_router(favorites.router, prefix=api_prefix, tags=["favorites"])
    app.include_router(history.router, prefix=api_prefix, tags=["history"])
    app.include_router(radios.router, prefix=api_prefix, tags=["radios"])
    app.include_router(reports.router, prefix=api_prefix, tags=["reports"])
    app.include_router(reports.admin_router, prefix=api_prefix, tags=["reports"])
    app.include_router(admin.router, prefix=api_prefix, tags=["admin"])
    app.include_router(files.router, prefix=api_prefix, tags=["files"])
    app.include_router(shares.router, prefix=api_prefix, tags=["shares"])
    app.include_router(share_urls.router, prefix=api_prefix, tags=["share-urls"])
    app.include_router(share.router, prefix=api_prefix, tags=["share"])
    app.include_router(instance.v1_router, prefix="/api/v1")
    app.include_router(instance.v2_router, prefix="/api/v2")

    # Federation routes
    if config.federation.enabled and config.federation.instance_domain:
        app.include_router(federation.router)
        _setup_federation(app, config)

    _setup_static_routes(app)

    return app


def _setup_federation(app: FastAPI, config: SonghiveConfig):
    """Set up ActivityPub federation routes via pubby."""
    try:
        from pubby import ActivityPubHandler, ActorConfig
        from pubby.server.adapters.fastapi import bind_activitypub
        from pubby.server.adapters.fastapi_mastodon import bind_mastodon_api

        from ..federation.storage import (
            create_activitypub_storage,
            get_or_create_private_key,
        )

        actor_config = ActorConfig(
            base_url=f"https://{config.federation.instance_domain}",
            username=config.federation.instance_name.lower().replace(" ", "-"),
            name=config.federation.instance_name,
            summary=config.federation.instance_description,
            actor_path="/ap/actor",
            type="Application",
        )

        storage = create_activitypub_storage(config.database.url)
        private_key_path = get_or_create_private_key(config.federation.private_key_path)

        handler = ActivityPubHandler(
            storage=storage,
            actor_config=actor_config,
            private_key_path=str(private_key_path),
        )
        bind_activitypub(app, handler, prefix="/ap")
        bind_mastodon_api(
            app,
            handler,
            title=config.federation.instance_name,
            description=config.federation.instance_description,
            software_name="Songhive",
            software_version=__version__,
        )
    except ImportError:
        logger.error("Federation is enabled but pubby is not installed")
