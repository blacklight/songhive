"""
Permissive CORS for read-only media endpoints.

Federated clients embed audio by fetching these URLs cross-origin (e.g.
Akkoma/Mangane render ``<audio>`` players backed by ``fetch``). The wildcard
origin is safe here because the endpoints only serve bytes over GET/HEAD:
browsers refuse to expose responses to credentialed requests when the
allow-origin is ``*``, and the ``access_token`` cookie is ``SameSite=Lax``
so it is never attached to cross-origin requests anyway.
"""

import re
from typing import Optional, Sequence

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Read-only endpoints that serve media bytes to embedded players. Federation
# publishes ``/api/v1/files/{id}/download`` on Audio objects; track downloads
# and the stream endpoint are covered for the same clients.
_MEDIA_PATH_RE = re.compile(r"^/api/v1/(?:(?:files|tracks)/[^/]+/download|stream/[^/]+)$")

_ALLOW_METHODS = "GET, HEAD, OPTIONS"
_EXPOSE_HEADERS = "Accept-Ranges, Content-Range, Content-Length, Content-Type"
_MAX_AGE = "86400"


class MediaCorsMiddleware:
    """
    Add ``Access-Control-Allow-Origin: *`` to media-serving endpoints.

    Answers CORS preflights on these paths itself so fetch-based players can
    send ``Range`` headers, and stamps the wildcard origin on responses that
    do not already carry an allow-origin header. Requests whose ``Origin``
    matches the configured ``allow_origins`` pass through untouched so the
    credentialed ``CORSMiddleware`` policy keeps working for them.
    """

    def __init__(self, app: ASGIApp, allow_origins: Optional[Sequence[str]] = None) -> None:
        self.app = app
        self.allow_origins = frozenset(allow_origins or ())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _MEDIA_PATH_RE.match(scope["path"]):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is not None and origin in self.allow_origins:
            await self.app(scope, receive, send)
            return

        if scope["method"] == "OPTIONS" and origin and "access-control-request-method" in headers:
            response = Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": _ALLOW_METHODS,
                    "Access-Control-Allow-Headers": headers.get("access-control-request-headers", "range"),
                    "Access-Control-Max-Age": _MAX_AGE,
                    "Vary": "Origin",
                },
            )
            await response(scope, receive, send)
            return

        async def send_with_media_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                if "access-control-allow-origin" not in response_headers:
                    response_headers["Access-Control-Allow-Origin"] = "*"
                    # CORSMiddleware sets this on every response when
                    # credentials are enabled; it cannot be combined with a
                    # wildcard origin, so drop it here.
                    if "access-control-allow-credentials" in response_headers:
                        del response_headers["access-control-allow-credentials"]
                if "access-control-expose-headers" not in response_headers:
                    response_headers["Access-Control-Expose-Headers"] = _EXPOSE_HEADERS
                # The allow-origin value varies by request Origin (allowlist
                # vs wildcard), so caches must key on it.
                vary = response_headers.get("vary", "")
                if "origin" not in {v.strip().lower() for v in vary.split(",")}:
                    response_headers["Vary"] = f"{vary}, Origin" if vary else "Origin"
            await send(message)

        await self.app(scope, receive, send_with_media_cors)
