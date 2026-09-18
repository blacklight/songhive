"""
Tornado-native Subsonic streaming handlers.

``stream.view``/``download.view`` reuse :class:`StreamHandler`'s delivery
machinery (range requests, cached/live transcodes, external-stream
proxying) while authenticating through Subsonic request parameters and
translating failures into protocol error envelopes instead of JSON bodies.
"""

from typing import Dict, Optional

from ...models.base import get_session
from ...models.track import Track
from ...models.user import User
from ...streaming.handler import StreamHandler
from . import now_playing
from . import serializers as sz
from .auth import authenticate_subsonic
from .errors import GENERIC_ERROR, MISSING_PARAMETER, NOT_AUTHORIZED, NOT_FOUND, WRONG_CREDENTIALS, SubsonicError
from .responses import error_envelope, render


class SubsonicStreamHandler(StreamHandler):
    """Serve ``/rest/stream.view`` through the shared streaming machinery."""

    _subsonic_user: Optional[User] = None

    def _subsonic_params(self) -> Dict[str, str]:
        """Return first values of the request's Subsonic parameters."""
        return {name: values[0].decode("utf-8", "replace") for name, values in self.request.arguments.items() if values}

    def _subsonic_error(self, code: int, message: str) -> None:
        """Write a protocol error envelope and finish the request."""
        body, content_type = render(error_envelope(code, message), self._subsonic_params())
        self.set_header("Content-Type", content_type)
        self.write(body)
        self.finish()

    # --- error renderers: protocol envelopes instead of JSON bodies ---

    def _unauthorized(self):
        self._subsonic_error(WRONG_CREDENTIALS, "Wrong username or password")

    def _forbidden(self):
        self._subsonic_error(NOT_AUTHORIZED, "Access denied")

    def _not_found(self, message: str = "not found"):
        self._subsonic_error(NOT_FOUND, message)

    def _bad_request(self, message: str):
        self._subsonic_error(GENERIC_ERROR, message)

    def _unprocessable(self, message: str):
        self._subsonic_error(GENERIC_ERROR, message)

    # --- hooks ---

    async def _authenticate(self, session) -> Optional[User]:
        """Authenticate Subsonic ``u``/``p``/``apiKey`` parameters."""
        try:
            self._subsonic_user = await authenticate_subsonic(
                session,
                self._subsonic_params(),
                self._config.auth.secret_key,
                self._redis,
            )
            return self._subsonic_user
        except SubsonicError as exc:
            self._subsonic_error(exc.code, exc.message)
            return None

    def _client_name(self) -> Optional[str]:
        return self.get_argument("c", None)

    async def _prepare_response(self, track: Track) -> None:
        """Record the play in the adapter's now-playing registry."""
        user = self._subsonic_user
        if user is not None:
            now_playing.record(str(user.id), user.username, str(track.id), self._client_name())

    def _requested_format(self) -> tuple[Optional[str], Optional[str]]:
        """Map Subsonic ``format``/``maxBitRate`` to the transcode arguments."""
        fmt = self.get_argument("format", None)
        if fmt == "raw":
            fmt = None
        return fmt, self.get_argument("maxBitRate", None)

    async def get(self, *args, **kwargs):
        """Stream the track named by the ``id`` parameter."""
        track_id = self.get_argument("id", None)
        if not track_id:
            # Authenticate first so unauthenticated probes get 40, not 10.
            async with get_session() as session:
                await self._authenticate(session)
            if self._request_aborted():
                return
            self._subsonic_error(MISSING_PARAMETER, "Required parameter is missing: id")
            return
        await super().get(track_id)


class SubsonicDownloadHandler(SubsonicStreamHandler):
    """Serve ``/rest/download.view`` with an attachment disposition."""

    async def _prepare_response(self, track: Track) -> None:
        await super()._prepare_response(track)
        title = track.title.replace('"', "").replace("\\", "") or "track"
        self.set_header("Content-Disposition", f'attachment; filename="{title}.{sz._track_suffix(track)}"')
