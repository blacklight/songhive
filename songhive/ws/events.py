"""
WebSocket handler for real-time events.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, ClassVar, Dict, Optional, Set, Tuple, Union
from urllib.parse import urlsplit

import tornado.ioloop
import tornado.websocket

from ..api.middleware.auth import decode_access_token, get_access_token_jti
from ..models.base import get_session
from ..services.auth import get_user_by_id
from ..users.tokens import is_access_token_revoked

logger = logging.getLogger(__name__)

# Redis pub/sub channel used to fan out events across processes. ``broadcast``
# and ``send_to_user`` only see the WebSocket connections living in their own
# process, so every event is also published here; the Tornado process (which
# holds the sockets) runs ``ws_event_subscriber`` to deliver envelopes
# published by other processes (e.g. Celery workers).
WS_EVENTS_CHANNEL = "songhive:ws-events"

# Unique id for this process, stamped on published envelopes so the local
# subscriber can skip events this process already delivered locally.
_PROCESS_ID = uuid.uuid4().hex


def _publish_envelope(envelope: dict) -> None:
    """Publish an event envelope to the WS pub/sub channel (best effort)."""
    try:
        from ..services.redis import get_sync_redis_client

        get_sync_redis_client().publish(WS_EVENTS_CHANNEL, json.dumps(envelope))
    except Exception as exc:
        logger.warning("Could not publish WebSocket event to Redis: %s", exc)


async def ws_event_subscriber(redis) -> None:
    """Forward events published by other processes to local connections.

    Subscribes to ``WS_EVENTS_CHANNEL`` and feeds each message to
    ``EventWebSocket.deliver_envelope``. Reconnects with backoff when the
    subscription drops; cancelled on shutdown.
    """
    backoff = 1.0
    while True:
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(WS_EVENTS_CHANNEL)
            backoff = 1.0
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    envelope = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Ignoring malformed WebSocket event envelope")
                    continue
                try:
                    EventWebSocket.deliver_envelope(envelope)
                except Exception:
                    logger.exception("Failed to deliver WebSocket event envelope")
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WebSocket event subscriber error; retrying in %.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            try:
                await pubsub.aclose()
            except Exception:
                pass


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
    # Failed-auth counters keyed by (remote_ip, token digest): count and last
    # failure time. Used to delay repeated unauthenticated closes so stale
    # clients whose reconnect backoff resets on every handshake cannot hammer
    # the endpoint.
    _auth_failures: ClassVar[Dict[str, Tuple[int, float]]] = {}

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
        """Allow same-host and configured origins, and clients with no Origin header."""
        allowed = self._get_allowed_origins()
        if "*" in allowed:
            return True
        if not origin:
            return True
        if self._is_same_host(origin):
            return True
        return any(_origin_matches(allowed_origin, origin) for allowed_origin in allowed)

    def _is_same_host(self, origin: str) -> bool:
        """Return True when ``origin`` targets the same host as this request.

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
        """Close with 4001, delaying repeat failures from the same client.

        The first failure closes immediately so well-behaved clients can
        refresh their token and reconnect without delay. Each consecutive
        failure is delayed exponentially (1s, 2s, ..., capped at 30s) before
        the close frame is sent, which bounds the reconnect rate of clients
        whose backoff resets on every accepted handshake.
        """
        now = time.monotonic()
        failures = EventWebSocket._auth_failures
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

    async def open(self, *_: str, **__: str) -> None:
        """Authenticate the connection and add it to the active set."""
        token = self.get_argument("token", default=None)
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
            return

        async with get_session() as session:
            user = await get_user_by_id(session, user_id)

        if user is None or not user.is_active:
            await self._reject_unauthenticated(auth_key)
            return

        EventWebSocket._auth_failures.pop(auth_key, None)
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
    def _deliver(cls, conn: "EventWebSocket", message: str) -> None:
        """Write ``message`` on the connection's IOLoop."""
        io_loop = getattr(conn, "_io_loop", None)
        if io_loop is None:
            # Backward compatibility for older connections; write
            # synchronously when the IOLoop is not available.
            cls._send(conn, message)
        else:
            # Schedule the write on the connection's IOLoop. This is
            # thread-safe and avoids races when called from outside the
            # IOLoop thread (e.g. Celery or tests).
            io_loop.add_callback(cls._send, conn, message)

    @classmethod
    def _local_broadcast(cls, event_type: str, data: dict, topic: Optional[str]) -> None:
        message = json.dumps({"type": event_type, "data": data})
        for conn in list(cls._connections):
            if topic is not None and conn.topics and topic not in conn.topics:
                continue
            cls._deliver(conn, message)

    @classmethod
    def _local_send_to_user(cls, user_id: str, event_type: str, data: dict) -> None:
        message = json.dumps({"type": event_type, "data": data})
        for conn in list(cls._connections):
            if conn.user_id == user_id:
                cls._deliver(conn, message)

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

        The event is also published to the Redis pub/sub channel so processes
        that do not share this process's connection set (e.g. Celery workers)
        can reach every connected client.
        """
        cls._local_broadcast(event_type, data, topic)
        _publish_envelope(
            {
                "src": _PROCESS_ID,
                "kind": "broadcast",
                "type": event_type,
                "topic": topic,
                "data": data,
            }
        )

    @classmethod
    def send_to_user(cls, user_id: str, event_type: str, data: dict) -> None:
        """Send an event only to connections belonging to ``user_id``.

        Topic subscriptions are ignored: user-targeted events (e.g.
        notifications) are always delivered to every connection the user
        holds. It is a no-op when the user has no active connections.

        The event is also published to the Redis pub/sub channel so processes
        that do not share this process's connection set (e.g. Celery workers)
        can reach the user's clients.
        """
        cls._local_send_to_user(user_id, event_type, data)
        _publish_envelope(
            {
                "src": _PROCESS_ID,
                "kind": "user",
                "user_id": user_id,
                "type": event_type,
                "data": data,
            }
        )

    @classmethod
    def deliver_envelope(cls, envelope: dict) -> None:
        """Deliver an event envelope received from the pub/sub channel.

        Envelopes published by this process are skipped: they were already
        delivered to local connections by ``broadcast``/``send_to_user``.
        Malformed envelopes are ignored.
        """
        if not isinstance(envelope, dict) or envelope.get("src") == _PROCESS_ID:
            return
        event_type = envelope.get("type")
        data = envelope.get("data")
        if not isinstance(event_type, str) or not isinstance(data, dict):
            return
        kind = envelope.get("kind")
        if kind == "user":
            user_id = envelope.get("user_id")
            if isinstance(user_id, str):
                cls._local_send_to_user(user_id, event_type, data)
        elif kind == "broadcast":
            topic = envelope.get("topic")
            cls._local_broadcast(event_type, data, topic if isinstance(topic, str) else None)
