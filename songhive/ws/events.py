"""
WebSocket handler for real-time events.
"""

import json
from typing import ClassVar, Set

import tornado.websocket


class EventWebSocket(tornado.websocket.WebSocketHandler):
    """
    WebSocket endpoint for real-time event broadcasting.

    Events include:
    - Import progress updates
    - Playback synchronization
    - Notifications
    """

    _connections: ClassVar[Set["EventWebSocket"]] = set()

    def check_origin(self, origin):
        # TODO: restrict origins in production
        return True

    def open(self):
        """Handle new WebSocket connection."""
        # TODO: authenticate the connection
        EventWebSocket._connections.add(self)

    def on_message(self, message):
        """Handle incoming WebSocket messages."""
        # TODO: handle client messages (e.g., subscribe to specific events)
        pass

    def on_close(self):
        """Handle WebSocket connection close."""
        EventWebSocket._connections.discard(self)

    @classmethod
    def broadcast(cls, event_type: str, data: dict):
        """Broadcast an event to all connected clients."""
        message = json.dumps({"type": event_type, "data": data})
        for conn in cls._connections:
            try:
                conn.write_message(message)
            except tornado.websocket.WebSocketClosedError:
                cls._connections.discard(conn)
