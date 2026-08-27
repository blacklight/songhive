"""Tests for the sync_track_tags Celery task."""

import asyncio
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from songhive.config.schema import SonghiveConfig
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.base import Base
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.music.metadata import AudioMetadataWrite, extract_metadata
from songhive.tasks.tags import _build_metadata, _prepare_cover_art, _resolve_cover_file, sync_track_tags


def _make_silence(tmp_path: Path, ext: str = "mp3") -> Path:
    """Generate a short sine-wave audio file."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")

    path = tmp_path / f"sample.{ext}"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.5",
            "-b:a",
            "128k",
            "-ac",
            "1",
            "-ar",
            "44100",
            "-c:a",
            "libmp3lame",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


class _LocalSessionFactory:
    """Create a fresh async session backed by the given database URL."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    async def __aenter__(self):
        self.engine = create_async_engine(self.database_url)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.factory()
        await self.session.__aenter__()
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.__aexit__(exc_type, exc, tb)
        await self.engine.dispose()


class _FakeRedis:
    """In-memory fake Redis with an async set/delete interface."""

    def __init__(self):
        self._data = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    async def delete(self, key):
        self._data.pop(key, None)
        return 1


def _patch_tags_task_env(monkeypatch, tmp_path, db_url, media_dir):
    """Patch the task's global dependencies so it can run in a test DB."""
    config = SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": db_url},
        storage={"backend": "local", "local_path": str(media_dir)},
    )

    monkeypatch.setattr("songhive.config.load_config", lambda *_: config)
    monkeypatch.setattr("songhive.models.base.init_db", lambda url: None)
    monkeypatch.setattr("songhive.services.redis.get_redis_client", lambda cfg: _FakeRedis())

    # The task expects ``get_session`` to be an async context manager that
    # yields a session.  Replace it with the local factory.
    @asynccontextmanager
    async def _get_session():
        async with _LocalSessionFactory(db_url) as session:
            yield session

    monkeypatch.setattr("songhive.models.base.get_session", _get_session)


def test_sync_track_tags_rewrites_tags(tmp_path, monkeypatch):
    """The Celery task writes database metadata into a track's audio file."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tags.db'}"
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    _patch_tags_task_env(monkeypatch, tmp_path, db_url, media_dir)

    audio_path = _make_silence(tmp_path)
    storage_path = "tracks/test.mp3"
    stored_file_path = media_dir / storage_path
    stored_file_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(audio_path, stored_file_path)

    async def _setup():
        async with _LocalSessionFactory(db_url) as session:
            artist = Artist(name="Test Artist")
            session.add(artist)
            await session.flush()

            album = Album(title="Test Album", artist_id=artist.id, release_year=2022)
            session.add(album)
            await session.flush()

            stored_file = StoredFile(
                sha256="a" * 64,
                size=stored_file_path.stat().st_size,
                storage_path=storage_path,
                storage_backend="local",
                content_type="audio/mpeg",
                owner_id=None,
                visibility="private",
            )
            track = Track(
                title="Test Title",
                artist_id=artist.id,
                album_id=album.id,
                audio_file=stored_file,
                track_number=2,
                disc_number=1,
                genre="Rock",
                owner_id=None,
                visibility="private",
            )
            session.add_all([stored_file, track])
            await session.commit()
            return str(track.id), str(stored_file.id)

    track_id, _ = asyncio.run(_setup())

    result = sync_track_tags(track_id)
    assert result is True

    meta = extract_metadata(stored_file_path)
    assert meta.title == "Test Title"
    assert meta.artist == "Test Artist"
    assert meta.album == "Test Album"
    assert meta.track_number == 2
    assert meta.disc_number == 1
    assert meta.genre == "Rock"
    assert meta.year == 2022

    # The stored file size is updated to reflect the new metadata.
    stored = asyncio.run(_get_stored_file(db_url, track_id))
    assert stored.size == stored_file_path.stat().st_size


async def _get_stored_file(db_url, track_id):
    async with _LocalSessionFactory(db_url) as session:
        from sqlalchemy import select

        from songhive.models.track import Track

        result = await session.execute(select(Track).where(Track.id == track_id).options())
        track = result.scalar_one()
        return track.audio_file


def test_sync_track_tags_missing_track_returns_true(tmp_path, monkeypatch):
    """The task returns True when the track does not exist."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'missing.db'}"
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    _patch_tags_task_env(monkeypatch, tmp_path, db_url, media_dir)

    async def _create_tables():
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_tables())

    result = sync_track_tags("00000000-0000-0000-0000-000000000000")
    assert result is True


def test_sync_track_tags_respects_existing_lock(tmp_path, monkeypatch):
    """The task returns True without work when the track lock is held."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'locked.db'}"
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    _patch_tags_task_env(monkeypatch, tmp_path, db_url, media_dir)

    lock_held = _FakeRedis()
    lock_held._data["sync_tags:track-1"] = "1"
    monkeypatch.setattr("songhive.services.redis.get_redis_client", lambda cfg: lock_held)

    async def _create_tables():
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_tables())

    result = sync_track_tags("track-1")
    assert result is True


def test_build_metadata_uses_track_and_album():
    """_build_metadata combines track and related album/artist fields."""
    track = MagicMock()
    track.title = "Title"
    track.track_number = 5
    track.disc_number = 2
    track.genre = "Pop"
    track.release_year = 2021
    track.artist = MagicMock(name="Artist")
    track.artist.name = "Artist Name"
    track.album = MagicMock(name="Album")
    track.album.title = "Album Title"
    track.album.release_year = 2022

    meta = _build_metadata(track)
    assert meta.title == "Title"
    assert meta.artist == "Artist Name"
    assert meta.album == "Album Title"
    assert meta.track_number == 5
    assert meta.disc_number == 2
    assert meta.genre == "Pop"
    # Track year takes precedence over album year.
    assert meta.year == 2021


def test_resolve_cover_file_prefers_track_image():
    """_resolve_cover_file prefers track image_file over album cover."""
    track = MagicMock()
    track_image = MagicMock()
    album_cover = MagicMock()
    track.image_file = track_image
    track.album = MagicMock()
    track.album.cover_file = album_cover

    assert _resolve_cover_file(track) is track_image

    track.image_file = None
    assert _resolve_cover_file(track) is album_cover

    track.album = None
    assert _resolve_cover_file(track) is None


@pytest.mark.asyncio
async def test_prepare_cover_art_clears_when_no_cover_resolves():
    """_prepare_cover_art flags embedded cover art for clearing when no cover is found."""
    track = MagicMock()
    track.image_file = None
    track.album = None
    meta = AudioMetadataWrite(title="Title")
    result = await _prepare_cover_art(MagicMock(), track, meta)
    assert result is None
    assert meta.clear_cover_art is True
