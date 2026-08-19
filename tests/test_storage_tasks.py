"""
Tests for storage Celery tasks, especially orphaned file cleanup.
"""

import io

import pytest
from sqlalchemy import select

from songhive.config.schema import StorageConfig
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
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


def test_cleanup_orphaned_files_task_config():
    """The cleanup task is registered, routed, and scheduled for 03:00 UTC."""
    assert "songhive.tasks.storage.cleanup_orphaned_files" in celery_app.tasks

    routes = celery_app.conf.task_routes
    assert "songhive.tasks.storage.*" in routes
    assert routes["songhive.tasks.storage.*"] == {"queue": "storage"}

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
