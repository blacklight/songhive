"""
Tests for bulk download archives: API routes, item resolution, ZIP
materialization, the build task, and the periodic cleanup task.
"""

import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.download import DownloadArchive
from songhive.models.notification import Notification
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.podcast import Podcast, PodcastEpisode
from songhive.models.remote_object import RemoteObject
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.services import downloads as downloads_service
from songhive.services.storage import StorageService
from songhive.storage import get_storage
from songhive.tasks.downloads import (
    _acquire_slot,
    _cleanup_archives,
    _release_slot,
)


@pytest.fixture
def storage_service(config):
    return StorageService(get_storage(config.storage), config.storage)


@pytest.fixture
def initialized_db(engine):
    """Bind the global session factory so worker-style ``get_session()`` works.

    Materialization helpers open their own sessions (mirroring the Celery
    worker, where the archive row's session is long gone), so tests must
    commit fixture rows for those sessions to see them.
    """
    from songhive.models.base import init_db

    init_db(engine=engine, force=True)
    return engine


@pytest.fixture
async def dl_artist(db_session):
    artist = Artist(name="Archive Artist")
    db_session.add(artist)
    await db_session.flush()
    return artist


async def _make_track(
    session,
    artist,
    storage_service,
    owner,
    title="Track",
    data=b"track-bytes",
    visibility=Visibility.PUBLIC.value,
    artist_name=None,
):
    stored = await storage_service.store_file(
        session,
        io.BytesIO(data),
        "audio/mpeg",
        owner_id=owner.id,
        visibility=visibility,
        original_filename=f"{title}.mp3",
    )
    track = Track(
        title=title,
        artist_id=artist.id,
        audio_file_id=stored.id,
        owner_id=str(owner.id),
        visibility=visibility,
        duration=1.0,
    )
    session.add(track)
    await session.flush()
    return track, data


@pytest.fixture
async def dl_track(db_session, dl_artist, storage_service, regular_user):
    return await _make_track(
        db_session, dl_artist, storage_service, regular_user, title="First Song", data=b"first-song"
    )


@pytest.fixture
async def private_track(db_session, dl_artist, storage_service, other_user):
    track, _ = await _make_track(
        db_session,
        dl_artist,
        storage_service,
        other_user,
        title="Hidden Song",
        data=b"hidden",
        visibility=Visibility.PRIVATE.value,
    )
    return track


@pytest.fixture
async def dl_playlist(db_session, regular_user):
    playlist = Playlist(name="Mixtape", owner_id=regular_user.id, visibility=Visibility.PUBLIC.value)
    db_session.add(playlist)
    await db_session.flush()
    return playlist


@pytest.fixture
async def dl_remote(db_session):
    row = RemoteObject(
        canonical_url="https://remote.example/objects/1",
        domain="remote.example",
        object_type="Audio",
        actor_url="https://remote.example/users/dj",
        visibility="public",
        name="Remote Tune",
        audio_url="https://remote.example/media/tune.mp3",
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def dl_episode(db_session):
    podcast = Podcast(feed_url="https://pods.example/feed.xml", title="The Pod")
    db_session.add(podcast)
    await db_session.flush()
    episode = PodcastEpisode(
        podcast_id=podcast.id,
        guid="ep-1",
        title="Episode One",
        audio_url="https://pods.example/ep1.mp3",
        audio_type="audio/mpeg",
    )
    db_session.add(episode)
    await db_session.flush()
    return episode


def _zip_members(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_archive_with_track_ids(client, regular_user, dl_track, auth_headers):
    track, _ = dl_track
    response = client.post(
        "/api/v1/downloads/",
        headers=auth_headers(regular_user),
        json={"track_ids": [track.id]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["item_count"] == 1
    assert body["items"][0]["title"] == "First Song"
    assert body["items"][0]["artist"] == "Archive Artist"


def test_create_archive_enqueues_build(client, regular_user, dl_track, auth_headers, _no_real_celery_broker):
    track, _ = dl_track
    response = client.post(
        "/api/v1/downloads/",
        headers=auth_headers(regular_user),
        json={"track_ids": [track.id]},
    )
    assert response.status_code == 201
    assert _no_real_celery_broker.called
    task_name = _no_real_celery_broker.call_args.args[0]
    assert task_name == "songhive.tasks.downloads.build_download_archive"


def test_create_archive_no_source(client, regular_user, auth_headers):
    response = client.post("/api/v1/downloads/", headers=auth_headers(regular_user), json={})
    assert response.status_code == 400


def test_create_archive_unauthenticated(client):
    response = client.post("/api/v1/downloads/", json={"track_ids": ["x"]})
    assert response.status_code == 401


def test_create_archive_all_inaccessible(client, regular_user, private_track, auth_headers):
    response = client.post(
        "/api/v1/downloads/",
        headers=auth_headers(regular_user),
        json={"track_ids": [private_track.id]},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_archive_max_active_per_user(client, regular_user, dl_track, db_session, auth_headers, config):
    track, _ = dl_track
    for _ in range(config.downloads.max_active_per_user):
        db_session.add(
            DownloadArchive(
                user_id=regular_user.id,
                status="pending",
                label="queued",
                items=[{"kind": "track", "ref": track.id, "title": "t", "artist": ""}],
            )
        )
    await db_session.flush()

    response = client.post(
        "/api/v1/downloads/",
        headers=auth_headers(regular_user),
        json={"track_ids": [track.id]},
    )
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_list_archives_scoped_to_user(client, regular_user, other_user, db_session, auth_headers):
    db_session.add(DownloadArchive(user_id=regular_user.id, status="pending", label="mine", items=[]))
    db_session.add(DownloadArchive(user_id=other_user.id, status="pending", label="theirs", items=[]))
    await db_session.flush()

    response = client.get("/api/v1/downloads/", headers=auth_headers(regular_user))
    assert response.status_code == 200
    labels = [row["label"] for row in response.json()]
    assert labels == ["mine"]


@pytest.mark.asyncio
async def test_get_archive_other_user_not_found(client, regular_user, other_user, db_session, auth_headers):
    archive = DownloadArchive(user_id=other_user.id, status="pending", label="theirs", items=[])
    db_session.add(archive)
    await db_session.flush()

    response = client.get(f"/api/v1/downloads/{archive.id}", headers=auth_headers(regular_user))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_download_archive_file(client, regular_user, db_session, storage_service, auth_headers):
    stored = await storage_service.store_file(
        db_session,
        io.BytesIO(b"zip-bytes"),
        "application/zip",
        owner_id=regular_user.id,
        original_filename="mix.zip",
    )
    archive = DownloadArchive(
        user_id=regular_user.id,
        status="ready",
        label="mix",
        items=[],
        archive_file_id=stored.id,
    )
    db_session.add(archive)
    await db_session.flush()

    response = client.get(f"/api/v1/downloads/{archive.id}/file", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.content == b"zip-bytes"

    pending = DownloadArchive(user_id=regular_user.id, status="pending", label="wip", items=[])
    db_session.add(pending)
    await db_session.flush()
    response = client.get(f"/api/v1/downloads/{pending.id}/file", headers=auth_headers(regular_user))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_archive_removes_file(client, regular_user, db_session, storage_service, auth_headers, config):
    stored = await storage_service.store_file(
        db_session,
        io.BytesIO(b"zip-bytes"),
        "application/zip",
        owner_id=regular_user.id,
    )
    archive = DownloadArchive(
        user_id=regular_user.id,
        status="ready",
        label="mix",
        items=[],
        archive_file_id=stored.id,
    )
    db_session.add(archive)
    await db_session.flush()
    storage_path = stored.storage_path

    response = client.delete(f"/api/v1/downloads/{archive.id}", headers=auth_headers(regular_user))
    assert response.status_code == 204
    # The overridden get_db yields the shared test session without committing;
    # flush so the pending deletes are visible.
    await db_session.flush()
    assert await db_session.get(DownloadArchive, archive.id) is None
    assert await db_session.get(StoredFile, stored.id) is None
    assert await storage_service.backend.retrieve(storage_path) is None


@pytest.mark.asyncio
async def test_clear_completed(client, regular_user, db_session, auth_headers):
    for status in ("ready", "failed", "pending"):
        db_session.add(DownloadArchive(user_id=regular_user.id, status=status, label=status, items=[]))
    await db_session.flush()

    response = client.post("/api/v1/downloads/clear", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json() == {"cleared": 2}

    await db_session.flush()
    remaining = (await db_session.execute(select(DownloadArchive))).scalars().all()
    assert [row.status for row in remaining] == ["pending"]


# ---------------------------------------------------------------------------
# Item resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_explicit_tracks_skips_inaccessible(db_session, regular_user, dl_track, private_track, config):
    track, _ = dl_track
    items, skipped = await downloads_service.resolve_archive_items(
        db_session,
        regular_user,
        config,
        track_ids=[track.id, private_track.id, "missing-id"],
    )
    assert skipped == 2
    assert [item.ref for item in items] == [track.id]
    assert items[0].kind == "track"
    assert items[0].artist == "Archive Artist"


@pytest.mark.asyncio
async def test_resolve_remote_and_episode_items(db_session, regular_user, dl_remote, dl_episode, config):
    items, skipped = await downloads_service.resolve_archive_items(
        db_session,
        regular_user,
        config,
        remote_object_ids=[dl_remote.id],
        episode_ids=[dl_episode.id],
    )
    assert skipped == 0
    assert [item.kind for item in items] == ["remote", "episode"]
    assert items[0].title == "Remote Tune"
    assert items[1].artist == "The Pod"


@pytest.mark.asyncio
async def test_resolve_playlist_items(db_session, regular_user, dl_playlist, dl_track, dl_episode, config):
    track, _ = dl_track
    db_session.add(PlaylistTrack(playlist_id=dl_playlist.id, track_id=track.id, position=0))
    db_session.add(PlaylistTrack(playlist_id=dl_playlist.id, podcast_episode_id=dl_episode.id, position=1))
    await db_session.flush()

    items, _ = await downloads_service.resolve_archive_items(
        db_session, regular_user, config, playlist_id=dl_playlist.id
    )
    assert [(item.kind, item.ref) for item in items] == [("track", track.id), ("episode", dl_episode.id)]


@pytest.mark.asyncio
async def test_resolve_album_items(db_session, regular_user, dl_artist, dl_track, storage_service, config):
    from songhive.models.album import Album

    album = Album(title="The Album", artist_id=dl_artist.id, owner_id=regular_user.id, visibility="public")
    db_session.add(album)
    await db_session.flush()
    track, _ = dl_track
    track.album_id = album.id
    await db_session.flush()

    items, _ = await downloads_service.resolve_archive_items(db_session, regular_user, config, album_id=album.id)
    assert [item.ref for item in items] == [track.id]


@pytest.mark.asyncio
async def test_resolve_missing_container(db_session, regular_user, config):
    with pytest.raises(downloads_service.ArchiveRequestError) as exc:
        await downloads_service.resolve_archive_items(db_session, regular_user, config, album_id="does-not-exist")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_resolve_private_container_denied(db_session, regular_user, other_user, config):
    playlist = Playlist(name="Secret", owner_id=other_user.id, visibility=Visibility.PRIVATE.value)
    db_session.add(playlist)
    await db_session.flush()

    with pytest.raises(downloads_service.ArchiveRequestError) as exc:
        await downloads_service.resolve_archive_items(db_session, regular_user, config, playlist_id=playlist.id)
    assert exc.value.status_code == 403


def test_sanitize_member_name():
    assert downloads_service.sanitize_member_name('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert downloads_service.sanitize_member_name("  ..  ") == "item"
    assert downloads_service.sanitize_member_name("") == "item"
    assert len(downloads_service.sanitize_member_name("x" * 300)) <= 120


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_local_track(
    db_session, regular_user, dl_track, storage_service, config, tmp_path, initialized_db
):
    track, data = dl_track
    await db_session.commit()
    items = [{"kind": "track", "ref": track.id, "title": track.title, "artist": "Archive Artist"}]
    zip_path, item_errors = await downloads_service.materialize_archive(storage_service, config, items, tmp_path)
    assert item_errors == []
    members = _zip_members(zip_path)
    assert list(members) == ["01 - Archive Artist - First Song.mp3"]
    assert members["01 - Archive Artist - First Song.mp3"] == data


@pytest.mark.asyncio
async def test_materialize_missing_item_recorded(
    db_session, regular_user, dl_track, storage_service, config, tmp_path, initialized_db
):
    track, _ = dl_track
    await db_session.commit()
    items = [
        {"kind": "track", "ref": "gone", "title": "Lost", "artist": ""},
        {"kind": "track", "ref": track.id, "title": track.title, "artist": ""},
    ]
    zip_path, item_errors = await downloads_service.materialize_archive(storage_service, config, items, tmp_path)
    assert len(item_errors) == 1
    assert item_errors[0]["ref"] == "gone"
    assert list(_zip_members(zip_path)) == ["02 - First Song.mp3"]


@pytest.mark.asyncio
async def test_materialize_all_failed_raises(storage_service, config, tmp_path, initialized_db):
    items = [{"kind": "track", "ref": "gone", "title": "Lost", "artist": ""}]
    with pytest.raises(downloads_service.ArchiveRequestError):
        await downloads_service.materialize_archive(storage_service, config, items, tmp_path)


@pytest.mark.asyncio
async def test_materialize_remote_retries_then_succeeds(
    db_session, regular_user, dl_remote, storage_service, config, tmp_path, initialized_db
):
    await db_session.commit()
    config.downloads.fetch_backoff_seconds = 0.001
    attempts = []

    def fake_download(url, dest_path, **kwargs):
        attempts.append(url)
        if len(attempts) < 3:
            from songhive.federation.fetch import FetchError

            raise FetchError("boom", status_code=502)
        dest_path.write_bytes(b"remote-audio")
        from songhive.federation.fetch import DownloadResult

        return DownloadResult(url=url, status_code=200, content_type="audio/mpeg", size=12)

    items = [
        {
            "kind": "remote",
            "ref": dl_remote.id,
            "title": dl_remote.name,
            "artist": "",
        }
    ]
    with patch.object(downloads_service, "guarded_download", side_effect=fake_download):
        zip_path, item_errors = await downloads_service.materialize_archive(storage_service, config, items, tmp_path)
    assert attempts == ["https://remote.example/media/tune.mp3"] * 3
    assert item_errors == []
    assert list(_zip_members(zip_path)) == ["01 - Remote Tune.mp3"]


@pytest.mark.asyncio
async def test_materialize_episode(
    db_session, regular_user, dl_episode, storage_service, config, tmp_path, initialized_db
):
    await db_session.commit()

    def fake_download(url, dest_path, **kwargs):
        dest_path.write_bytes(b"episode-audio")
        from songhive.federation.fetch import DownloadResult

        return DownloadResult(url=url, status_code=200, content_type="audio/mpeg", size=13)

    items = [
        {
            "kind": "episode",
            "ref": dl_episode.id,
            "title": dl_episode.title,
            "artist": "The Pod",
        }
    ]
    with patch.object(downloads_service, "guarded_download", side_effect=fake_download):
        zip_path, item_errors = await downloads_service.materialize_archive(storage_service, config, items, tmp_path)
    assert item_errors == []
    assert list(_zip_members(zip_path)) == ["01 - The Pod - Episode One.mp3"]


@pytest.mark.asyncio
async def test_materialize_external_track(
    db_session, regular_user, dl_artist, storage_service, config, tmp_path, initialized_db
):
    """An externally mounted track resolves through its adapter."""
    from songhive.models.external_library import ExternalLibrary
    from songhive.models.external_track import ExternalTrack
    from songhive.models.library import Library

    data = b"external-bytes"
    library = Library(name="Ext Lib", owner_id=regular_user.id)
    db_session.add(library)
    await db_session.flush()
    ext_lib = ExternalLibrary(
        name="Ext",
        provider_type="fake",
        library_id=library.id,
        created_by_id=regular_user.id,
        config={"items": {"song.mp3": {"data": list(data), "mimetype": "audio/mpeg"}}},
        capabilities={"read_bytes": True, "download": True},
    )
    db_session.add(ext_lib)
    await db_session.flush()
    ext_track = ExternalTrack(
        track_id=None,
        external_library_id=ext_lib.id,
        provider_key="song.mp3",
        provider_size=len(data),
        provider_mime_type="audio/mpeg",
        state="active",
    )
    db_session.add(ext_track)
    await db_session.flush()
    track = Track(
        title="External Song",
        artist_id=dl_artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        duration=1.0,
        audio_mime_type="audio/mpeg",
    )
    db_session.add(track)
    await db_session.flush()
    ext_track.track_id = track.id
    await db_session.commit()

    items = [{"kind": "track", "ref": track.id, "title": track.title, "artist": "Archive Artist"}]
    zip_path, item_errors = await downloads_service.materialize_archive(storage_service, config, items, tmp_path)
    assert item_errors == []
    members = _zip_members(zip_path)
    assert list(members) == ["01 - Archive Artist - External Song.mp3"]
    assert members["01 - Archive Artist - External Song.mp3"] == data


# ---------------------------------------------------------------------------
# Build task + cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_archive_end_to_end(db_session, regular_user, dl_track, storage_service, config, initialized_db):
    """The async build body stores the ZIP, flips the row, and notifies."""
    from songhive.tasks.downloads import _build_archive

    track, data = dl_track
    archive = DownloadArchive(
        user_id=regular_user.id,
        status="pending",
        label="My Mix",
        items=[{"kind": "track", "ref": track.id, "title": track.title, "artist": "Archive Artist"}],
    )
    db_session.add(archive)
    await db_session.commit()

    await _build_archive(archive.id, config, storage_service)

    await db_session.refresh(archive)
    assert archive.status == "ready"
    assert archive.archive_file_id is not None
    stored = await db_session.get(StoredFile, archive.archive_file_id)
    assert stored is not None
    path = await storage_service.backend.retrieve(stored.storage_path)
    assert _zip_members(path)["01 - Archive Artist - First Song.mp3"] == data

    notification = (
        await db_session.execute(
            select(Notification).where(Notification.user_id == regular_user.id, Notification.type == "download")
        )
    ).scalar_one()
    assert notification.payload["archive_id"] == archive.id


@pytest.mark.asyncio
async def test_cleanup_archives(db_session, regular_user, storage_service, config):
    now = datetime.now(timezone.utc)
    stale = DownloadArchive(
        user_id=regular_user.id,
        status="processing",
        label="stale",
        items=[],
    )
    expired_ready = DownloadArchive(
        user_id=regular_user.id,
        status="ready",
        label="old",
        items=[],
        completed_at=now - timedelta(hours=config.downloads.retention_hours + 1),
    )
    fresh = DownloadArchive(
        user_id=regular_user.id,
        status="ready",
        label="new",
        items=[],
        completed_at=now,
    )
    pending = DownloadArchive(user_id=regular_user.id, status="pending", label="wip", items=[])
    db_session.add_all([stale, expired_ready, fresh, pending])
    await db_session.flush()

    stale.updated_at = now - timedelta(hours=config.downloads.stale_run_hours + 1)
    await db_session.flush()

    removed = await _cleanup_archives(db_session, storage_service, config)
    assert removed == 1

    await db_session.refresh(stale)
    assert stale.status == "failed"
    assert await db_session.get(DownloadArchive, expired_ready.id) is None
    assert await db_session.get(DownloadArchive, fresh.id) is not None
    assert await db_session.get(DownloadArchive, pending.id) is not None


@pytest.mark.asyncio
async def test_cleanup_removes_backing_file(db_session, regular_user, storage_service, config):
    now = datetime.now(timezone.utc)
    stored = await storage_service.store_file(
        db_session, io.BytesIO(b"zip"), "application/zip", owner_id=regular_user.id
    )
    archive = DownloadArchive(
        user_id=regular_user.id,
        status="ready",
        label="old",
        items=[],
        archive_file_id=stored.id,
        completed_at=now - timedelta(hours=config.downloads.retention_hours + 1),
    )
    db_session.add(archive)
    await db_session.flush()
    storage_path = stored.storage_path

    removed = await _cleanup_archives(db_session, storage_service, config)
    assert removed == 1
    await db_session.flush()
    assert await db_session.get(StoredFile, stored.id) is None
    assert await storage_service.backend.retrieve(storage_path) is None


def test_archive_slot_semaphore(config, monkeypatch):
    """The Redis semaphore caps concurrent builds and releases slots."""
    import fakeredis

    class _EmulatedRedis:
        """fakeredis lacks Lua eval; emulate the acquire script in Python."""

        def __init__(self):
            self._redis = fakeredis.FakeRedis()

        def eval(self, script, numkeys, key, *args):
            stale_before, limit, score, member, ttl = args
            self._redis.zremrangebyscore(key, 0, float(stale_before))
            if self._redis.zcard(key) >= int(limit):
                return 0
            self._redis.zadd(key, {member: float(score)})
            self._redis.expire(key, int(ttl))
            return 1

        def zrem(self, *args):
            return self._redis.zrem(*args)

    shared = _EmulatedRedis()
    monkeypatch.setattr("songhive.services.redis.get_sync_redis_client", lambda config=None: shared)

    config.downloads.max_concurrent_archives = 2
    first = _acquire_slot("a1", config)
    second = _acquire_slot("a2", config)
    assert first is not None and second is not None
    assert _acquire_slot("a3", config) is None

    _release_slot(first, "a1")
    third = _acquire_slot("a3", config)
    assert third is not None
    _release_slot(second, "a2")
    _release_slot(third, "a3")
