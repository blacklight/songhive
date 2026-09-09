"""
Proxy header middleware for FastAPI.

When Songhive is served behind a reverse proxy that terminates HTTPS, the
Tornado/a2wsgi bridge sees the request as plain HTTP and builds
``request.url`` with the ``http`` scheme. This middleware updates the ASGI
``scope["scheme"]`` from the ``X-Forwarded-Proto`` header so redirects, share
URLs, email verification links and other absolute URLs use the public scheme.
"""

from typing import Optional

from starlette.types import ASGIApp, Receive, Scope, Send


class ForwardedProtoMiddleware:
    """
    Update the request scheme from ``X-Forwarded-Proto``.

    The header is only honored when at least one proxy hop is trusted
    (``trusted_hops != 0``), matching the trust model used by ``client_ip`` for
    ``X-Forwarded-For``.
    """

    def __init__(self, app: ASGIApp, trusted_hops: Optional[int] = None):
        self.app = app
        self.trusted_hops = trusted_hops

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.trusted_hops != 0 and scope["type"] in {"http", "websocket"}:
            for name, value in scope.get("headers", []):
                if name != b"x-forwarded-proto":
                    continue

                proto = value.decode("latin1").strip().lower()
                if proto in ("http", "https"):
                    if scope["type"] == "websocket":
                        scope["scheme"] = proto.replace("http", "ws")
                    else:
                        scope["scheme"] = proto
                elif proto in ("ws", "wss") and scope["type"] == "websocket":
                    scope["scheme"] = proto
                break

        await self.app(scope, receive, send)
