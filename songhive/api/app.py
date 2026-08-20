"""
FastAPI application factory.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config.schema import SonghiveConfig
from ..services.redis import close_redis_client, get_redis_client
from .routes import (
    admin,
    albums,
    artists,
    auth,
    favorites,
    files,
    history,
    libraries,
    playlists,
    radios,
    tracks,
    users,
)

logger = logging.getLogger(__name__)


def create_app(config: SonghiveConfig) -> FastAPI:
    """
    Create and configure the FastAPI application.

    :param config: The application configuration.
    :returns: A configured FastAPI instance.
    """
    from ..version import __version__

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.redis = get_redis_client(config)
        yield
        await close_redis_client()

    app = FastAPI(
        title="Songhive",
        description="A federated and self-hosted music sharing service",
        version=__version__,
        debug=config.server.debug,
        lifespan=_lifespan,
    )

    # Store config in app state for dependency injection
    app.state.config = config
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

    # Register API routes
    api_prefix = "/api/v1"
    app.include_router(auth.router, prefix=api_prefix, tags=["auth"])
    app.include_router(users.router, prefix=api_prefix, tags=["users"])
    app.include_router(artists.router, prefix=api_prefix, tags=["artists"])
    app.include_router(albums.router, prefix=api_prefix, tags=["albums"])
    app.include_router(tracks.router, prefix=api_prefix, tags=["tracks"])
    app.include_router(playlists.router, prefix=api_prefix, tags=["playlists"])
    app.include_router(libraries.router, prefix=api_prefix, tags=["libraries"])
    app.include_router(favorites.router, prefix=api_prefix, tags=["favorites"])
    app.include_router(history.router, prefix=api_prefix, tags=["history"])
    app.include_router(radios.router, prefix=api_prefix, tags=["radios"])
    app.include_router(admin.router, prefix=api_prefix, tags=["admin"])
    app.include_router(files.router, prefix=api_prefix, tags=["files"])

    # Federation routes (pubby)
    if config.federation.enabled:
        _setup_federation(app, config)

    return app


def _setup_federation(app: FastAPI, config: SonghiveConfig):
    """Set up ActivityPub federation routes via pubby."""
    try:
        from pubby import ActivityPubHandler, ActorConfig
        from pubby.server.adapters.fastapi import bind_activitypub

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
    except ImportError:
        logger.error("Federation is enabled but pubby is not installed")
