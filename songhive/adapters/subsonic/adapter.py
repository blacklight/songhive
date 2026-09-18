"""
Subsonic/OpenSubsonic API adapter.

Exposes Songhive's library through the ``/rest`` namespace that Subsonic
clients (DSub, Tempo, Symfonium, Ultrasonic, ...) hard-code, so they can
browse, search, and stream the instance's content after authenticating
with a Songhive username + password or API token.
"""

from typing import TYPE_CHECKING, Optional, Sequence, Tuple, Type

from fastapi import APIRouter

from ...config.schema import SonghiveConfig
from ..base import APIAdapter

if TYPE_CHECKING:
    import tornado.web


class SubsonicAdapter(APIAdapter):
    """Adapter exposing the Subsonic REST API under ``/rest``."""

    name = "subsonic"

    def is_enabled(self, config: SonghiveConfig) -> bool:
        """Mount when ``subsonic.enabled`` is set (default: enabled)."""
        return bool(config.subsonic.enabled)

    def router(self) -> Optional[APIRouter]:
        """Return the FastAPI router serving every ``/rest/*.view`` endpoint."""
        # Imports are deferred so importing ``adapters`` does not pull the
        # whole FastAPI route surface (and its Tornado siblings) into
        # contexts that only need the registry. ``media`` registers the
        # binary endpoints on the same router.
        from . import media  # noqa: F401
        from . import routes
        from .routes import router as envelope_router

        # The catch-all must be registered after every concrete .view route.
        routes.register_fallback_route()
        return envelope_router

    def tornado_routes(self) -> Sequence[Tuple[str, Type["tornado.web.RequestHandler"]]]:
        """Return native streaming routes for the Tornado bootstrap."""
        from .handlers import SubsonicDownloadHandler, SubsonicStreamHandler

        return [
            (r"/rest/stream\.view", SubsonicStreamHandler),
            (r"/rest/download\.view", SubsonicDownloadHandler),
        ]
