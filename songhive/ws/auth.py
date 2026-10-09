"""
Shared WebSocket authentication and origin checking for Tornado handlers.

The ``?token=<jwt>`` / ``access_token`` cookie handshake and Origin checks are
shared by ``ws.events.EventWebSocket`` and ``streaming.live.LiveIngestHandler``
through ``AuthenticatedWebSocketMixin`` so both endpoints enforce exactly the
same authentication contract.
"""

import asyncio
import hashlib
import logging
import time
from typing import ClassVar, Dict, Optional, Tuple
from urllib.parse import urlsplit

import tornado.websocket

from ..api.middleware.auth import decode_access_token, get_access_token_jti
from ..models.base import get_session
from ..services.auth import get_user_by_id
from ..users.tokens import is_access_token_revoked

logger = logging.getLogger(__name__)


def ws_origin_matches(allowed: str, origin: str) -> bool:
    """Return True when ``origin`` matches ``allowed`` by scheme and host."""
    allowed_parts = urlsplit(allowed)
    origin_parts = urlsplit(origin)

    if allowed_parts.scheme and origin_parts.scheme and allowed_parts.scheme != origin_parts.scheme:
        return False
    if allowed_parts.hostname and origin_parts.hostname and allowed_parts.hostname != origin_parts.hostname:
        return False
    if allowed_parts.port is not None and origin_parts.port is not None and allowed_parts.port != origin_parts.port:
        return False

    return True


class AuthenticatedWebSocket(tornado.websocket.WebSocketHandler):
    """
    JWT/cookie authentication and origin checks for WebSocket handlers.

    Subclasses call ``_authenticate_websocket`` from ``open``; it returns the
    authenticated ``user_id`` or closes the socket with 4001 (with progressive
    backoff for repeat failures) and returns None.
    """

    _allowed_origins: ClassVar[Optional[set[str]]] = None
    # Failed-auth counters keyed by (remote_ip, token digest): count and last
    # failure time. Used to delay repeated unauthenticated closes so stale
    # clients whose reconnect backoff resets on every handshake cannot hammer
    # the endpoint. Shared across handlers: the throttle key already scopes
    # per client and credential.
    _auth_failures: ClassVar[Dict[str, Tuple[int, float]]] = {}

    def _get_allowed_origins(self) -> set[str]:
        """Return the configured allowed origins for WebSocket connections."""
        if self._allowed_origins is not None:
            return self._allowed_origins
        config = self.application.settings.get("config")
        if config is not None:
            return set(config.server.cors_origins)
        return set()

    def check_origin(self, origin: Optional[str]) -> bool:
        """Allow same-host and configured origins, and clients with no Origin header."""
        allowed = self._get_allowed_origins()
        if "*" in allowed:
            return True
        if not origin:
            return True
        if self._is_same_host(origin):
            return True
        return any(ws_origin_matches(allowed_origin, origin) for allowed_origin in allowed)

    def _is_same_host(self, origin: str) -> bool:
        """
        Return True when ``origin`` targets the same host as this request.

        The SPA is normally served from the same origin as the backend (nginx
        proxies both, and the Vite dev server forwards ``/ws`` while preserving
        the ``Host`` header), so a browser handshake whose Origin matches the
        request Host is same-origin traffic and does not need to be listed in
        ``server.cors_origins``.
        """
        origin_parts = urlsplit(origin)
        request_host = (self.request.host or "").lower()
        if not request_host or not origin_parts.hostname:
            return False
        if origin_parts.netloc.lower() == request_host:
            return True
        # Host headers without an explicit port match on hostname alone.
        if ":" not in request_host and (origin_parts.hostname or "").lower() == request_host:
            return True
        return False

    def _auth_key(self, token: Optional[str]) -> str:
        """Return the failure-throttling key for this connection attempt."""
        digest = hashlib.sha256((token or "").encode()).hexdigest()[:16]
        return f"{self.request.remote_ip}:{digest}"

    async def _reject_unauthenticated(self, key: str) -> None:
        """
        Close with 4001, delaying repeat failures from the same client.

        The first failure closes immediately so well-behaved clients can
        refresh their token and reconnect without delay. Each consecutive
        failure is delayed exponentially (1s, 2s, ..., capped at 30s) before
        the close frame is sent, which bounds the reconnect rate of clients
        whose backoff resets on every accepted handshake.
        """
        now = time.monotonic()
        failures = self._auth_failures
        if len(failures) > 1024:
            stale = [k for k, (_, ts) in failures.items() if now - ts >= 300.0]
            for k in stale:
                del failures[k]
            if len(failures) > 1024:
                failures.clear()
        count, _ = failures.get(key, (0, now))
        count += 1
        failures[key] = (count, now)
        if count > 1:
            await asyncio.sleep(min(2.0 ** (count - 2), 30.0))
        if self.ws_connection is not None:
            try:
                self.close(4001, "unauthenticated")
            except Exception:
                pass

    async def _authenticate_websocket(self) -> Optional[str]:
        """
        Authenticate the handshake and return the user id, or None.

        The token comes from the ``?token=`` query parameter (API clients and
        share flows) or the ``access_token`` cookie, which the browser sends
        automatically on same-origin WebSocket handshakes. Revoked tokens and
        inactive users are rejected with the standard 4001 close.
        """
        token = self.get_argument("token", default=None) or self.get_cookie("access_token")
        auth_key = self._auth_key(token)

        config = self.application.settings.get("config")
        user_id = None
        if token and config is not None:
            user_id = decode_access_token(token, config.auth.secret_key)
            if user_id is not None:
                jti = get_access_token_jti(token, config.auth.secret_key)
                if jti is not None:
                    redis = self.application.settings.get("redis")
                    if redis is not None and await is_access_token_revoked(jti, redis):
                        user_id = None
        if user_id is None:
            await self._reject_unauthenticated(auth_key)
            return None

        async with get_session() as session:
            user = await get_user_by_id(session, user_id)

        if user is None or not user.is_active:
            await self._reject_unauthenticated(auth_key)
            return None

        self._auth_failures.pop(auth_key, None)
        return str(user.id)
