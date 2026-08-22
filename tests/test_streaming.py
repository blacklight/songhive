"""
Tests for the Tornado audio streaming endpoint.
"""

import array
import asyncio
import math
import shutil
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

import pytest
import tornado.testing
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from songhive.api.app import create_app
from songhive.api.middleware.auth import create_access_token
from songhive.app import _build_tornado_app
from songhive.config.schema import SonghiveConfig
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.base import Base, get_session, init_db, reset_db
from songhive.models.history import ListeningHistory
from songhive.models.library import Library
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.upload import Upload
from songhive.services.auth import create_user
from songhive.services.storage import StorageService
from songhive.storage import get_storage
from songhive.streaming.transcoder import Transcoder


class TestStreamHandler(tornado.testing.AsyncHTTPTestCase):
    """Integration tests for the /api/v1/stream/{track_id} Tornado handler."""

    def _create_wav(self, path: Path, duration: float, sample_rate: int = 8000) -> None:
        """Write a mono 16-bit PCM WAV file of the given duration."""
        samples = int(duration * sample_rate)
        data = array.array(
            "h",
            (int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(samples)),
        )
        with wave.open(str(path), "w") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(sample_rate)
            f.writeframes(data.tobytes())

    async def _create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _seed(self):
        self.media_path.mkdir(parents=True, exist_ok=True)
        storage_config = self.config.storage
        storage_service = StorageService(get_storage(storage_config), storage_config)

        async with get_session() as session:
            self.user = await create_user(session, "streamer", "streamer@example.com", "secret")
            self.other = await create_user(session, "other", "other@example.com", "secret")
            self.inactive = await create_user(
                session,
                "inactive",
                "inactive@example.com",
                "secret",
                is_active=False,
            )

            artist = Artist(name="Stream Artist")
            library = Library(name="Stream Library", owner_id=str(self.user.id))
            session.add_all([artist, library])
            await session.flush()

            # Short source file for format/acl/cache tests.
            short_wav = self._tmp / "short.wav"
            self._create_wav(short_wav, 1)
            with open(short_wav, "rb") as f:
                short_file = await storage_service.store_file(
                    session,
                    f,
                    "audio/wav",
                    owner_id=str(self.user.id),
                    visibility=Visibility.PUBLIC.value,
                )
            session.add(short_file)

            # Long source file for the 30-second listen threshold.
            long_wav = self._tmp / "long.wav"
            self._create_wav(long_wav, 31)
            with open(long_wav, "rb") as f:
                long_file = await storage_service.store_file(
                    session,
                    f,
                    "audio/wav",
                    owner_id=str(self.user.id),
                    visibility=Visibility.PUBLIC.value,
                )
            session.add(long_file)

            self.public_track = Track(
                title="Public Track",
                artist_id=artist.id,
                audio_file_id=short_file.id,
                owner_id=str(self.user.id),
                visibility=Visibility.PUBLIC.value,
                duration=1.0,
            )
            self.public_long_track = Track(
                title="Public Long Track",
                artist_id=artist.id,
                audio_file_id=long_file.id,
                owner_id=str(self.user.id),
                visibility=Visibility.PUBLIC.value,
                duration=31.0,
            )
            self.private_track = Track(
                title="Private Track",
                artist_id=artist.id,
                audio_file_id=short_file.id,
                owner_id=str(self.user.id),
                visibility=Visibility.PRIVATE.value,
                duration=1.0,
            )
            self.local_track = Track(
                title="Local Track",
                artist_id=artist.id,
                audio_file_id=short_file.id,
                owner_id=str(self.user.id),
                visibility=Visibility.LOCAL.value,
                duration=1.0,
            )
            session.add_all([self.public_track, self.public_long_track, self.private_track, self.local_track])
            await session.flush()

            # Upload that falls back to the short file.
            upload = Upload(
                track_id=self.public_track.id,
                library_id=library.id,
                storage_path=short_file.storage_path,
                storage_backend="local",
                mimetype="audio/wav",
                stored_file_id=short_file.id,
            )
            session.add(upload)
            await session.commit()

            self.token = create_access_token(str(self.user.id), self.config.auth.secret_key)
            self.other_token = create_access_token(str(self.other.id), self.config.auth.secret_key)
            self.inactive_token = create_access_token(str(self.inactive.id), self.config.auth.secret_key)

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self.media_path = self._tmp / "media"
        self.config = SonghiveConfig(
            auth={"secret_key": "a" * 64},  # type: ignore
            database={"url": f"sqlite+aiosqlite:///{self._tmp / 'songhive.db'}"},  # type: ignore
            storage={  # type: ignore
                "backend": "local",
                "local_path": str(self.media_path),
            },
        )
        self.engine = create_async_engine(self.config.database.url, poolclass=NullPool)
        init_db(engine=self.engine, force=True)
        asyncio.run(self._create_tables())
        asyncio.run(self._seed())
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

    def _auth_header(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def test_public_stream_no_auth(self):
        """A public track can be streamed without authentication."""
        response = self.fetch(f"/api/v1/stream/{self.public_track.id}")
        assert response.code == 200
        assert "audio/wav" in (response.headers.get("Content-Type") or "")

    def test_private_stream_no_auth_forbidden(self):
        """A private track requires authentication."""
        response = self.fetch(f"/api/v1/stream/{self.private_track.id}")
        assert response.code == 403

    def test_local_stream_no_auth_forbidden(self):
        """A local track is not accessible to anonymous users."""
        response = self.fetch(f"/api/v1/stream/{self.local_track.id}")
        assert response.code == 403

    def test_private_stream_owner(self):
        """The owner can stream a private track."""
        response = self.fetch(
            f"/api/v1/stream/{self.private_track.id}",
            headers=self._auth_header(self.token),
        )
        assert response.code == 200

    def test_invalid_token_returns_401(self):
        """An invalid or expired Bearer token returns 401."""
        response = self.fetch(
            f"/api/v1/stream/{self.public_track.id}",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert response.code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    def test_inactive_user_returns_401(self):
        """A token for an inactive user is rejected."""
        response = self.fetch(
            f"/api/v1/stream/{self.public_track.id}",
            headers=self._auth_header(self.inactive_token),
        )
        assert response.code == 401

    def test_unknown_track_returns_404(self):
        """Requesting a missing track returns 404."""
        response = self.fetch("/api/v1/stream/00000000-0000-0000-0000-000000000000")
        assert response.code == 404

    def test_range_request_returns_206(self):
        """A Range header on a passthrough request returns 206."""
        response = self.fetch(
            f"/api/v1/stream/{self.public_track.id}",
            headers={**self._auth_header(self.token), "Range": "bytes=0-1023"},
        )
        assert response.code == 206
        assert "bytes" in (response.headers.get("Content-Range") or "")
        assert len(response.body) == 1024

    def test_malformed_range_returns_400(self):
        """A malformed Range header returns 400 instead of 500."""
        response = self.fetch(
            f"/api/v1/stream/{self.public_track.id}",
            headers={**self._auth_header(self.token), "Range": "bytes=abc-def"},
        )
        assert response.code == 400

    def test_missing_file_returns_404(self):
        """A track whose backing file is missing returns 404."""
        # Delete the backing file but leave the database row.
        storage = get_storage(self.config.storage)

        async def _remove():
            assert self.public_track.audio_file_id
            stored = await self._get_stored_file(self.public_track.audio_file_id)
            path = await storage.retrieve(stored.storage_path)
            if path:
                path.unlink()

        self.io_loop.run_sync(_remove)

        response = self.fetch(
            f"/api/v1/stream/{self.public_track.id}",
            headers=self._auth_header(self.token),
        )
        assert response.code == 404

    async def _get_stored_file(self, stored_file_id: str) -> StoredFile:
        async with get_session() as session:
            ret = await session.get(StoredFile, stored_file_id)
            assert ret
            return ret

    @pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
    def test_transcode_opus(self):
        """A ?format=opus request returns audio/opus content."""
        response = self.fetch(
            f"/api/v1/stream/{self.public_track.id}?format=opus&bitrate=128k",
            headers=self._auth_header(self.token),
            request_timeout=30,
        )
        assert response.code == 200
        assert response.headers.get("Content-Type") == "audio/opus"

    def test_transcode_unsupported_format(self):
        """An unknown ?format= returns 400."""
        response = self.fetch(
            f"/api/v1/stream/{self.public_track.id}?format=wav",
            headers=self._auth_header(self.token),
        )
        assert response.code == 400

    @pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
    def test_transcode_cache_hit(self):
        """A second identical transcode request is served from the cache."""
        response = self.fetch(
            f"/api/v1/stream/{self.public_track.id}?format=opus&bitrate=128k",
            headers=self._auth_header(self.token),
            request_timeout=30,
        )
        assert response.code == 200

        with patch.object(
            Transcoder,
            "stream",
            side_effect=RuntimeError("stream should not be called on cache hit"),
        ):
            response2 = self.fetch(
                f"/api/v1/stream/{self.public_track.id}?format=opus&bitrate=128k",
                headers=self._auth_header(self.token),
                request_timeout=10,
            )
        assert response2.code == 200
        assert response2.headers.get("Content-Type") == "audio/opus"

    async def _assert_listen_recorded(self):
        # The listen record is committed after the response is fully written,
        # so give the handler's session a moment to commit.
        for _ in range(40):
            async with get_session() as session:
                track = await session.get(Track, self.public_long_track.id)
                assert track is not None
                if track.play_count == 1:
                    break
            await asyncio.sleep(0.025)

        async with get_session() as session:
            track = await session.get(Track, self.public_long_track.id)
            assert track is not None
            assert track.play_count == 1

            result = await session.execute(select(Track).where(Track.id == self.public_long_track.id))
            track = result.scalar_one()
            assert track.play_count == 1

            result = await session.execute(
                select(ListeningHistory).where(ListeningHistory.track_id == self.public_long_track.id)
            )
            assert len(result.scalars().all()) == 1

    def test_full_stream_records_listen_threshold(self):
        """Streaming a long track to completion records a listen."""
        response = self.fetch(
            f"/api/v1/stream/{self.public_long_track.id}",
            headers=self._auth_header(self.token),
            request_timeout=60,
        )
        assert response.code == 200

        self.io_loop.run_sync(self._assert_listen_recorded)

    async def _assert_listen_not_recorded(self):
        async with get_session() as session:
            track = await session.get(Track, self.public_track.id)
            assert track is not None
            assert track.play_count == 0
            result = await session.execute(select(Track).where(Track.id == self.public_track.id))
            track = result.scalar_one()
            assert track.play_count == 0

    def test_short_stream_does_not_record_listen(self):
        """A short stream does not cross the 30-second threshold."""
        response = self.fetch(
            f"/api/v1/stream/{self.public_track.id}",
            headers=self._auth_header(self.token),
            request_timeout=30,
        )
        assert response.code == 200

        self.io_loop.run_sync(self._assert_listen_not_recorded)

    async def _get_share_token(self) -> str:
        from songhive.services.sharing import create_share_token

        async with get_session() as session:
            _, raw = await create_share_token(session, "track", self.private_track.id, str(self.user.id))
            await session.commit()
            return raw

    def test_private_stream_with_share_token(self):
        """A valid share URL token grants anonymous access to a private track."""
        token = self.io_loop.run_sync(self._get_share_token)
        response = self.fetch(
            f"/api/v1/stream/{self.private_track.id}?token={token}",
        )
        assert response.code == 200
