"""
Tests for the external-library data models.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from songhive.models.artist import Artist
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_sync_run import ExternalSyncRun
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
from songhive.models.track import Track
from songhive.models.user import User


@pytest.mark.asyncio
async def test_external_models_persist_and_cascade(db_session):
    """ExternalLibrary, ExternalTrack, and ExternalSyncRun can be persisted and cascade on delete."""
    await db_session.execute(text("PRAGMA foreign_keys = ON"))
    user = User(username="externaltest", email="externaltest@example.com", password_hash="hash")
    db_session.add(user)
    await db_session.flush()
    library = Library(name="External Library", owner_id=str(user.id))
    db_session.add(library)
    await db_session.flush()

    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type="memory",
        scope="user",
    )
    db_session.add(external_library)
    await db_session.flush()

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(title="Test Track", artist_id=artist.id)
    db_session.add(track)
    await db_session.flush()

    external_track = ExternalTrack(
        external_library_id=str(external_library.id),
        track_id=str(track.id),
        provider_key="songs/test.mp3",
    )
    external_run = ExternalSyncRun(
        external_library_id=str(external_library.id),
        triggered_by="manual",
    )
    db_session.add_all([external_track, external_run])
    await db_session.flush()

    # Deleting the Songhive library cascades to the external library, which
    # cascades to tracks and sync runs.
    await db_session.delete(library)
    await db_session.flush()

    libs = await db_session.execute(select(func.count()).select_from(Library))
    assert libs.scalar() == 0, "library row was not deleted"

    result = await db_session.execute(select(func.count()).select_from(ExternalLibrary))
    assert result.scalar() == 0
    result = await db_session.execute(select(func.count()).select_from(ExternalTrack))
    assert result.scalar() == 0
    result = await db_session.execute(select(func.count()).select_from(ExternalSyncRun))
    assert result.scalar() == 0


@pytest.mark.asyncio
async def test_external_track_unique_constraint(db_session, regular_user):
    """Duplicate (external_library_id, provider_key) rows raise IntegrityError."""
    library = Library(name="External Library", owner_id=str(regular_user.id))
    db_session.add(library)
    await db_session.flush()

    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type="memory",
    )
    db_session.add(external_library)
    await db_session.flush()

    track1 = ExternalTrack(
        external_library_id=str(external_library.id),
        provider_key="songs/test.mp3",
    )
    track2 = ExternalTrack(
        external_library_id=str(external_library.id),
        provider_key="songs/test.mp3",
    )
    db_session.add_all([track1, track2])
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_track_metadata_timestamps_require_timezones(db_session):
    """Track metadata timestamp columns accept aware datetimes and reject naive ones."""
    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Test Track",
        artist_id=artist.id,
        metadata_updated_at=datetime.now(timezone.utc),
        external_metadata_synced_at=datetime.now(timezone.utc),
    )
    db_session.add(track)
    await db_session.flush()

    assert track.metadata_updated_at is not None
    assert track.metadata_updated_at.tzinfo is not None
    assert track.external_metadata_synced_at is not None
    assert track.external_metadata_synced_at.tzinfo is not None

    track.external_metadata_synced_at = datetime.now()
    with pytest.raises(StatementError) as exc_info:
        await db_session.flush()
    assert isinstance(exc_info.value.orig, ValueError)
