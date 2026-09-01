"""
Tests for external-library streaming through the Tornado stream handler.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import tornado.testing
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from songhive.api.app import create_app
from songhive.api.middleware.auth import create_access_token
from songhive.app import _build_tornado_app
from songhive.config.schema import SonghiveConfig
from songhive.external._fake import FakeExternalAdapter
from songhive.external.registry import register_external_adapter
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.base import Base, get_session, init_db, reset_db
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.history import ListeningHistory
from songhive.models.library import Library
from songhive.models.track import Track
from songhive.services.auth import create_user
from songhive.ws.events import EventWebSocket


class TestExternalStreamHandler(tornado.testing.AsyncHTTPTestCase):
    """Integration tests for /api/v1/stream/{track_id} with external sources."""

    async def _create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _seed(self):
        self.media_path.mkdir(parents=True, exist_ok=True)

        register_external_adapter("fake", FakeExternalAdapter)

        async with get_session() as session:
            self.user = await create_user(session, "streamer", "streamer@example.com", "secret")
            self.artist = Artist(name="External Artist")
            session.add_all([self.artist])
            await session.flush()

            async def _make_library(name: str, extra_config: dict):
                test_library = Library(name=f"Library {name}", owner_id=str(self.user.id))
                session.add(test_library)
                await session.flush()

                audio_data = b"fake audio data for streaming"
                long_audio_data = b"X" * 32000
                config = {
                    "items": {
                        "song.mp3": {
                            "data": list(audio_data),
                            "mimetype": "audio/mpeg",
                        },
                        "long.mp3": {
                            "data": list(long_audio_data),
                            "mimetype": "audio/mpeg",
                        },
                        "url.mp3": {
                            "data": list(b"url audio"),
                            "mimetype": "audio/mpeg",
                        },
                        "tombstone.mp3": {
                            "data": list(audio_data),
                            "mimetype": "audio/mpeg",
                        },
                    },
                }
                config.update(extra_config)
                lib = ExternalLibrary(
                    name=name,
                    provider_type="fake",
                    library_id=test_library.id,
                    created_by_id=str(self.user.id),
                    config=config,
                    capabilities={"read_bytes": True, "stream_url": True, "download": True},
                )
                session.add(lib)
                return lib

            self.audio_data = b"fake audio data for streaming"
            self.long_audio_data = b"X" * 32000

            self.iterator_lib = await _make_library("Iterator", {})
            self.path_lib = await _make_library("Path", {"prefer_path": True})
            self.safe_url_lib = await _make_library("Safe URL", {"safe_url": True})
            self.proxy_url_lib = await _make_library("Proxy URL", {"prefer_url": True})
            await session.flush()

            def _make_external_track(lib, key, size, mime):
                et = ExternalTrack(
                    track_id=None,
                    external_library_id=lib.id,
                    provider_key=key,
                    provider_size=size,
                    provider_mime_type=mime,
                    state="active",
                )
                session.add(et)
                return et

            self.iterator_et = _make_external_track(self.iterator_lib, "song.mp3", len(self.audio_data), "audio/mpeg")
            self.path_et = _make_external_track(self.path_lib, "song.mp3", len(self.audio_data), "audio/mpeg")
            self.long_et = _make_external_track(self.iterator_lib, "long.mp3", len(self.long_audio_data), "audio/mpeg")
            self.safe_url_et = _make_external_track(self.safe_url_lib, "url.mp3", 9, "audio/mpeg")
            self.proxy_url_et = _make_external_track(self.proxy_url_lib, "url.mp3", 9, "audio/mpeg")
            self.tombstone_et = _make_external_track(
                self.iterator_lib, "tombstone.mp3", len(self.audio_data), "audio/mpeg"
            )
            self.tombstone_et.state = "tombstoned"

            await session.flush()

            def _make_track(external_track):
                track = Track(
                    title=f"Track {external_track.id}",
                    artist_id=self.artist.id,
                    audio_file_id=None,
                    owner_id=str(self.user.id),
                    visibility=Visibility.PUBLIC.value,
                    duration=1.0,
                    audio_mime_type=external_track.provider_mime_type,
                )
                session.add(track)
                return track

            self.track = _make_track(self.iterator_et)
            self.path_track = _make_track(self.path_et)
            self.long_track = _make_track(self.long_et)
            self.safe_url_track = _make_track(self.safe_url_et)
            self.proxy_url_track = _make_track(self.proxy_url_et)
            self.tombstone_track = _make_track(self.tombstone_et)

            await session.flush()

            for et, track in [
                (self.iterator_et, self.track),
                (self.path_et, self.path_track),
                (self.long_et, self.long_track),
                (self.safe_url_et, self.safe_url_track),
                (self.proxy_url_et, self.proxy_url_track),
                (self.tombstone_et, self.tombstone_track),
            ]:
                et.track_id = track.id

            self.long_track.duration = 31.0
            await session.commit()

        self.token = create_access_token(str(self.user.id), self.config.auth.secret_key)

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self.media_path = self._tmp / "media"
        self.config = SonghiveConfig(
            auth={"secret_key": "a" * 64},  # type: ignore
            database={"url": f"sqlite+aiosqlite:///{self._tmp / 'songhive.db'}"},  # type: ignore
            server={"cors_origins": ["*"]},  # type: ignore
            storage={  # type: ignore
                "backend": "local",
                "local_path": str(self.media_path),
            },
            external_libraries={  # type: ignore
                "stream_temp_dir": str(self._tmp / "external-temp"),
                "stream_max_proxy_bytes": 256 * 1024 * 1024,
            },
        )
        self.engine = create_async_engine(self.config.database.url, poolclass=NullPool)
        init_db(engine=self.engine, force=True)
        asyncio.run(self._create_tables())
        asyncio.run(self._seed())
        EventWebSocket._connections.clear()
        EventWebSocket._allowed_origins = None
        super().setUp()

    def tearDown(self):
        self.io_loop.run_sync(self.engine.dispose)
        reset_db()
        super().tearDown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def get_app(self):
        from fakeredis.aioredis import FakeRedis

        app = create_app(self.config)
        app.state.redis = FakeRedis(decode_responses=True)
        return _build_tornado_app(self.config, app)

    def _auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _mock_httpx(self, data: bytes):
        """Return a patched httpx.AsyncClient that yields the given bytes."""
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "audio/mpeg", "content-length": str(len(data))}

        async def _chunks(**kwargs):
            chunk_size = kwargs.get("chunk_size", 1024)
            for i in range(0, len(data), chunk_size):
                yield data[i : i + chunk_size]

        resp.aiter_bytes = _chunks

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.stream = MagicMock()
        client.stream.return_value.__aenter__ = AsyncMock(return_value=resp)
        client.stream.return_value.__aexit__ = AsyncMock(return_value=False)

        return patch("songhive.streaming.handler.httpx.AsyncClient", return_value=client)

    def test_external_iterator_stream(self):
        response = self.fetch(f"/api/v1/stream/{self.track.id}", headers=self._auth_header())
        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, self.audio_data)
        self.assertEqual(response.headers.get("Content-Type"), "audio/mpeg")

    def test_external_path_stream(self):
        response = self.fetch(f"/api/v1/stream/{self.path_track.id}", headers=self._auth_header())
        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, self.audio_data)

    def test_external_iterator_records_listen(self):
        async def _assert_listen():
            async with get_session() as session:
                result = await session.execute(
                    select(ListeningHistory).where(ListeningHistory.track_id == self.long_track.id)
                )
                return result.scalar_one_or_none()

        response = self.fetch(f"/api/v1/stream/{self.long_track.id}", headers=self._auth_header())
        self.assertEqual(response.code, 200)
        listen = self.io_loop.run_sync(_assert_listen)
        self.assertIsNotNone(listen)

    def test_external_tombstoned_stream(self):
        response = self.fetch(f"/api/v1/stream/{self.tombstone_track.id}", headers=self._auth_header())
        self.assertEqual(response.code, 404)

    def test_external_url_safe_redirect(self):
        response = self.fetch(
            f"/api/v1/stream/{self.safe_url_track.id}",
            headers=self._auth_header(),
            follow_redirects=False,
        )
        self.assertEqual(response.code, 302)
        self.assertIn("songhive.invalid", response.headers.get("Location", ""))

    def test_external_url_proxy(self):
        data = b"url audio"

        with self._mock_httpx(data):
            response = self.fetch(f"/api/v1/stream/{self.proxy_url_track.id}", headers=self._auth_header())

        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, data)
