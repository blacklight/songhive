"""
Tests for the external-library sync service.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from songhive.external._fake import FakeExternalAdapter
from songhive.external.errors import ExternalLibraryError
from songhive.external.registry import register_external_adapter
from songhive.external.sync import sync_external_library
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_sync_run import ExternalSyncRun
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import secrets


@pytest.fixture(autouse=True)
def _register_fake_adapter():
    """Register the fake adapter for every test."""
    register_external_adapter("fake", FakeExternalAdapter)


@pytest.fixture
def _make_user(db_session):
    """Return a helper that creates a user."""

    async def _inner(username: str) -> User:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash="x" * 60,
            role="user",
            is_active=True,
        )
        db_session.add(user)
        await db_session.flush()
        return user

    return _inner


@pytest.fixture
def _make_library(db_session, _make_user):
    """Return a helper that creates a Songhive library."""

    async def _inner(name: str, owner: User) -> Library:
        library = Library(name=name, owner_id=str(owner.id), visibility="private")
        db_session.add(library)
        await db_session.flush()
        return library

    return _inner


@pytest.fixture
def _make_external_library(db_session, _make_user, _make_library):
    """Return a helper that creates an ExternalLibrary with encrypted fake config."""

    async def _inner(
        items: dict,
        owner: User | None = None,
        library: Library | None = None,
        enabled: bool = True,
    ) -> ExternalLibrary:
        if owner is None:
            owner = await _make_user("elib")
        if library is None:
            library = await _make_library("External Library", owner)

        config = {"items": items}
        encrypted = secrets.encrypt_json(config)
        external_library = ExternalLibrary(
            library_id=str(library.id),
            provider_type="fake",
            config=encrypted,
            enabled=enabled,
            created_by_id=str(owner.id),
        )
        db_session.add(external_library)
        await db_session.flush()
        return external_library

    return _inner


@pytest.mark.asyncio
async def test_first_sync_creates_tracks(db_session, fake_redis, _make_external_library):
    """First sync creates Track, LibraryTrack and ExternalTrack rows and a success run."""
    items = {
        "track1.flac": {
            "data": list(b"audio1"),
            "metadata": {
                "title": "Track 1",
                "artist": "Artist A",
                "album": "Album A",
                "duration": 180.0,
            },
        },
        "track2.flac": {
            "data": list(b"audio2"),
            "metadata": {
                "title": "Track 2",
                "artist": "Artist A",
                "album": "Album A",
                "duration": 200.0,
            },
        },
    }
    external_library = await _make_external_library(items)

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        triggered_by_user_id=external_library.created_by_id,
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.items_seen == 2
    assert run.tracks_created == 2
    assert run.tracks_updated == 0
    assert run.tracks_shadowed == 0
    assert run.tracks_missing == 0
    assert run.tracks_failed == 0

    external_library = await db_session.get(ExternalLibrary, external_library.id)
    assert external_library.last_sync_status == "success"

    assert (await db_session.scalar(select(func.count()).select_from(Track))) == 2
    assert (await db_session.scalar(select(func.count()).select_from(LibraryTrack))) == 2
    assert (await db_session.scalar(select(func.count()).select_from(ExternalTrack))) == 2

    ext_tracks = (
        (
            await db_session.execute(
                select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
            )
        )
        .scalars()
        .all()
    )
    for ext_track in ext_tracks:
        assert ext_track.state == "active"
        assert ext_track.track_id is not None
        assert ext_track.metadata_fingerprint is not None
        track = await db_session.get(Track, ext_track.track_id)
        assert track is not None
        assert track.source == "external"
        assert track.external_metadata_synced_at is not None
        assert track.metadata_updated_at is None


@pytest.mark.asyncio
async def test_resync_unchanged_leaves_tracks(db_session, fake_redis, _make_external_library):
    """Re-syncing unchanged metadata leaves Track fields and reports tracks_updated=0."""
    items = {
        "track1.flac": {
            "data": list(b"audio1"),
            "metadata": {
                "title": "Track 1",
                "artist": "Artist A",
                "album": "Album A",
                "duration": 180.0,
            },
        },
    }
    external_library = await _make_external_library(items)

    await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    result = await db_session.execute(select(Track).join(ExternalTrack, ExternalTrack.track_id == Track.id))
    track = result.scalar_one()
    first_synced_at = track.external_metadata_synced_at
    first_title = track.title

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.tracks_updated == 0
    assert track.title == first_title
    assert track.external_metadata_synced_at is not None
    # A re-sync that confirms the metadata is unchanged may refresh the sync timestamp.
    assert track.external_metadata_synced_at >= first_synced_at


@pytest.mark.asyncio
async def test_resync_changed_updates_tracks(db_session, fake_redis, _make_external_library):
    """Re-syncing changed provider metadata with no local edits updates the Track."""
    items = {
        "track1.flac": {
            "data": list(b"audio1"),
            "metadata": {
                "title": "Track 1",
                "artist": "Artist A",
                "album": "Album A",
                "duration": 180.0,
            },
        },
    }
    external_library = await _make_external_library(items)

    await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    # Mutate the in-memory config through the decrypted dict and re-encrypt.
    ext_lib = await db_session.get(ExternalLibrary, external_library.id)
    config = secrets.decrypt_json(ext_lib.config)
    config["items"]["track1.flac"]["metadata"]["title"] = "Track 1 Updated"
    ext_lib.config = secrets.encrypt_json(config)
    await db_session.flush()

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.tracks_updated == 1

    track = (
        (await db_session.execute(select(Track).join(ExternalTrack, ExternalTrack.track_id == Track.id)))
        .scalars()
        .one()
    )
    assert track.title == "Track 1 Updated"


@pytest.mark.asyncio
async def test_resync_conflict_keeps_local_edits(db_session, fake_redis, _make_external_library):
    """Re-syncing changed provider metadata after a local edit keeps Songhive fields and enqueues write-back."""
    items = {
        "track1.flac": {
            "data": list(b"audio1"),
            "metadata": {
                "title": "Track 1",
                "artist": "Artist A",
                "album": "Album A",
                "duration": 180.0,
            },
        },
    }
    external_library = await _make_external_library(items)

    await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    ext_track = (
        (
            await db_session.execute(
                select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
            )
        )
        .scalars()
        .one()
    )
    track = await db_session.get(Track, ext_track.track_id)
    track.title = "Local Title"
    track.metadata_updated_at = datetime.now(timezone.utc)
    await db_session.flush()

    ext_lib = await db_session.get(ExternalLibrary, external_library.id)
    config = secrets.decrypt_json(ext_lib.config)
    config["items"]["track1.flac"]["metadata"]["title"] = "Provider Title"
    ext_lib.config = secrets.encrypt_json(config)
    await db_session.flush()

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert track.title == "Local Title"
    assert ext_track.write_back_pending is True
    assert ext_track.sync_error is not None


@pytest.mark.asyncio
async def test_sha256_collision_shadows_item(db_session, fake_redis, _make_user, _make_library, _make_external_library):
    """An external item matching an existing StoredFile becomes shadowed and creates no Track."""
    owner = await _make_user("shadow")
    library = await _make_library("Shadow Library", owner)

    data = list(b"same_audio_bytes")
    import hashlib

    sha256 = hashlib.sha256(bytes(data)).hexdigest()

    stored_file = StoredFile(
        storage_path="local/track.flac",
        storage_backend="local",
        content_type="audio/flac",
        size=len(data),
        sha256=sha256,
        owner_id=str(owner.id),
        visibility="private",
    )
    db_session.add(stored_file)
    await db_session.flush()

    items = {
        "shadow.flac": {
            "data": data,
            "metadata": {
                "title": "Shadow Track",
                "artist": "Artist S",
                "album": "Album S",
                "duration": 120.0,
            },
        },
    }
    external_library = await _make_external_library(items, owner=owner, library=library)

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.tracks_shadowed == 1
    assert run.tracks_created == 0
    assert run.tracks_updated == 0

    ext_track = (
        (
            await db_session.execute(
                select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
            )
        )
        .scalars()
        .one()
    )
    assert ext_track.state == "shadowed"
    assert ext_track.track_id is None
    assert ext_track.sha256 == sha256


@pytest.mark.asyncio
async def test_tombstoned_track_not_recreated(db_session, fake_redis, _make_external_library):
    """A tombstoned ExternalTrack is not reactivated by a normal sync."""
    items = {
        "track1.flac": {
            "data": list(b"audio1"),
            "metadata": {
                "title": "Track 1",
                "artist": "Artist A",
                "album": "Album A",
                "duration": 180.0,
            },
        },
    }
    external_library = await _make_external_library(items)

    await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    ext_track = (
        (
            await db_session.execute(
                select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
            )
        )
        .scalars()
        .one()
    )
    ext_track.state = "tombstoned"
    await db_session.flush()

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.items_seen == 1
    assert run.tracks_created == 0
    assert run.tracks_updated == 0
    ext_track = (
        (
            await db_session.execute(
                select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
            )
        )
        .scalars()
        .one()
    )
    assert ext_track.state == "tombstoned"


@pytest.mark.asyncio
async def test_missing_item_marked_missing(db_session, fake_redis, _make_external_library):
    """A provider item that disappears between full syncs becomes missing."""
    items = {
        "track1.flac": {
            "data": list(b"audio1"),
            "metadata": {
                "title": "Track 1",
                "artist": "Artist A",
            },
        },
        "track2.flac": {
            "data": list(b"audio2"),
            "metadata": {
                "title": "Track 2",
                "artist": "Artist A",
            },
        },
    }
    external_library = await _make_external_library(items)

    await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    ext_lib = await db_session.get(ExternalLibrary, external_library.id)
    config = secrets.decrypt_json(ext_lib.config)
    del config["items"]["track2.flac"]
    ext_lib.config = secrets.encrypt_json(config)
    await db_session.flush()

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.tracks_missing == 1

    missing = (
        (
            await db_session.execute(
                select(ExternalTrack).where(
                    ExternalTrack.external_library_id == str(external_library.id),
                    ExternalTrack.state == "missing",
                )
            )
        )
        .scalars()
        .one()
    )
    assert missing.provider_key == "track2.flac"


@pytest.mark.asyncio
async def test_concurrent_sync_raises(db_session, fake_redis, _make_external_library):
    """Two concurrent syncs for the same library result in an error for the second."""
    items = {
        "track1.flac": {
            "data": list(b"audio1"),
            "metadata": {
                "title": "Track 1",
                "artist": "Artist A",
            },
        },
    }
    external_library = await _make_external_library(items)

    async def _sync():
        return await sync_external_library(
            db_session,
            str(external_library.id),
            triggered_by="manual",
            redis=fake_redis,
        )

    results = await asyncio.gather(_sync(), _sync(), return_exceptions=True)

    successes = [r for r in results if isinstance(r, ExternalSyncRun)]
    errors = [r for r in results if isinstance(r, ExternalLibraryError)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert str(errors[0]) == "sync already running"


@pytest.mark.asyncio
async def test_sync_disabled_library_raises(db_session, fake_redis, _make_external_library):
    """Syncing a disabled external library raises an error."""
    external_library = await _make_external_library({}, enabled=False)

    with pytest.raises(ExternalLibraryError, match="disabled"):
        await sync_external_library(
            db_session,
            str(external_library.id),
            triggered_by="manual",
            redis=fake_redis,
        )


@pytest.mark.asyncio
async def test_sync_unknown_library_raises(db_session, fake_redis):
    """Syncing a non-existent external library raises an error."""
    with pytest.raises(ExternalLibraryError, match="not found"):
        await sync_external_library(
            db_session,
            "missing-id",
            triggered_by="manual",
            redis=fake_redis,
        )
