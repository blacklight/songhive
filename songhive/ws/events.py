"""
WebSocket handler for real-time events.
"""

import json
import logging
from typing import Any, ClassVar, Optional, Set, Union
from urllib.parse import urlsplit

import tornado.ioloop
import tornado.websocket

from ..api.middleware.auth import decode_access_token, get_access_token_jti
from ..models.base import get_session
from ..services.auth import get_user_by_id
from ..users.tokens import is_access_token_revoked

logger = logging.getLogger(__name__)


def _origin_matches(allowed: str, origin: str) -> bool:
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


class EventWebSocket(tornado.websocket.WebSocketHandler):
    """
    WebSocket endpoint for real-time event broadcasting.

    Connections are authenticated with a ``?token=<jwt>`` query parameter. Clients
    may subscribe to named topics; broadcasts with a ``topic`` are only delivered
    to connections that have explicitly subscribed to that topic (or to
    connections with an empty topic list, which means "all").
    """

    _connections: ClassVar[Set["EventWebSocket"]] = set()
    _allowed_origins: ClassVar[Optional[set[str]]] = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user_id: Optional[str] = None
        self.topics: Set[str] = set()

    def _get_allowed_origins(self) -> set[str]:
        """Return the configured allowed origins for WebSocket connections."""
        if self._allowed_origins is not None:
            return self._allowed_origins
        config = self.application.settings.get("config")
        if config is not None:
            return set(config.server.cors_origins)
        return set()

    def check_origin(self, origin: Optional[str]) -> bool:
        """Allow configured origins and non-browser clients with no Origin header."""
        allowed = self._get_allowed_origins()
        if "*" in allowed:
            return True
        if not origin:
            return True
        return any(_origin_matches(allowed_origin, origin) for allowed_origin in allowed)

    async def open(self, *_: str, **__: str) -> None:
        """Authenticate the connection and add it to the active set."""
        token = self.get_argument("token", default=None)
        if not token:
            self.close(4001, "unauthenticated")
            return

        config = self.application.settings.get("config")
        if config is None:
            self.close(4001, "unauthenticated")
            return

        user_id = decode_access_token(token, config.auth.secret_key)
        if user_id is None:
            self.close(4001, "unauthenticated")
            return

        jti = get_access_token_jti(token, config.auth.secret_key)
        if jti is not None:
            redis = self.application.settings.get("redis")
            if redis is not None and await is_access_token_revoked(jti, redis):
                self.close(4001, "unauthenticated")
                return

        async with get_session() as session:
            user = await get_user_by_id(session, user_id)

        if user is None or not user.is_active:
            self.close(4001, "unauthenticated")
            return

        self.user_id = str(user.id)
        self._io_loop = tornado.ioloop.IOLoop.current()
        EventWebSocket._connections.add(self)

    def on_message(self, message: Union[str, bytes]) -> None:
        """Handle client subscription and unsubscription requests."""
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("Ignoring non-JSON WebSocket message")
            return

        action = payload.get("action")
        raw_topics = payload.get("topics")
        if not isinstance(raw_topics, list):
            logger.warning("Ignoring WebSocket message without a valid topics list")
            return

        topics = {str(t) for t in raw_topics}
        if action == "subscribe":
            self.topics.update(topics)
        elif action == "unsubscribe":
            self.topics.difference_update(topics)
        else:
            logger.warning("Ignoring unknown WebSocket action: %s", action)

    def on_close(self) -> None:
        """Handle WebSocket connection close."""
        EventWebSocket._connections.discard(self)

    @classmethod
    def _send(cls, conn: "EventWebSocket", message: str) -> None:
        """Write a message to a connection, removing it if it has closed."""
        try:
            conn.write_message(message)
        except tornado.websocket.WebSocketClosedError:
            cls._connections.discard(conn)

    @classmethod
    def broadcast(
        cls,
        event_type: str,
        data: dict,
        topic: Optional[str] = None,
    ) -> None:
        """Broadcast an event to connected clients.

        When ``topic`` is provided, only connections that have explicitly
        subscribed to that topic receive the message (or connections with an
        empty topic list, which means they subscribe to all topics). Broadcasts
        without a ``topic`` are delivered to every active connection.
        """
        message = json.dumps({"type": event_type, "data": data})
        for conn in list(cls._connections):
            if topic is not None and conn.topics and topic not in conn.topics:
                continue
            io_loop = getattr(conn, "_io_loop", None)
            if io_loop is None:
                # Backward compatibility for older connections; write
                # synchronously when the IOLoop is not available.
                cls._send(conn, message)
            else:
                # Schedule the write on the connection's IOLoop. This is
                # thread-safe and avoids races when broadcast() is called from
                # outside the IOLoop thread (e.g. Celery or tests).
                io_loop.add_callback(cls._send, conn, message)
