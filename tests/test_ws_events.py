"""
Tests for the authenticated, subscription-aware WebSocket event endpoint.
"""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import tornado.testing
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tornado.httpclient import HTTPError, HTTPRequest
from tornado.websocket import websocket_connect

from songhive.api.app import create_app
from songhive.api.middleware.auth import create_access_token
from songhive.app import _build_tornado_app
from songhive.config.schema import SonghiveConfig
from songhive.models.base import Base, get_session, init_db, reset_db
from songhive.services.auth import create_user
from songhive.ws.events import EventWebSocket


class TestEventWebSocket(tornado.testing.AsyncHTTPTestCase):
    """Tornado integration tests for the /ws WebSocket handler."""

    async def _create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _seed(self):
        async with get_session() as session:
            self.user = await create_user(session, "wsuser", "wsuser@example.com", "secret")
            self.inactive = await create_user(
                session,
                "wsinactive",
                "wsinactive@example.com",
                "secret",
                is_active=False,
            )
            await session.commit()

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self.config = SonghiveConfig(
            auth={"secret_key": "a" * 64},  # type: ignore
            database={"url": f"sqlite+aiosqlite:///{self._tmp / 'songhive.db'}"},  # type: ignore
            server={"cors_origins": ["http://localhost:8080"]},  # type: ignore
        )
        self.engine = create_async_engine(self.config.database.url, poolclass=NullPool)
        init_db(engine=self.engine, force=True)
        super().setUp()
        self.io_loop.run_sync(self._create_tables)
        self.io_loop.run_sync(self._seed)
        self.token = create_access_token(str(self.user.id), self.config.auth.secret_key)
        self.inactive_token = create_access_token(str(self.inactive.id), self.config.auth.secret_key)
        EventWebSocket._connections.clear()
        EventWebSocket._allowed_origins = None

    def tearDown(self):
        self.io_loop.run_sync(self.engine.dispose)
        reset_db()
        super().tearDown()
        shutil.rmtree(self._tmp, ignore_errors=True)
        EventWebSocket._connections.clear()
        EventWebSocket._allowed_origins = None

    def get_app(self):
        from fakeredis.aioredis import FakeRedis

        app = create_app(self.config)
        app.state.redis = FakeRedis(decode_responses=True)
        return _build_tornado_app(self.config, app)

    def _ws_url(self, path: str, token: str | None = None) -> str:
        url = f"ws://localhost:{self.get_http_port()}{path}"
        if token:
            url += f"?token={token}"
        return url

    def _ws_connect(self, path: str, token: str | None = None, headers: dict | None = None):
        url = self._ws_url(path, token)
        request = HTTPRequest(url, headers=headers or {})

        async def _connect():
            client = await websocket_connect(request)
            # websocket_connect returns as soon as the HTTP upgrade succeeds,
            # but the server-side open() coroutine (which adds the connection
            # to EventWebSocket._connections) may still be running. Wait for it
            # to finish, or for the connection to be rejected and closed.
            for _ in range(500):
                if EventWebSocket._connections or client.close_code is not None:
                    break
                await asyncio.sleep(0.01)
            return client

        return self.io_loop.run_sync(_connect)

    def _write(self, client, message: str) -> None:
        self.io_loop.run_sync(lambda: client.write_message(message))

    def _read(self, client, timeout: float = 2.0) -> str | bytes | None:
        return self.io_loop.run_sync(lambda: asyncio.wait_for(client.read_message(), timeout=timeout))

    def _sleep(self, seconds: float) -> None:
        self.io_loop.run_sync(lambda: asyncio.sleep(seconds))

    def test_valid_token_connects(self):
        """A valid token on /ws/?token=... is accepted."""
        client = self._ws_connect(
            "/ws/",
            self.token,
            headers={"Origin": "http://localhost:8080"},
        )
        assert client is not None
        client.close()

    def test_ws_events_alias_works(self):
        """The legacy /ws/events endpoint still accepts connections."""
        client = self._ws_connect(
            "/ws/events",
            self.token,
            headers={"Origin": "http://localhost:8080"},
        )
        assert client is not None
        client.close()

    def test_invalid_token_rejected(self):
        """A missing or invalid token closes the connection with code 4001."""
        client = self._ws_connect("/ws/", "not-a-valid-token")
        self._read(client, timeout=1.0)
        assert client.close_code == 4001

    def test_inactive_user_rejected(self):
        """A token for an inactive user is rejected."""
        client = self._ws_connect("/ws/", self.inactive_token)
        self._read(client, timeout=1.0)
        assert client.close_code == 4001

    def test_disallowed_origin_rejected(self):
        """A connection from a disallowed Origin is refused during the handshake."""
        with self.assertRaises(HTTPError) as ctx:
            self._ws_connect(
                "/ws/",
                self.token,
                headers={"Origin": "http://evil.com"},
            )
        assert ctx.exception.code == 403

    def test_subscribe_and_receive_topic_broadcast(self):
        """A subscribed client receives only matching and untopiced broadcasts."""
        client = self._ws_connect(
            "/ws/",
            self.token,
            headers={"Origin": "http://localhost:8080"},
        )
        self._write(client, json.dumps({"action": "subscribe", "topics": ["now_playing"]}))
        self._sleep(0.05)

        EventWebSocket.broadcast(
            "now_playing",
            {"track_id": "track-1"},
            topic="now_playing",
        )
        msg = self._read(client)
        assert msg is not None
        payload = json.loads(msg)
        assert payload["type"] == "now_playing"
        assert payload["data"]["track_id"] == "track-1"

        # An unrelated topic should not be delivered.
        EventWebSocket.broadcast(
            "import.completed",
            {"library_id": "lib-1"},
            topic="import",
        )
        with self.assertRaises(asyncio.TimeoutError):
            self._read(client, timeout=0.5)

        client.close()

    def test_unsubscribe_filters_broadcasts(self):
        """Unsubscribing removes a topic from the connection's filter."""
        client = self._ws_connect(
            "/ws/",
            self.token,
            headers={"Origin": "http://localhost:8080"},
        )
        self._write(
            client,
            json.dumps({"action": "subscribe", "topics": ["now_playing", "import"]}),
        )
        self._sleep(0.05)

        EventWebSocket.broadcast(
            "now_playing",
            {"track_id": "track-a"},
            topic="now_playing",
        )
        self._read(client)

        self._write(client, json.dumps({"action": "unsubscribe", "topics": ["now_playing"]}))
        self._sleep(0.05)

        # After unsubscribing from now_playing, that topic is no longer delivered,
        # but the still-subscribed import topic is.
        EventWebSocket.broadcast(
            "now_playing",
            {"track_id": "track-b"},
            topic="now_playing",
        )
        with self.assertRaises(asyncio.TimeoutError):
            self._read(client, timeout=0.5)

        EventWebSocket.broadcast(
            "import.completed",
            {"library_id": "lib-1"},
            topic="import",
        )
        msg = self._read(client)
        assert msg is not None
        payload = json.loads(msg)
        assert payload["type"] == "import.completed"

        client.close()

    def test_no_topic_broadcast_reaches_all(self):
        """Broadcasts without a topic are delivered to every connection."""
        client = self._ws_connect(
            "/ws/",
            self.token,
            headers={"Origin": "http://localhost:8080"},
        )
        self._write(client, json.dumps({"action": "subscribe", "topics": ["import"]}))
        self._sleep(0.05)

        EventWebSocket.broadcast("announcement", {"msg": "hello"})
        msg = self._read(client)
        assert msg is not None
        payload = json.loads(msg)
        assert payload["type"] == "announcement"
        client.close()
