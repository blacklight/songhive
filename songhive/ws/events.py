"""
WebSocket handler for real-time events.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, ClassVar, Optional, Set, Union

import tornado.ioloop
import tornado.websocket

from .auth import AuthenticatedWebSocket

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


class EventWebSocket(AuthenticatedWebSocket):
    """
    WebSocket endpoint for real-time event broadcasting.

    Connections are authenticated with a ``?token=<jwt>`` query parameter. Clients
    may subscribe to named topics; broadcasts with a ``topic`` are only delivered
    to connections that have explicitly subscribed to that topic (or to
    connections with an empty topic list, which means "all").
    """

    _connections: ClassVar[Set["EventWebSocket"]] = set()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user_id: Optional[str] = None
        self.connection_id: Optional[str] = None
        self.topics: Set[str] = set()

    async def open(self, *_: str, **__: str) -> None:
        """Authenticate the connection and add it to the active set."""
        user_id = await self._authenticate_websocket()
        if user_id is None:
            return

        self.user_id = user_id
        self._io_loop = tornado.ioloop.IOLoop.current()
        EventWebSocket._connections.add(self)

    def on_message(self, message: Union[str, bytes]) -> None:
        """Handle client subscription, unsubscription, and playback-control registration."""
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("Ignoring non-JSON WebSocket message")
            return

        action = payload.get("action")
        if action == "playback-control":
            connection_id = payload.get("connection_id")
            if isinstance(connection_id, str):
                self.connection_id = connection_id
            return

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
        user_id = self.user_id
        connection_id = self.connection_id
        if user_id and connection_id:
            loop = tornado.ioloop.IOLoop.current()
            loop.add_callback(lambda: asyncio.create_task(_clear_controller(user_id, connection_id)))

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


async def _clear_controller(user_id: str, connection_id: str) -> None:
    """Clear the playback controller when its connection closes."""
    # Import locally to avoid an import cycle with ``ws.events``.
    from ..models.base import get_session
    from ..services.playback import clear_controller

    try:
        async with get_session() as session:
            await clear_controller(session, user_id, connection_id)
    except Exception:
        logger.exception("Failed to clear controller for user %s", user_id)
