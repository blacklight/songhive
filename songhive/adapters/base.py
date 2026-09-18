"""
Abstract base class for third-party API adapters.

An API adapter exposes Songhive content and behaviour through a foreign
API surface (Subsonic, Jellyfin, Icecast, Mopidy, ...) so that clients
written for those protocols can browse and stream the instance's library.

Adapters are read-oriented translators: they authenticate through their
own credential scheme (never the cookie session), map foreign entity and
error models onto Songhive's ACL and services, and may additionally expose
Tornado-native routes for byte streaming, mirroring how
``/api/v1/stream/{id}`` bypasses FastAPI.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Optional, Sequence, Tuple, Type

from fastapi import APIRouter, FastAPI

from ..config.schema import SonghiveConfig

if TYPE_CHECKING:
    import tornado.web


class APIAdapter(ABC):
    """Base class for adapters that map a third-party API onto Songhive."""

    #: Registry key and human-readable adapter name (e.g. ``"subsonic"``).
    name: ClassVar[str] = ""

    @abstractmethod
    def is_enabled(self, config: SonghiveConfig) -> bool:
        """Return True when the adapter should be mounted for ``config``."""

    @abstractmethod
    def router(self) -> Optional[APIRouter]:
        """
        Return the adapter's FastAPI router, or ``None`` when it only serves
        Tornado routes.

        The router is included without a prefix — adapters own their whole
        URL namespace (e.g. ``/rest`` for Subsonic) because third-party
        clients hard-code conventional base paths.
        """

    def tornado_routes(self) -> Sequence[Tuple[str, Type["tornado.web.RequestHandler"]]]:
        """
        Return ``(url_pattern, handler_class)`` pairs for Tornado-native
        endpoints such as byte streaming, which benefit from the same
        non-blocking delivery path as ``/api/v1/stream/{id}``.

        These only run when the application is served through the Tornado
        bootstrap; adapters should also provide a FastAPI fallback route
        for the uvicorn path.
        """
        return ()

    def include(self, app: FastAPI, config: SonghiveConfig) -> None:
        """Mount the adapter on ``app``. Called once from ``create_app``."""
        router = self.router()
        if router is not None:
            app.include_router(router)
