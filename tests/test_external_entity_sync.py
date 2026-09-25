"""Tests for the entity-backed external-provider sync path.

Uses an in-memory fake adapter marked ``entity_import`` that yields
``ExternalItemRef`` track items plus album/artist/playlist entities, so no
HTTP or provider SDK is needed.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

import pytest
from sqlalchemy import func, select

from songhive.external.base import ExternalLibraryAdapter
from songhive.external.registry import register_external_adapter
from songhive.external.sync import sync_external_library
from songhive.external.types import (
    ExternalAlbumMetadata,
    ExternalArtistMetadata,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalPlaylistEntry,
    ExternalPlaylistMetadata,
    ExternalTrackMetadata,
)
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.external_item import ExternalItem
from songhive.models.external_library import ExternalLibrary
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import secrets


class FakeEntityAdapter(ExternalLibraryAdapter):
    """Entity-backed fake adapter: config["entities"] carries the fixtures."""

    provider_type = "fake-entity"
    user_configurable = True

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        entities = config.get("entities")
        if not isinstance(entities, dict):
            from songhive.external.errors import ExternalConfigError

            raise ExternalConfigError('config["entities"] must be a dict', field="entities")
        self._capabilities = ExternalLibraryCapabilities(
            list_items=bool(config.get("include_tracks", True)),
            read_bytes=False,
            stream_url=True,
            range_read=True,
            download=True,
            compute_hash=False,
            read_tags=False,
            write_tags=False,
            rename_source=False,
            delete_source=False,
            detect_changes=True,
            validate_config=True,
            list_albums=bool(config.get("include_albums", True)),
            list_artists=bool(config.get("include_artists", True)),
            list_playlists=bool(config.get("include_playlists", True)),
            limits={"checksum_algorithm": None, "entity_import": True},
        )
        return self._capabilities

    async def iter_items(self, config: dict, since=None, scope=None) -> AsyncIterator[ExternalItemRef]:
        for entry in config.get("entities", {}).get("tracks", []):
            yield ExternalItemRef(
                provider_key=entry["provider_key"],
                display_path=entry.get("display_path", entry["provider_key"]),
                etag=entry.get("etag"),
                size=entry.get("size"),
                mime_type=entry.get("mime_type", "audio/flac"),
                metadata=ExternalTrackMetadata(**entry["metadata"]),
            )

    async def iter_albums(self, config: dict, since=None, scope=None) -> AsyncIterator[ExternalAlbumMetadata]:
        for entry in config.get("entities", {}).get("albums", []):
            yield ExternalAlbumMetadata(**entry)

    async def iter_artists(self, config: dict, since=None, scope=None) -> AsyncIterator[ExternalArtistMetadata]:
        for entry in config.get("entities", {}).get("artists", []):
            yield ExternalArtistMetadata(**entry)

    async def open_stream(self, config, item, *, range=None):
        from songhive.external.types import ExternalStream

        return ExternalStream(
            kind="url",
            url=f"https://provider.invalid/audio/{item.provider_key}",
            headers={"X-Fake-Token": "secret"},
            content_type=item.mime_type,
            size=item.size,
            supports_range=True,
            safe_to_redirect=False,
            content_range=(f"bytes {range[0]}-{range[1]}/{item.size}" if range and item.size else None),
        )

    async def iter_playlists(self, config: dict, since=None, scope=None) -> AsyncIterator[ExternalPlaylistMetadata]:
        for entry in config.get("entities", {}).get("playlists", []):
            entry = dict(entry)
            entries = tuple(
                ExternalPlaylistEntry(position=i, track_provider_key=key)
                for i, key in enumerate(entry.pop("entries", ()))
            )
            yield ExternalPlaylistMetadata(entries=entries, **entry)


@pytest.fixture(autouse=True)
def _register_fake_entity_adapter():
    register_external_adapter("fake-entity", FakeEntityAdapter)


@pytest.fixture
def _make_user(db_session):
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
def _make_entity_library(db_session, _make_user):
    async def _inner(entities: dict, config_extra: Optional[dict] = None):
        owner = await _make_user("elib")
        library = Library(name="Entity Library", owner_id=str(owner.id), visibility="private")
        db_session.add(library)
        await db_session.flush()

        config = {"entities": entities}
        config.update(config_extra or {})
        external_library = ExternalLibrary(
            library_id=str(library.id),
            provider_type="fake-entity",
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
        "raw_metadata": {"Id": provider_key},
    }
    metadata.update(metadata_overrides)
    return {"provider_key": provider_key, "etag": f"etag-{provider_key}", "size": 1000, "metadata": metadata}


@pytest.mark.asyncio
async def test_entity_sync_creates_track_entities(db_session, fake_redis, _make_entity_library):
    """First entity sync creates Track/Artist/Album + ExternalItem + LibraryTrack."""
    external_library, library, _ = await _make_entity_library({"tracks": [_track("jf-1"), _track("jf-2")]})

    run = await sync_external_library(
        db_session,
        str(external_library.id),
        triggered_by="manual",
        triggered_by_user_id=external_library.created_by_id,
        redis=fake_redis,
    )

    assert run.status == "success"
    assert run.items_seen == 2
    assert run.details["entities"]["tracks_created"] == 2

    tracks = (await db_session.execute(select(Track))).scalars().all()
    assert len(tracks) == 2
    for track in tracks:
        assert track.source == "external"
        assert track.audio_file_id is None
        assert track.owner_id == library.owner_id
        assert track.visibility == library.visibility

    refs = (await db_session.execute(select(ExternalItem))).scalars().all()
    assert len(refs) == 2
    assert {r.kind for r in refs} == {"track"}
    assert all(r.state == "active" for r in refs)
    assert all(r.track_id is not None for r in refs)

    links = (await db_session.execute(select(LibraryTrack))).scalars().all()
    assert {str(lt.track_id) for lt in links} == {str(t.id) for t in tracks}

    artists = (await db_session.execute(select(Artist))).scalars().all()
    assert len(artists) == 1 and artists[0].name == "Artist A"
    albums = (await db_session.execute(select(Album))).scalars().all()
    assert len(albums) == 1 and albums[0].title == "Album A"

    # No file-provider rows are created for entity providers.
    from songhive.models.external_track import ExternalTrack

    assert (await db_session.scalar(select(func.count()).select_from(ExternalTrack))) == 0


@pytest.mark.asyncio
async def test_entity_sync_is_idempotent_on_etag(db_session, fake_redis, _make_entity_library):
    """Re-syncing with unchanged etags reuses rows and does not duplicate."""
    external_library, _, _ = await _make_entity_library({"tracks": [_track("jf-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    run = await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)

    assert run.status == "success"
    assert run.details["entities"].get("tracks_created", 0) == 0
    assert (await db_session.scalar(select(func.count()).select_from(Track))) == 1
    assert (await db_session.scalar(select(func.count()).select_from(ExternalItem))) == 1


@pytest.mark.asyncio
async def test_entity_sync_updates_on_new_etag(db_session, fake_redis, _make_entity_library):
    """A changed etag rewrites the track's metadata."""
    external_library, _, _ = await _make_entity_library({"tracks": [_track("jf-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)

    # Second sync: same provider key, new etag, new title.
    config = secrets.decrypt_json(external_library.config)
    config["entities"]["tracks"][0]["etag"] = "etag-v2"
    config["entities"]["tracks"][0]["metadata"]["title"] = "Renamed Title"
    external_library.config = secrets.encrypt_json(config)
    await db_session.flush()

    run = await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    assert run.details["entities"]["tracks_updated"] == 1
    track = (await db_session.execute(select(Track))).scalar_one()
    assert track.title == "Renamed Title"


@pytest.mark.asyncio
async def test_entity_sync_marks_missing(db_session, fake_redis, _make_entity_library):
    """Items absent from a full listing are marked missing and unlinked."""
    external_library, library, _ = await _make_entity_library({"tracks": [_track("jf-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)

    config = secrets.decrypt_json(external_library.config)
    config["entities"]["tracks"] = []
    external_library.config = secrets.encrypt_json(config)
    await db_session.flush()

    run = await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    assert run.tracks_missing == 1

    ref = (await db_session.execute(select(ExternalItem))).scalar_one()
    assert ref.state == "missing"
    # The Track row is kept (favorites/history may reference it) but the
    # library membership is removed.
    assert (await db_session.scalar(select(func.count()).select_from(Track))) == 1
    assert (await db_session.scalar(select(func.count()).select_from(LibraryTrack))) == 0


@pytest.mark.asyncio
async def test_entity_sync_imports_albums_artists_playlists(db_session, fake_redis, _make_entity_library):
    """Albums, artists, and playlists are materialized as local entities."""
    external_library, _, owner = await _make_entity_library(
        {
            "tracks": [_track("jf-1"), _track("jf-2")],
            "artists": [
                {
                    "provider_key": "jf-artist-1",
                    "name": "Artist A",
                    "bio": "A bio",
                    "etag": "e1",
                    "provider_ids": {"MusicBrainzArtist": "mb-a"},
                }
            ],
            "albums": [
                {
                    "provider_key": "jf-album-1",
                    "title": "Album A",
                    "artist_names": ("Artist A",),
                    "release_year": 2001,
                    "etag": "e2",
                }
            ],
            "playlists": [
                {
                    "provider_key": "jf-pl-1",
                    "title": "My Mix",
                    "description": "d",
                    "entries": ("jf-2", "jf-1", "missing-track"),
                    "etag": "e3",
                }
            ],
        }
    )

    run = await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    assert run.status == "success"

    kinds = {(r.kind, r.provider_key) for r in (await db_session.execute(select(ExternalItem))).scalars().all()}
    assert ("artist", "jf-artist-1") in kinds
    assert ("album", "jf-album-1") in kinds
    assert ("playlist", "jf-pl-1") in kinds

    playlist = (await db_session.execute(select(Playlist))).scalar_one()
    assert playlist.name == "My Mix"
    assert str(playlist.owner_id) == str(owner.id)

    entries = (
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
    # "missing-track" is skipped; order is preserved for the rest.
    assert len(entries) == 2
    track_ids_to_key = {
        str(r.track_id): r.provider_key
        for r in (await db_session.execute(select(ExternalItem))).scalars().all()
        if r.kind == "track"
    }
    # position 0 -> jf-2, position 1 -> jf-1
    assert entries[0].position == 0
    assert entries[1].position == 1
    ordered_tracks = [track_ids_to_key.get(str(e.track_id)) for e in entries]
    assert ordered_tracks == ["jf-2", "jf-1"]

    assert run.details["entities"].get("playlist_entries_skipped") == 1


@pytest.mark.asyncio
async def test_entity_track_extra_artists(db_session, fake_redis, _make_entity_library):
    """Multi-artist track metadata populates Track.extra_artists."""
    external_library, _, _ = await _make_entity_library(
        {
            "tracks": [
                _track(
                    "jf-1",
                    artist="Primary Artist",
                    artists=("Primary Artist", "Feat Artist", "Second Guest"),
                )
            ]
        }
    )
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    track = (await db_session.execute(select(Track))).scalar_one()
    assert track.artist is not None and track.artist.name == "Primary Artist"
    assert track.extra_artists == ["Feat Artist", "Second Guest"]


@pytest.mark.asyncio
async def test_entity_sync_provider_playlist_removed_when_absent(db_session, fake_redis, _make_entity_library):
    """A playlist missing from a later listing is deleted with its reference."""
    external_library, _, _ = await _make_entity_library(
        {
            "tracks": [_track("jf-1")],
            "playlists": [{"provider_key": "jf-pl-1", "title": "Mix", "entries": ("jf-1",), "etag": "e"}],
        }
    )
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    assert (await db_session.scalar(select(func.count()).select_from(Playlist))) == 1

    config = secrets.decrypt_json(external_library.config)
    config["entities"]["playlists"] = []
    external_library.config = secrets.encrypt_json(config)
    await db_session.flush()

    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    assert (await db_session.scalar(select(func.count()).select_from(Playlist))) == 0
    # The ExternalItem row is removed with the entity (exactly-one-FK check).
    playlist_refs = (
        (await db_session.execute(select(ExternalItem).where(ExternalItem.kind == "playlist"))).scalars().all()
    )
    assert playlist_refs == []


@pytest.mark.asyncio
async def test_entity_track_resolves_stream(db_session, fake_redis, _make_entity_library):
    """``resolve_external_stream`` dispatches on ``Track.external_item``."""
    from songhive.services.streaming import resolve_external_stream

    external_library, _, _ = await _make_entity_library({"tracks": [_track("jf-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    track = (await db_session.execute(select(Track))).scalar_one()

    stream = await resolve_external_stream(db_session, str(track.id))
    assert stream is not None
    assert stream.kind == "url"
    assert stream.url == "https://provider.invalid/audio/jf-1"
    assert stream.safe_to_redirect is False
    assert stream.headers["X-Fake-Token"] == "secret"


@pytest.mark.asyncio
async def test_entity_track_stream_forwards_range(db_session, fake_redis, _make_entity_library):
    """A Range header is forwarded to the adapter when size is known."""
    from songhive.services.streaming import resolve_external_stream

    external_library, _, _ = await _make_entity_library({"tracks": [_track("jf-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)
    track = (await db_session.execute(select(Track))).scalar_one()

    stream = await resolve_external_stream(db_session, str(track.id), range_header="bytes=0-499")
    assert stream is not None
    assert stream.content_range == "bytes 0-499/1000"


@pytest.mark.asyncio
async def test_entity_track_missing_state_blocks_stream(db_session, fake_redis, _make_entity_library):
    """A missing entity reference raises instead of streaming."""
    from songhive.external.errors import ExternalItemNotFound
    from songhive.services.streaming import resolve_external_stream

    external_library, _, _ = await _make_entity_library({"tracks": [_track("jf-1")]})
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)

    config = secrets.decrypt_json(external_library.config)
    config["entities"]["tracks"] = []
    external_library.config = secrets.encrypt_json(config)
    await db_session.flush()
    await sync_external_library(db_session, str(external_library.id), triggered_by="manual", redis=fake_redis)

    track = (await db_session.execute(select(Track))).scalar_one()
    with pytest.raises(ExternalItemNotFound):
        await resolve_external_stream(db_session, str(track.id))
