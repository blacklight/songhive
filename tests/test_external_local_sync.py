"""
Integration tests for syncing a local external library end-to-end.
"""

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from songhive.config.schema import SonghiveConfig
from songhive.external.sync import sync_external_library
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import metadata as metadata_module
from songhive.services import secrets
from songhive.services.metadata import AudioMetadata


def _write_file(path: Path, content: bytes = b"audio content") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _make_metadata(title: str) -> AudioMetadata:
    return AudioMetadata(
        title=title,
        artist="Artist",
        album="Album",
        track_number=1,
        disc_number=1,
        duration=180.0,
        genre="Rock",
        year=2024,
        mimetype="audio/mpeg",
        raw_tags={},
    )


@pytest.fixture
def local_sync_env(tmp_path, monkeypatch):
    """Set up the environment for local-adapter integration tests."""
    root = tmp_path / "local_music"
    root.mkdir()
    monkeypatch.setenv("SONGHIVE_EXTERNAL_LIBRARIES__LOCAL_ROOTS", str(root))

    def _extract(_file_path: Path) -> AudioMetadata:
        return _make_metadata(f"Title {_file_path.name}")

    monkeypatch.setattr(metadata_module, "extract_metadata", _extract)

    return root


@pytest.fixture
def _make_local_external_library(db_session):
    """Return a helper that creates a user, Songhive library, and local external library."""

    async def _inner(root: Path, owner: User | None = None, library: Library | None = None) -> ExternalLibrary:
        if owner is None:
            owner = User(
                username="localuser",
                email="localuser@example.com",
                password_hash="x" * 60,
                role="user",
                is_active=True,
            )
            db_session.add(owner)
            await db_session.flush()

        if library is None:
            library = Library(name="Local Library", owner_id=str(owner.id), visibility="private")
            db_session.add(library)
            await db_session.flush()

        config = {
            "root": str(root),
            "allow_hashing": True,
            "fast_hash": True,
        }
        encrypted = secrets.encrypt_json(config)
        external_library = ExternalLibrary(
            library_id=str(library.id),
            provider_type="local",
            config=encrypted,
            enabled=True,
            created_by_id=str(owner.id),
        )
        db_session.add(external_library)
        await db_session.flush()
        return external_library

    return _inner


@pytest.mark.asyncio
async def test_local_sync_creates_tracks(db_session, fake_redis, local_sync_env, _make_local_external_library):
    """A first full sync of a local library creates Track and ExternalTrack rows."""
    _write_file(local_sync_env / "track1.mp3")
    _write_file(local_sync_env / "sub" / "track2.flac")

    external_library = await _make_local_external_library(local_sync_env)
    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.items_seen == 2
    assert run.tracks_created == 2

    track_count = await db_session.scalar(select(func.count()).select_from(Track))
    assert track_count == 2

    ext_tracks = (
        (
            await db_session.execute(
                select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
            )
        )
        .scalars()
        .all()
    )
    assert len(ext_tracks) == 2
    for ext_track in ext_tracks:
        assert ext_track.state == "active"
        assert ext_track.provider_key in {"track1.mp3", "sub/track2.flac"}


@pytest.mark.asyncio
async def test_local_resync_deletes_mark_missing(db_session, fake_redis, local_sync_env, _make_local_external_library):
    """Removing a file between full syncs marks its ExternalTrack as missing."""
    track_path = _write_file(local_sync_env / "gone.mp3")

    external_library = await _make_local_external_library(local_sync_env)
    await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    track_path.unlink()

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.tracks_missing == 1

    ext_track = (
        await db_session.execute(
            select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
        )
    ).scalar_one()
    assert ext_track.state == "missing"


@pytest.mark.asyncio
async def test_local_sync_honors_since(db_session, fake_redis, local_sync_env, _make_local_external_library):
    """A sync with ``since`` only picks up files newer than the cutoff."""
    old = _write_file(local_sync_env / "old.mp3")
    _write_file(local_sync_env / "new.mp3")

    old_mtime = datetime.now(timezone.utc) - timedelta(hours=1)
    os.utime(old, (old_mtime.timestamp(), old_mtime.timestamp()))

    external_library = await _make_local_external_library(local_sync_env)
    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        since=since,
        redis=fake_redis,
    )

    assert run.items_seen == 1
    ext_track = (
        await db_session.execute(
            select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
        )
    ).scalar_one()
    assert ext_track.provider_key == "new.mp3"


@pytest.mark.asyncio
async def test_local_sync_honors_scope(db_session, fake_redis, local_sync_env, _make_local_external_library):
    """A sync with ``scope`` limits the missing pass to that sub-tree."""
    _write_file(local_sync_env / "a" / "keep.mp3")
    _write_file(local_sync_env / "b" / "gone.mp3")

    external_library = await _make_local_external_library(local_sync_env)
    await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    (local_sync_env / "b" / "gone.mp3").unlink()

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        scope="b",
        redis=fake_redis,
    )

    # Only the scoped file should be reported missing; track a stays active.
    ext_tracks = (
        (
            await db_session.execute(
                select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
            )
        )
        .scalars()
        .all()
    )
    assert run.tracks_missing == 1
    assert len(ext_tracks) == 2
    states = {t.provider_key: t.state for t in ext_tracks}
    assert states["b/gone.mp3"] == "missing"
    assert states["a/keep.mp3"] == "active"


@pytest.mark.asyncio
async def test_local_sync_scope_escapes_like_metacharacters(
    db_session, fake_redis, local_sync_env, _make_local_external_library
):
    """A scope containing LIKE metacharacters only matches its exact directory tree."""
    _write_file(local_sync_env / "a_b" / "track1.mp3")
    _write_file(local_sync_env / "axb" / "track2.mp3")

    external_library = await _make_local_external_library(local_sync_env)
    await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    (local_sync_env / "a_b" / "track1.mp3").unlink()

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        scope="a_b",
        redis=fake_redis,
    )

    ext_tracks = (
        (
            await db_session.execute(
                select(ExternalTrack).where(ExternalTrack.external_library_id == str(external_library.id))
            )
        )
        .scalars()
        .all()
    )
    states = {t.provider_key: t.state for t in ext_tracks}
    assert run.tracks_missing == 1
    assert states["a_b/track1.mp3"] == "missing"
    assert states["axb/track2.mp3"] == "active"


@pytest.mark.asyncio
async def test_local_sync_stream_resolves_path_stream(
    db_session, fake_redis, local_sync_env, _make_local_external_library
):
    """resolve_external_stream returns a local path stream for a synced external track."""
    _write_file(local_sync_env / "streamed.mp3")

    external_library = await _make_local_external_library(local_sync_env)
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

    from songhive.services.streaming import resolve_external_stream

    stream = await resolve_external_stream(db_session, ext_track.track_id, range_header="bytes=0-3")
    assert stream is not None
    assert stream.kind == "path"
    assert stream.supports_range is True
    assert stream.path == local_sync_env / "streamed.mp3"


@pytest.mark.asyncio
async def test_local_sync_sha256_collision_shadows(
    db_session, fake_redis, local_sync_env, _make_local_external_library
):
    """A local file matching an existing StoredFile is shadowed instead of creating a Track."""
    content = b"same local audio"
    sha256 = hashlib.sha256(content).hexdigest()

    _write_file(local_sync_env / "shadowed.mp3", content)

    stored_file = StoredFile(
        storage_path="local/shadowed.mp3",
        storage_backend="local",
        content_type="audio/mpeg",
        size=len(content),
        sha256=sha256,
        owner_id=None,
        visibility="private",
    )
    db_session.add(stored_file)
    await db_session.flush()

    external_library = await _make_local_external_library(local_sync_env)
    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

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

    track_count = await db_session.scalar(select(func.count()).select_from(Track))
    assert track_count == 0
    library_track_count = await db_session.scalar(select(func.count()).select_from(LibraryTrack))
    assert library_track_count == 0


def _musicbrainz_config(enabled: bool) -> SonghiveConfig:
    """Return a test config with MusicBrainz enabled/disabled."""
    return SonghiveConfig(musicbrainz={"enabled": enabled})


@pytest.mark.asyncio
async def test_local_sync_enqueues_musicbrainz_enrichment(
    db_session,
    fake_redis,
    local_sync_env,
    _make_local_external_library,
    monkeypatch,
):
    """A successful sync enqueues MusicBrainz enrichment for new external tracks."""
    _write_file(local_sync_env / "track1.mp3")
    _write_file(local_sync_env / "track2.flac")

    monkeypatch.setattr(
        "songhive.external.sync.load_config",
        lambda *_: _musicbrainz_config(enabled=True),
    )
    enrich_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.musicbrainz.enrich_track", enrich_mock)

    external_library = await _make_local_external_library(local_sync_env)
    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.tracks_created == 2
    assert enrich_mock.delay.call_count == 2

    tracks = (await db_session.execute(select(Track).where(Track.source == "external"))).scalars().all()
    track_ids = {str(track.id) for track in tracks}
    called_ids = {call.args[0] for call in enrich_mock.delay.call_args_list}
    assert called_ids == track_ids


@pytest.mark.asyncio
async def test_local_sync_skips_musicbrainz_enrichment_when_disabled(
    db_session,
    fake_redis,
    local_sync_env,
    _make_local_external_library,
    monkeypatch,
):
    """MusicBrainz enrichment is not enqueued when MusicBrainz is disabled."""
    _write_file(local_sync_env / "track1.mp3")

    monkeypatch.setattr(
        "songhive.external.sync.load_config",
        lambda *_: _musicbrainz_config(enabled=False),
    )
    enrich_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.musicbrainz.enrich_track", enrich_mock)

    external_library = await _make_local_external_library(local_sync_env)
    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.tracks_created == 1
    enrich_mock.delay.assert_not_called()


@pytest.mark.asyncio
async def test_local_sync_does_not_re_enrich_tracks(
    db_session,
    fake_redis,
    local_sync_env,
    _make_local_external_library,
    monkeypatch,
):
    """Tracks that have already been MusicBrainz-enriched are not re-enqueued."""
    _write_file(local_sync_env / "track1.mp3")

    monkeypatch.setattr(
        "songhive.external.sync.load_config",
        lambda *_: _musicbrainz_config(enabled=True),
    )
    enrich_mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.musicbrainz.enrich_track", enrich_mock)

    external_library = await _make_local_external_library(local_sync_env)
    await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    track = (await db_session.execute(select(Track).where(Track.source == "external"))).scalars().one()
    track.musicbrainz_enriched_at = datetime.now(timezone.utc)
    await db_session.flush()

    enrich_mock.delay.reset_mock()

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.tracks_updated == 0
    enrich_mock.delay.assert_not_called()
