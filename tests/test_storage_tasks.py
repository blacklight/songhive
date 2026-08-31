"""
Tests for storage Celery tasks, especially orphaned file cleanup.
"""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from songhive.config.schema import SonghiveConfig, StorageConfig
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.transcoded_file import TranscodedFile
from songhive.models.upload import Upload
from songhive.services.storage import StorageService
from songhive.storage import get_storage
from songhive.tasks.celery import _parse_crontab, celery_app, make_celery
from songhive.tasks.storage import _cleanup_orphaned_files


@pytest.fixture
def local_storage_service(tmp_path):
    """Create a StorageService backed by a local temp directory."""
    config = StorageConfig(backend="local", local_path=tmp_path / "media")
    backend = get_storage(config)
    return StorageService(backend, config)


@pytest.mark.asyncio
async def test_cleanup_orphaned_files_removes_only_orphans(db_session, local_storage_service, regular_user):
    """Only StoredFile rows with no Track/Album/Upload references are deleted."""
    contents = [b"track", b"cover", b"upload", b"orphan"]
    stored_files = []
    for i, content in enumerate(contents):
        file = io.BytesIO(content)
        stored_file = await local_storage_service.store_file(
            db_session,
            file,
            "audio/mpeg",
            original_filename=f"file{i}.mp3",
        )
        db_session.add(stored_file)
        await db_session.flush()
        stored_files.append(stored_file)

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Test Track",
        artist_id=artist.id,
        audio_file_id=stored_files[0].id,
    )
    album = Album(
        title="Test Album",
        artist_id=artist.id,
        cover_file_id=stored_files[1].id,
    )
    library = Library(name="Test Library", owner_id=regular_user.id)
    db_session.add_all([track, album, library])
    await db_session.flush()

    upload = Upload(
        track_id=track.id,
        library_id=library.id,
        storage_path="legacy",
        storage_backend="local",
        mimetype="audio/mpeg",
        stored_file_id=stored_files[2].id,
    )
    db_session.add(upload)
    await db_session.flush()

    for stored_file in stored_files:
        assert await local_storage_service.backend.exists(stored_file.storage_path) is True

    count = await _cleanup_orphaned_files(local_storage_service.backend, db_session)

    assert count == 1

    result = await db_session.execute(select(StoredFile).where(StoredFile.id == stored_files[3].id))
    assert result.scalar_one_or_none() is None

    for stored_file in stored_files[:3]:
        result = await db_session.execute(select(StoredFile).where(StoredFile.id == stored_file.id))
        assert result.scalar_one_or_none() is stored_file

    assert await local_storage_service.backend.exists(stored_files[3].storage_path) is False
    for stored_file in stored_files[:3]:
        assert await local_storage_service.backend.exists(stored_file.storage_path) is True


@pytest.mark.asyncio
async def test_cleanup_orphaned_files_preserves_all_references(db_session, local_storage_service, regular_user):
    """Stored files referenced by Artist, Library, Playlist, Track image or TranscodedFile survive cleanup."""
    contents = [
        b"artist-image",
        b"artist-cover",
        b"library-cover",
        b"playlist-image",
        b"track-image",
        b"transcoded",
        b"orphan",
    ]
    stored_files = []
    for i, content in enumerate(contents):
        file = io.BytesIO(content)
        stored_file = await local_storage_service.store_file(
            db_session,
            file,
            "image/png" if i != 5 else "audio/mpeg",
            original_filename=f"file{i}.png",
        )
        stored_files.append(stored_file)
    await db_session.flush()

    artist = Artist(
        name="Test Artist",
        image_file_id=stored_files[0].id,
        cover_file_id=stored_files[1].id,
    )
    db_session.add(artist)
    await db_session.flush()

    library = Library(
        name="Test Library",
        owner_id=regular_user.id,
        cover_file_id=stored_files[2].id,
    )
    playlist = Playlist(
        name="Test Playlist",
        owner_id=regular_user.id,
        image_file_id=stored_files[3].id,
    )
    track = Track(
        title="Test Track",
        artist_id=artist.id,
        image_file_id=stored_files[4].id,
    )
    db_session.add_all([library, playlist, track])
    await db_session.flush()

    db_session.add(
        TranscodedFile(
            track_id=track.id,
            format="mp3",
            bitrate="128k",
            stored_file_id=stored_files[5].id,
        )
    )
    await db_session.flush()

    count = await _cleanup_orphaned_files(local_storage_service.backend, db_session)

    assert count == 1

    for stored_file in stored_files[:-1]:
        result = await db_session.execute(select(StoredFile).where(StoredFile.id == stored_file.id))
        assert result.scalar_one_or_none() is stored_file

    result = await db_session.execute(select(StoredFile).where(StoredFile.id == stored_files[-1].id))
    assert result.scalar_one_or_none() is None

    assert await local_storage_service.backend.exists(stored_files[-1].storage_path) is False
    for stored_file in stored_files[:-1]:
        assert await local_storage_service.backend.exists(stored_file.storage_path) is True


def test_cleanup_orphaned_files_task_config():
    """The cleanup task is registered and scheduled for 03:00 UTC."""
    assert "songhive.tasks.storage.cleanup_orphaned_files" in celery_app.tasks

    assert celery_app.conf.task_default_queue == "celery"

    schedule = celery_app.conf.beat_schedule["cleanup-orphaned-files"]
    assert schedule["task"] == "songhive.tasks.storage.cleanup_orphaned_files"
    assert schedule["schedule"].hour == {3}
    assert schedule["schedule"].minute == {0}


def test_celery_config_default_cleanup_schedule(config):
    """``CeleryConfig`` exposes a default cleanup schedule of 03:00 UTC."""
    assert config.celery.cleanup_orphaned_files_schedule == "0 3 * * *"


def test_make_celery_custom_cleanup_schedule():
    """``make_celery`` honors a custom crontab expression for the cleanup task."""
    app = make_celery(cleanup_orphaned_files_schedule="30 6 * * *")
    schedule = app.conf.beat_schedule["cleanup-orphaned-files"]["schedule"]
    assert schedule.hour == {6}
    assert schedule.minute == {30}


def test_parse_crontab_rejects_invalid_expression():
    """``_parse_crontab`` raises for cron expressions without exactly 5 fields."""
    with pytest.raises(ValueError):
        _parse_crontab("0 3 * *")


@pytest.mark.asyncio
async def test_cleanup_orphaned_files_logs_delete_error(
    db_session, local_storage_service, regular_user, caplog, monkeypatch
):
    """A storage backend delete failure during cleanup is logged but not re-raised."""
    file = io.BytesIO(b"orphan")
    stored_file = await local_storage_service.store_file(
        db_session,
        file,
        "audio/mpeg",
    )
    db_session.add(stored_file)
    await db_session.flush()

    monkeypatch.setattr(
        local_storage_service.backend,
        "delete",
        AsyncMock(side_effect=RuntimeError("delete failed")),
    )

    with caplog.at_level("ERROR"):
        count = await _cleanup_orphaned_files(local_storage_service.backend, db_session)

    assert count == 1
    assert "delete failed" in caplog.text
    assert "Failed to delete backing file" in caplog.text


class _FakeSession:
    """Yield a fixed session for the cleanup task wrapper test."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_, **__):
        return False


def test_cleanup_orphaned_files_task(monkeypatch):
    """The ``cleanup_orphaned_files`` Celery task wires config, storage and DB."""
    config = SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": "sqlite+aiosqlite:///:memory:"},
        storage={"backend": "local", "local_path": "/tmp/media"},
    )
    storage = MagicMock()
    session = MagicMock()

    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: config)
    monkeypatch.setattr("songhive.models.base.init_db", lambda *a, **k: None)
    monkeypatch.setattr("songhive.storage.get_storage", lambda *a, **k: storage)
    monkeypatch.setattr("songhive.models.base.get_session", lambda: _FakeSession(session))

    async def _fake_helper(s, sess):
        assert s is storage
        assert sess is session
        return 7

    monkeypatch.setattr("songhive.tasks.storage._cleanup_orphaned_files", _fake_helper)

    from songhive.tasks.storage import cleanup_orphaned_files

    result = cleanup_orphaned_files()
    assert result == 7


def test_load_celery_config_falls_back_to_defaults_on_error(monkeypatch, caplog):
    """``_load_celery_config`` returns defaults when config loading fails."""
    monkeypatch.setattr("songhive.config.load_config", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad config")))

    from songhive.tasks.celery import _load_celery_config

    with caplog.at_level("INFO", logger="songhive.tasks.celery"):
        broker, backend, schedule = _load_celery_config()

    assert broker == "redis://localhost:6379/1"
    assert backend == "redis://localhost:6379/2"
    assert schedule == "0 3 * * *"
    assert "Could not load Songhive config for Celery" in caplog.text


def test_make_celery_uses_default_cleanup_schedule():
    """``make_celery`` uses the default 03:00 UTC schedule when none is supplied."""
    from songhive.tasks.celery import make_celery

    app = make_celery()
    schedule = app.conf.beat_schedule["cleanup-orphaned-files"]["schedule"]
    assert schedule.hour == {3}
    assert schedule.minute == {0}
