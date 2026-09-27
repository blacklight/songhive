"""Tests for membership/lazy/immutable behavior in the entity sync path.

Uses a fake entity adapter whose capabilities mimic TIDAL
(``immutable_tracks`` + ``lazy_contents``) so no HTTP/SDK is needed.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

import pytest
from sqlalchemy import func, select

from songhive.external.base import ExternalLibraryAdapter
from songhive.external.registry import register_external_adapter
from songhive.external.sync import sync_external_library
from songhive.external.types import (
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalPlaylistMetadata,
    ExternalTrackMetadata,
)
from songhive.models.external_item import ExternalItem
from songhive.models.external_library import ExternalLibrary
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.provider_catalog import ProviderCatalogEntry
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import secrets


class FakeLazyAdapter(ExternalLibraryAdapter):
    """Entity adapter advertising TIDAL-style lazy/immutable capabilities."""

    provider_type = "fake-lazy"
    user_configurable = True

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        self._capabilities = ExternalLibraryCapabilities(
            list_items=True,
            stream_url=True,
            download=True,
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
                "editable_fields": ["genres", "tags"],
            },
        )
        return self._capabilities

    async def iter_items(self, config: dict, since=None, scope=None) -> AsyncIterator[ExternalItemRef]:
        for entry in config.get("entities", {}).get("tracks", []):
            yield ExternalItemRef(
                provider_key=entry["provider_key"],
                display_path=entry.get("display_path", entry["provider_key"]),
                etag=entry.get("etag"),
                mime_type="audio/flac",
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
            # Lazy providers never carry entries in the listing.
            entry.pop("entries", None)
            yield ExternalPlaylistMetadata(**entry)


@pytest.fixture(autouse=True)
def _register_fake_lazy_adapter():
    register_external_adapter("fake-lazy", FakeLazyAdapter)
    yield
    from songhive.external.registry import unregister_external_adapter

    unregister_external_adapter("fake-lazy")


@pytest.fixture
def _make_lazy_library(db_session):
    async def _inner(entities: dict, config_extra: Optional[dict] = None):
        owner = User(
            username="lazy-owner",
            email="lazy@example.com",
            password_hash="x" * 60,
            role="user",
            is_active=True,
        )
        db_session.add(owner)
        await db_session.flush()
        library = Library(name="Lazy Library", owner_id=str(owner.id), visibility="private")
        db_session.add(library)
        await db_session.flush()
        config = {"entities": entities}
        config.update(config_extra or {})
        external_library = ExternalLibrary(
            library_id=str(library.id),
            provider_type="fake-lazy",
            config=secrets.encrypt_json(config),
            enabled=True,
            created_by_id=str(owner.id),
        )
        db_session.add(external_library)
        await db_session.flush()
        return external_library, library, owner

    return _inner


def _track(provider_key: str, **overrides) -> dict:
    etag = overrides.pop("etag", None)
    metadata = {
        "title": f"Title {provider_key}",
        "artist": "Artist A",
        "album": "Album A",
        "album_artist": "Artist A",
        "duration": 180.0,
        "raw_metadata": {"id": provider_key, "title": f"Title {provider_key}"},
    }
    metadata.update(overrides)
    return {
        "provider_key": provider_key,
        "etag": etag or f"etag-{provider_key}",
        "metadata": metadata,
    }


async def _update_entities(external_library, entities: dict, db_session) -> None:
    config = secrets.decrypt_json(external_library.config)
    config["entities"] = entities
    external_library.config = secrets.encrypt_json(config)
    await db_session.flush()


@pytest.mark.asyncio
async def test_lazy_sync_creates_saved_tracks_no_playlist_entries(db_session, fake_redis, _make_lazy_library):
    """Saved tracks get LibraryTrack rows; lazy playlists get none."""
    external_library, library, _ = await _make_lazy_library(
        {
            "tracks": [_track("t-1"), _track("t-2")],
            "playlists": [
                {
                    "provider_key": "pl-1",
                    "title": "Lazy Mix",
                    "entries": ("t-1", "t-2"),
                }
            ],
        }
    )
    run = await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    assert run.status == "success"

    refs = (await db_session.execute(select(ExternalItem))).scalars().all()
    assert all(r.membership == "saved" for r in refs)
    tracks = (await db_session.execute(select(Track))).scalars().all()
    assert len(tracks) == 2
    links = (await db_session.execute(select(LibraryTrack))).scalars().all()
    assert len(links) == 2

    # The playlist exists but has no PlaylistTrack rows — contents are lazy.
    playlist = (await db_session.execute(select(Playlist))).scalar_one()
    assert playlist.name == "Lazy Mix"
    assert (
        await db_session.scalar(
            select(func.count()).select_from(PlaylistTrack).where(PlaylistTrack.playlist_id == str(playlist.id))
        )
    ) == 0


@pytest.mark.asyncio
async def test_immutable_second_sync_skips_track_writes(db_session, fake_redis, _make_lazy_library):
    """Immutable providers don't rewrite track metadata on re-sync."""
    external_library, _, _ = await _make_lazy_library({"tracks": [_track("t-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    track = (await db_session.execute(select(Track))).scalar_one()
    original_title = track.title
    original_synced = track.external_metadata_synced_at

    # Provider side changed the title AND etag — immutable path ignores it.
    await _update_entities(
        external_library,
        {"tracks": [_track("t-1", title="Changed", etag="etag-v2")]},
        db_session,
    )
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    await db_session.refresh(track)
    assert track.title == original_title
    assert track.external_metadata_synced_at == original_synced


@pytest.mark.asyncio
async def test_catalog_write_once_on_sync(db_session, fake_redis, _make_lazy_library):
    """Track raw payloads land in the catalog and are never rewritten."""
    external_library, _, _ = await _make_lazy_library({"tracks": [_track("t-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)

    entry = (
        await db_session.execute(
            select(ProviderCatalogEntry).where(
                ProviderCatalogEntry.provider_type == "fake-lazy",
                ProviderCatalogEntry.kind == "track",
            )
        )
    ).scalar_one()
    assert entry.provider_key == "t-1"
    assert entry.payload["title"] == "Title t-1"

    # Re-sync with a changed payload — catalog payload stays write-once.
    await _update_entities(
        external_library,
        {"tracks": [_track("t-1", title="CHANGED", etag="e2")]},
        db_session,
    )
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    await db_session.refresh(entry)
    assert entry.payload["title"] == "Title t-1"


@pytest.mark.asyncio
async def test_demote_saved_track_still_referenced(db_session, fake_redis, _make_lazy_library):
    """A track removed from the collection but still in a playlist demotes."""
    external_library, library, _ = await _make_lazy_library({"tracks": [_track("t-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    track = (await db_session.execute(select(Track))).scalar_one()

    # Reference it from a local playlist.
    playlist = Playlist(name="Local", owner_id=library.owner_id, visibility="private")
    db_session.add(playlist)
    await db_session.flush()
    db_session.add(PlaylistTrack(playlist_id=str(playlist.id), track_id=str(track.id), position=0))
    await db_session.flush()

    # Remove from the saved collection.
    await _update_entities(external_library, {"tracks": []}, db_session)
    run = await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    assert run.tracks_missing == 1

    ref = (await db_session.execute(select(ExternalItem))).scalar_one()
    assert ref.membership == "referenced"
    assert ref.state == "active"  # demoted, not missing
    assert (await db_session.scalar(select(func.count()).select_from(LibraryTrack))) == 0  # library membership removed


@pytest.mark.asyncio
async def test_missing_track_not_referenced_goes_missing(db_session, fake_redis, _make_lazy_library):
    """A saved track with no remaining references marks missing."""
    external_library, _, _ = await _make_lazy_library({"tracks": [_track("t-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    await _update_entities(external_library, {"tracks": []}, db_session)
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    ref = (await db_session.execute(select(ExternalItem))).scalar_one()
    assert ref.state == "missing"
    assert ref.membership == "saved"


@pytest.mark.asyncio
async def test_promote_referenced_to_saved(db_session, fake_redis, _make_lazy_library):
    """A referenced row reappearing in the collection promotes to saved."""
    external_library, library, _ = await _make_lazy_library({"tracks": [_track("t-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    track = (await db_session.execute(select(Track))).scalar_one()
    ref = (await db_session.execute(select(ExternalItem))).scalar_one()

    # Demote it (as if dropped from the collection but still referenced).
    playlist = Playlist(name="Local", owner_id=library.owner_id, visibility="private")
    db_session.add(playlist)
    await db_session.flush()
    db_session.add(PlaylistTrack(playlist_id=str(playlist.id), track_id=str(track.id), position=0))
    ref.membership = "referenced"
    await db_session.flush()

    # Saved listing includes it again → promote + LibraryTrack restored.
    await _update_entities(external_library, {"tracks": [_track("t-1")]}, db_session)
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    await db_session.refresh(ref)
    assert ref.membership == "saved"
    assert (await db_session.scalar(select(func.count()).select_from(LibraryTrack))) == 1
