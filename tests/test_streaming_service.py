"""
Tests for streaming service helpers.
"""

import io

import pytest
from sqlalchemy import select

from songhive.config.schema import StorageConfig
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.history import ListeningHistory
from songhive.models.library import Library
from songhive.models.track import Track
from songhive.models.transcoded_file import TranscodedFile
from songhive.models.upload import Upload
from songhive.services.storage import StorageService
from songhive.services.streaming import (
    cache_transcode,
    get_cached_transcode,
    record_listen,
    resolve_track_file,
)
from songhive.storage import get_storage


@pytest.fixture
def local_storage_service(tmp_path):
    """Create a StorageService backed by a local temp directory."""
    config = StorageConfig(backend="local", local_path=tmp_path / "media")
    backend = get_storage(config)
    return StorageService(backend, config)


@pytest.mark.asyncio
async def test_resolve_track_file_returns_audio_file(db_session, regular_user, local_storage_service):
    """A track with an audio_file_id resolves to that StoredFile."""
    file_like = io.BytesIO(b"audio data")
    stored = await local_storage_service.store_file(
        db_session,
        file_like,
        "audio/mpeg",
        owner_id=str(regular_user.id),
    )
    db_session.add(stored)

    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Track",
        artist_id=artist.id,
        audio_file_id=stored.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    result = await resolve_track_file(db_session, track.id)
    assert result is stored


@pytest.mark.asyncio
async def test_resolve_track_file_falls_back_to_upload(db_session, regular_user, local_storage_service):
    """A track without an audio_file_id falls back to its upload's StoredFile."""
    file_like = io.BytesIO(b"audio data")
    stored = await local_storage_service.store_file(
        db_session,
        file_like,
        "audio/mpeg",
        owner_id=str(regular_user.id),
    )
    db_session.add(stored)

    artist = Artist(name="Artist")
    library = Library(name="Library", owner_id=str(regular_user.id))
    db_session.add_all([artist, library])
    await db_session.flush()

    track = Track(
        title="Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    upload = Upload(
        track_id=track.id,
        library_id=library.id,
        storage_path="legacy",
        storage_backend="local",
        mimetype="audio/mpeg",
        stored_file_id=stored.id,
    )
    db_session.add(upload)
    await db_session.flush()

    result = await resolve_track_file(db_session, track.id)
    assert result is stored


@pytest.mark.asyncio
async def test_resolve_track_file_missing(db_session):
    """A missing track resolves to None."""
    result = await resolve_track_file(db_session, "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_cache_transcode_and_retrieve(db_session, regular_user, local_storage_service):
    """cache_transcode stores output and get_cached_transcode can retrieve it."""
    source_file = io.BytesIO(b"audio data")
    source = await local_storage_service.store_file(
        db_session,
        source_file,
        "audio/mpeg",
        owner_id=str(regular_user.id),
    )
    db_session.add(source)

    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Track",
        artist_id=artist.id,
        audio_file_id=source.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    output = b"transcoded output"
    cached = await cache_transcode(
        db_session,
        local_storage_service,
        track,
        "opus",
        "128k",
        output,
        "audio/opus",
    )

    assert cached.content_type == "audio/opus"
    row = await db_session.scalar(select(TranscodedFile).where(TranscodedFile.track_id == track.id))
    assert row is not None

    cached2 = await get_cached_transcode(db_session, track.id, "opus", "128k")
    assert cached2 is not None
    assert cached2.id == cached.id


@pytest.mark.asyncio
async def test_cache_transcode_duplicate_returns_existing(db_session, regular_user, local_storage_service):
    """Cache duplicate (same track/format/bitrate) resolves to the existing file."""
    source_file = io.BytesIO(b"audio data")
    source = await local_storage_service.store_file(
        db_session,
        source_file,
        "audio/mpeg",
        owner_id=str(regular_user.id),
    )
    db_session.add(source)

    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Track",
        artist_id=artist.id,
        audio_file_id=source.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    output = b"transcoded output"
    cached1 = await cache_transcode(
        db_session,
        local_storage_service,
        track,
        "opus",
        "128k",
        output,
        "audio/opus",
    )
    cached2 = await cache_transcode(
        db_session,
        local_storage_service,
        track,
        "opus",
        "128k",
        output,
        "audio/opus",
    )

    assert cached1.id == cached2.id


@pytest.mark.asyncio
async def test_record_listen(db_session, regular_user):
    """record_listen creates a ListeningHistory row and increments play_count."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    await record_listen(db_session, str(regular_user.id), track.id)

    result = await db_session.execute(select(ListeningHistory).where(ListeningHistory.track_id == track.id))
    assert len(result.scalars().all()) == 1
    assert track.play_count == 1
