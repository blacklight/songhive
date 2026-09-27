"""Tests for lazy provider-contents refresh (``external.lazy`` + refresh task).

Uses a fake entity adapter advertising ``lazy_contents`` + ``immutable_tracks``
(the TIDAL capability shape) with an in-config ``iter_contents`` source, so no
HTTP/SDK is needed. The Celery task's inner coroutine is invoked directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Optional
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from songhive.external.base import ExternalLibraryAdapter
from songhive.external.lazy import ensure_contents, wait_for_contents
from songhive.external.registry import register_external_adapter
from songhive.external.sync import sync_external_library
from songhive.external.types import (
    ContentsNotModified,
    ExternalContentEntry,
    ExternalContents,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalPlaylistMetadata,
    ExternalTrackMetadata,
)
from songhive.models.base import init_db
from songhive.models.external_item import ExternalItem
from songhive.models.external_library import ExternalLibrary
from songhive.models.library import Library
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.provider_catalog import ProviderCatalogEntry
from songhive.models.user import User
from songhive.services import secrets


class FakeContentsAdapter(ExternalLibraryAdapter):
    """Entity adapter with lazy contents served from ``config["contents"]``."""

    provider_type = "fake-contents"
    user_configurable = True

    # Test instrumentation: number of provider calls per adapter instance.
    iter_contents_calls: int = 0

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        self._capabilities = ExternalLibraryCapabilities(
            list_items=True,
            stream_url=True,
            detect_changes=True,
            validate_config=True,
            list_albums=True,
            list_artists=True,
            list_playlists=True,
            limits={
                "checksum_algorithm": None,
                "entity_import": True,
                "immutable_tracks": True,
                "lazy_contents": ["playlist", "album"],
            },
        )
        return self._capabilities

    async def iter_items(self, config: dict, since=None, scope=None) -> AsyncIterator[ExternalItemRef]:
        for entry in config.get("entities", {}).get("tracks", []):
            yield ExternalItemRef(
                provider_key=entry["provider_key"],
                display_path=entry["provider_key"],
                metadata=ExternalTrackMetadata(**entry["metadata"]),
            )

    async def iter_albums(self, config: dict, since=None, scope=None):
        from songhive.external.types import ExternalAlbumMetadata

        for entry in config.get("entities", {}).get("albums", []):
            yield ExternalAlbumMetadata(**entry)
        if False:
            yield  # pragma: no cover

    async def iter_artists(self, config: dict, since=None, scope=None):
        from songhive.external.types import ExternalArtistMetadata

        for entry in config.get("entities", {}).get("artists", []):
            yield ExternalArtistMetadata(**entry)
        if False:
            yield  # pragma: no cover

    async def iter_playlists(self, config: dict, since=None, scope=None) -> AsyncIterator[ExternalPlaylistMetadata]:
        for entry in config.get("entities", {}).get("playlists", []):
            entry = dict(entry)
            entry.pop("entries", None)  # lazy listings never carry entries
            yield ExternalPlaylistMetadata(**entry)

    async def iter_contents(
        self,
        config: dict,
        kind: str,
        provider_key: str,
        *,
        etag: Optional[str] = None,
    ) -> ExternalContents:
        FakeContentsAdapter.iter_contents_calls += 1
        current_etag = config.get("etags", {}).get(provider_key)
        if etag is not None and etag == current_etag:
            raise ContentsNotModified()
        if config.get("raise_contents_error"):
            raise RuntimeError("provider exploded")
        entries = []
        for position, entry in enumerate(config.get("contents", {}).get(kind, {}).get(provider_key, [])):
            entries.append(
                ExternalContentEntry(
                    position=position,
                    provider_key=entry["provider_key"],
                    metadata=ExternalTrackMetadata(**entry["metadata"]),
                )
            )
        return ExternalContents(entries=tuple(entries), etag=current_etag)


@pytest.fixture(autouse=True)
def _register_fake_contents_adapter():
    register_external_adapter("fake-contents", FakeContentsAdapter)
    FakeContentsAdapter.iter_contents_calls = 0
    yield
    from songhive.external.registry import unregister_external_adapter

    unregister_external_adapter("fake-contents")


@pytest.fixture
def make_contents_library(db_session):
    async def _inner(config: dict, username: str = "lazy-owner"):
        owner = User(
            username=username,
            email=f"{username}@example.com",
            password_hash="x" * 60,
            role="user",
            is_active=True,
        )
        db_session.add(owner)
        await db_session.flush()
        library = Library(name="Lazy Library", owner_id=str(owner.id), visibility="private")
        db_session.add(library)
        await db_session.flush()
        external_library = ExternalLibrary(
            library_id=str(library.id),
            provider_type="fake-contents",
            config=secrets.encrypt_json(config),
            enabled=True,
            created_by_id=str(owner.id),
        )
        db_session.add(external_library)
        await db_session.flush()
        return external_library, library, owner

    return _inner


def _track(provider_key: str, **metadata_overrides) -> dict:
    metadata = {
        "title": f"Title {provider_key}",
        "artist": "Artist A",
        "album": "Album A",
        "album_artist": "Artist A",
        "duration": 180.0,
        "raw_metadata": {"id": provider_key, "title": f"Title {provider_key}"},
    }
    metadata.update(metadata_overrides)
    return {"provider_key": provider_key, "metadata": metadata}


def _playlist(provider_key: str, title: str = "Lazy Mix") -> dict:
    return {"provider_key": provider_key, "title": title}


async def _external_item(db_session, external_library, kind, provider_key) -> ExternalItem:
    result = await db_session.execute(
        select(ExternalItem).where(
            ExternalItem.external_library_id == str(external_library.id),
            ExternalItem.kind == kind,
            ExternalItem.provider_key == provider_key,
        )
    )
    return result.scalar_one()


def _spy_delay(monkeypatch) -> MagicMock:
    """Replace the refresh task's ``.delay`` with a recording mock."""
    from songhive.tasks import external_libraries as ext_tasks

    mock = MagicMock(name="refresh_external_contents_task.delay")
    monkeypatch.setattr(ext_tasks.refresh_external_contents_task, "delay", mock)
    return mock


# ---------------------------------------------------------------------------
# ensure_contents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_never_fetched_enqueues_once(db_session, fake_redis, make_contents_library, monkeypatch):
    """First sighting enqueues a refresh; concurrent requests don't duplicate."""
    delay = _spy_delay(monkeypatch)
    external_library, _, _ = await make_contents_library({"entities": {"playlists": [_playlist("pl-1")]}})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()

    status = await ensure_contents(db_session, "playlist", str(playlist.id), redis=fake_redis)
    assert status.state == "never_fetched"
    assert status.provider_type == "fake-contents"
    assert delay.call_count == 1
    args = delay.call_args.args
    assert args[0] == str(external_library.id)
    assert args[1] == "playlist"
    assert args[2] == "pl-1"

    # Second call while the lock is held → no second enqueue.
    status = await ensure_contents(db_session, "playlist", str(playlist.id), redis=fake_redis)
    assert status.state == "never_fetched"
    assert delay.call_count == 1


@pytest.mark.asyncio
async def test_fresh_within_ttl_does_not_enqueue(db_session, fake_redis, make_contents_library, monkeypatch):
    delay = _spy_delay(monkeypatch)
    external_library, _, _ = await make_contents_library({"entities": {"playlists": [_playlist("pl-1")]}})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()
    item = await _external_item(db_session, external_library, "playlist", "pl-1")
    item.contents_fetched_at = datetime.now(timezone.utc)
    await db_session.flush()

    status = await ensure_contents(db_session, "playlist", str(playlist.id), redis=fake_redis)
    assert status.state == "fresh"
    assert status.ttl_seconds and status.ttl_seconds > 0
    assert delay.call_count == 0


@pytest.mark.asyncio
async def test_expired_ttl_enqueues_refresh(db_session, fake_redis, make_contents_library, monkeypatch):
    delay = _spy_delay(monkeypatch)
    # Per-library TTL of 60 s (above the 5 min minimum? no — clamped to 300).
    external_library, _, _ = await make_contents_library(
        {"entities": {"playlists": [_playlist("pl-1")]}, "playlist_ttl_seconds": 600}
    )
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()
    item = await _external_item(db_session, external_library, "playlist", "pl-1")
    item.contents_fetched_at = datetime.now(timezone.utc) - timedelta(seconds=3600)
    await db_session.flush()

    status = await ensure_contents(db_session, "playlist", str(playlist.id), redis=fake_redis)
    assert status.state == "refreshing"
    assert delay.call_count == 1


@pytest.mark.asyncio
async def test_stale_marker_enqueues_refresh(db_session, fake_redis, make_contents_library, monkeypatch):
    """lastUpdated advancing during sync marks contents stale → refresh."""
    delay = _spy_delay(monkeypatch)
    external_library, _, _ = await make_contents_library({"entities": {"playlists": [_playlist("pl-1")]}})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()
    item = await _external_item(db_session, external_library, "playlist", "pl-1")
    item.contents_fetched_at = datetime.now(timezone.utc)
    item.contents_error = "stale"
    await db_session.flush()

    status = await ensure_contents(db_session, "playlist", str(playlist.id), redis=fake_redis)
    assert status.state == "refreshing"
    assert delay.call_count == 1


@pytest.mark.asyncio
async def test_force_refresh_enqueues(db_session, fake_redis, make_contents_library, monkeypatch):
    delay = _spy_delay(monkeypatch)
    external_library, _, _ = await make_contents_library({"entities": {"playlists": [_playlist("pl-1")]}})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()
    item = await _external_item(db_session, external_library, "playlist", "pl-1")
    item.contents_fetched_at = datetime.now(timezone.utc)
    await db_session.flush()

    status = await ensure_contents(db_session, "playlist", str(playlist.id), force=True, redis=fake_redis)
    assert status.state == "refreshing"
    assert delay.call_count == 1
    assert delay.call_args.args[3] is True


@pytest.mark.asyncio
async def test_error_state_reported(db_session, fake_redis, make_contents_library, monkeypatch):
    delay = _spy_delay(monkeypatch)
    external_library, _, _ = await make_contents_library({"entities": {"playlists": [_playlist("pl-1")]}})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()
    item = await _external_item(db_session, external_library, "playlist", "pl-1")
    item.contents_fetched_at = datetime.now(timezone.utc)
    item.contents_error = "provider exploded"
    await db_session.flush()

    status = await ensure_contents(db_session, "playlist", str(playlist.id), redis=fake_redis)
    assert status.state == "error"
    assert status.error == "provider exploded"
    assert delay.call_count == 1  # a real error is retried by the next view


@pytest.mark.asyncio
async def test_non_provider_entity_returns_none(db_session, fake_redis):
    """A purely local playlist has no provider_sync state."""
    playlist = Playlist(name="Local", owner_id=None, visibility="public")
    db_session.add(playlist)
    await db_session.flush()
    assert (await ensure_contents(db_session, "playlist", str(playlist.id), redis=fake_redis)) is None


# ---------------------------------------------------------------------------
# wait_for_contents (Subsonic bounded wait)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_contents_returns_when_fetched(
    db_session, engine, fake_redis, fake_redis_server, make_contents_library, monkeypatch
):
    """The waiter returns as soon as the refresh task stamps contents_fetched_at."""
    external_library, _, _ = await make_contents_library({"entities": {"playlists": [_playlist("pl-1")]}})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    item = await _external_item(db_session, external_library, "playlist", "pl-1")

    monkeypatch.setattr("songhive.services.redis.get_redis_client", lambda config: fake_redis)

    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from songhive.external.lazy import contents_lock_key

    # Hold the dedupe lock, as a real enqueue would have left it.
    await fake_redis.set(contents_lock_key(str(external_library.id), "playlist", "pl-1"), "1", ex=60)

    async def _finish_refresh():
        await asyncio.sleep(0.4)
        # Write through a separate session like the Celery worker does.
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as worker_session:
            worker_item = await worker_session.get(ExternalItem, item.id)
            worker_item.contents_fetched_at = datetime.now(timezone.utc)
            await worker_session.commit()

    task = asyncio.create_task(_finish_refresh())
    await wait_for_contents(db_session, item, 5.0)
    await task
    assert item.contents_fetched_at is not None


@pytest.mark.asyncio
async def test_wait_for_contents_times_out(db_session, fake_redis, make_contents_library, monkeypatch):
    """A refresh that never lands is bounded by the timeout."""
    import time

    external_library, _, _ = await make_contents_library({"entities": {"playlists": [_playlist("pl-1")]}})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    item = await _external_item(db_session, external_library, "playlist", "pl-1")
    monkeypatch.setattr("songhive.services.redis.get_redis_client", lambda config: fake_redis)

    from songhive.external.lazy import contents_lock_key

    await fake_redis.set(contents_lock_key(str(external_library.id), "playlist", "pl-1"), "1", ex=60)

    start = time.monotonic()
    await wait_for_contents(db_session, item, 0.6)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0
    assert elapsed >= 0.5


# ---------------------------------------------------------------------------
# refresh_external_contents task
# ---------------------------------------------------------------------------


async def _run_refresh(db_session, engine, fake_redis, external_library, kind, provider_key):
    """Invoke the task coroutine against the test engine/redis."""
    from songhive.tasks import external_libraries as ext_tasks

    external_library_id = str(external_library.id)
    # Commit any open read transaction so the worker session can write; commit
    # (unlike rollback) keeps loaded attribute state usable afterwards.
    await db_session.commit()
    init_db(engine=engine, force=True)
    original_get_redis = ext_tasks.get_redis_client
    ext_tasks.get_redis_client = lambda config: fake_redis
    try:
        return await ext_tasks._refresh_external_contents(external_library_id, kind, provider_key)
    finally:
        ext_tasks.get_redis_client = original_get_redis


@pytest.mark.asyncio
async def test_refresh_materializes_referenced_tracks(
    db_session, engine, fake_redis, fake_redis_server, make_contents_library, monkeypatch
):
    """Refresh fills catalog, materializes referenced tracks, orders rows, and
    publishes the ``external_contents_refreshed`` envelope."""
    from songhive.ws import events as ws_events

    published: list[dict] = []
    monkeypatch.setattr(ws_events, "_publish_envelope", published.append)

    external_library, library, owner = await make_contents_library(
        {
            "entities": {"playlists": [_playlist("pl-1")]},
            "etags": {"pl-1": "etag-v1"},
            "contents": {
                "playlist": {
                    "pl-1": [_track("t-1"), _track("t-2"), _track("t-3")],
                }
            },
        }
    )
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()

    result = await _run_refresh(db_session, engine, fake_redis, external_library, "playlist", "pl-1")
    assert result["status"] == "ok"
    assert result["entries"] == 3
    assert FakeContentsAdapter.iter_contents_calls == 1  # single provider call

    # Playlist rows replaced in provider order.
    rows = (
        (
            await db_session.execute(
                select(PlaylistTrack)
                .where(PlaylistTrack.playlist_id == str(playlist.id))
                .order_by(PlaylistTrack.position)
            )
        )
        .scalars()
        .all()
    )
    assert [r.position for r in rows] == [0, 1, 2]

    # Materialized tracks are referenced members, not library saves.
    track_items = (await db_session.execute(select(ExternalItem).where(ExternalItem.kind == "track"))).scalars().all()
    assert len(track_items) == 3
    assert all(i.membership == "referenced" for i in track_items)
    assert all(i.track_id is not None for i in track_items)
    from songhive.models.library_track import LibraryTrack

    assert (await db_session.scalar(select(func.count()).select_from(LibraryTrack))) == 0

    # Track catalog rows are populated write-once, immutable (no expiry).
    catalog_tracks = (
        (await db_session.execute(select(ProviderCatalogEntry).where(ProviderCatalogEntry.kind == "track")))
        .scalars()
        .all()
    )
    assert {e.provider_key for e in catalog_tracks} == {"t-1", "t-2", "t-3"}
    assert all(e.expires_at is None for e in catalog_tracks)

    # The container's catalog row carries ordered refs + etag.
    container = (
        await db_session.execute(
            select(ProviderCatalogEntry).where(
                ProviderCatalogEntry.kind == "playlist",
                ProviderCatalogEntry.provider_key == "pl-1",
            )
        )
    ).scalar_one()
    assert [c["id"] for c in container.contents] == ["t-1", "t-2", "t-3"]
    assert container.contents_etag == "etag-v1"
    assert container.contents_fetched_at is not None

    # Per-library bookkeeping updated.
    item = await _external_item(db_session, external_library, "playlist", "pl-1")
    assert item.contents_fetched_at is not None
    assert item.contents_error is None

    # WS envelope published to the owner via the Redis pub/sub publisher.
    envelope = next(e for e in published if e["type"] == "external_contents_refreshed")
    assert envelope["kind"] == "user"
    assert envelope["user_id"] == str(owner.id)
    assert envelope["type"] == "external_contents_refreshed"
    assert envelope["data"]["kind"] == "playlist"
    assert envelope["data"]["entity_id"] == str(playlist.id)
    assert envelope["data"]["state"] == "fresh"


@pytest.mark.asyncio
async def test_refresh_replaces_playlist_rows_in_order(db_session, engine, fake_redis, make_contents_library):
    """A second refresh with reordered contents replaces rows in the new order."""
    config = {
        "entities": {"playlists": [_playlist("pl-1")]},
        "etags": {"pl-1": "etag-v1"},
        "contents": {"playlist": {"pl-1": [_track("t-1"), _track("t-2")]}},
    }
    external_library, _, _ = await make_contents_library(config)
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()

    await _run_refresh(db_session, engine, fake_redis, external_library, "playlist", "pl-1")

    rows = (
        (
            await db_session.execute(
                select(PlaylistTrack)
                .where(PlaylistTrack.playlist_id == str(playlist.id))
                .order_by(PlaylistTrack.position)
            )
        )
        .scalars()
        .all()
    )
    first_order = [r.track_id for r in rows]

    # Provider reorders the playlist and bumps its etag.
    config["etags"]["pl-1"] = "etag-v2"
    config["contents"]["playlist"]["pl-1"] = [_track("t-2"), _track("t-1")]
    external_library.config = secrets.encrypt_json(config)
    # Commit (not flush): an open write transaction on db_session holds the
    # SQLite write lock and would deadlock the worker session.
    await db_session.commit()

    await _run_refresh(db_session, engine, fake_redis, external_library, "playlist", "pl-1")
    await db_session.refresh(playlist)

    rows = (
        (
            await db_session.execute(
                select(PlaylistTrack)
                .where(PlaylistTrack.playlist_id == str(playlist.id))
                .order_by(PlaylistTrack.position)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert [r.position for r in rows] == [0, 1]
    assert [r.track_id for r in rows] == [first_order[1], first_order[0]]


@pytest.mark.asyncio
async def test_refresh_not_modified_bumps_timestamp(db_session, engine, fake_redis, make_contents_library):
    """An unchanged etag (304) refreshes the timestamp without rewriting rows."""
    config = {
        "entities": {"playlists": [_playlist("pl-1")]},
        "etags": {"pl-1": "etag-v1"},
        "contents": {"playlist": {"pl-1": [_track("t-1")]}},
    }
    external_library, _, _ = await make_contents_library(config)
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()

    await _run_refresh(db_session, engine, fake_redis, external_library, "playlist", "pl-1")
    item = await _external_item(db_session, external_library, "playlist", "pl-1")
    first_fetched = item.contents_fetched_at

    await db_session.refresh(playlist)
    # Etag is now stored — next refresh gets ContentsNotModified.
    result = await _run_refresh(db_session, engine, fake_redis, external_library, "playlist", "pl-1")
    assert result["status"] == "not_modified"
    await db_session.refresh(item)
    assert item.contents_fetched_at > first_fetched


@pytest.mark.asyncio
async def test_refresh_error_recorded_and_published(
    db_session, engine, fake_redis, fake_redis_server, make_contents_library, monkeypatch
):
    """Provider failures land on contents_error and publish an error event."""
    from songhive.ws import events as ws_events

    published: list[dict] = []
    monkeypatch.setattr(ws_events, "_publish_envelope", published.append)

    external_library, _, owner = await make_contents_library(
        {
            "entities": {"playlists": [_playlist("pl-1")]},
            "raise_contents_error": True,
        }
    )
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)

    result = await _run_refresh(db_session, engine, fake_redis, external_library, "playlist", "pl-1")
    assert result["status"] == "failed"

    item = await _external_item(db_session, external_library, "playlist", "pl-1")
    assert item.contents_error
    assert item.sync_error

    envelope = next(e for e in published if e["type"] == "external_contents_refreshed")
    assert envelope["data"]["state"] == "error"
    assert envelope["user_id"] == str(owner.id)


# ---------------------------------------------------------------------------
# API: provider_sync field + provider-sync endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_playlist_get_reports_never_fetched(
    client,
    db_session,
    fake_redis,
    fake_redis_server,
    make_contents_library,
    regular_user,
    auth_headers,
    monkeypatch,
):
    """GET /playlists/{id} returns never_fetched and enqueues exactly once."""
    from fakeredis.aioredis import FakeRedis

    delay = _spy_delay(monkeypatch)
    monkeypatch.setattr(
        "songhive.services.redis.get_redis_client",
        lambda config: FakeRedis(server=fake_redis_server, decode_responses=True),
    )
    external_library, library, owner = await make_contents_library(
        {"entities": {"playlists": [_playlist("pl-1")]}}, username="playlist-owner"
    )
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()

    response = client.get(f"/api/v1/playlists/{playlist.id}", headers=auth_headers(owner))
    assert response.status_code == 200
    provider_sync = response.json()["provider_sync"]
    assert provider_sync["provider_type"] == "fake-contents"
    assert provider_sync["state"] == "never_fetched"
    assert delay.call_count == 1

    # Second GET: lock still held → still one enqueue.
    response = client.get(f"/api/v1/playlists/{playlist.id}", headers=auth_headers(owner))
    assert response.status_code == 200
    assert delay.call_count == 1


@pytest.mark.asyncio
async def test_provider_sync_endpoint_authz(
    client,
    db_session,
    fake_redis,
    fake_redis_server,
    make_contents_library,
    regular_user,
    other_user,
    auth_headers,
    monkeypatch,
):
    """POST provider-sync requires owner/admin; non-owner gets 403."""
    from fakeredis.aioredis import FakeRedis

    _spy_delay(monkeypatch)
    monkeypatch.setattr(
        "songhive.services.redis.get_redis_client",
        lambda config: FakeRedis(server=fake_redis_server, decode_responses=True),
    )
    external_library, library, owner = await make_contents_library(
        {"entities": {"playlists": [_playlist("pl-1")]}}, username="pl-owner"
    )
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    playlist = (await db_session.execute(select(Playlist))).scalar_one()

    # Non-owner → 403.
    response = client.post(
        f"/api/v1/playlists/{playlist.id}/provider-sync",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403

    # Owner → 202 + status payload.
    response = client.post(
        f"/api/v1/playlists/{playlist.id}/provider-sync",
        headers=auth_headers(owner),
    )
    assert response.status_code == 202
    assert response.json()["provider_type"] == "fake-contents"

    # A local playlist → 404.
    local = Playlist(name="Local", owner_id=str(other_user.id), visibility="private")
    db_session.add(local)
    await db_session.flush()
    response = client.post(
        f"/api/v1/playlists/{local.id}/provider-sync",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 404
