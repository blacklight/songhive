"""Tests for the ExternalItem entity-reference model."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.external_item import ExternalItem
from songhive.models.external_library import ExternalLibrary
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.track import Track
from songhive.services import secrets


@pytest.fixture
def _make_external_library(db_session, make_user):
    async def _inner() -> ExternalLibrary:
        user = await make_user("owner")
        library = Library(name="Lib", owner_id=str(user.id), visibility="private")
        db_session.add(library)
        await db_session.flush()
        ext_lib = ExternalLibrary(
            library_id=str(library.id),
            provider_type="jellyfin",
            config=secrets.encrypt_json({}),
            created_by_id=str(user.id),
        )
        db_session.add(ext_lib)
        await db_session.flush()
        return ext_lib

    return _inner


@pytest.mark.asyncio
async def test_external_item_requires_exactly_one_entity(db_session, _make_external_library):
    ext_lib = await _make_external_library()

    db_session.add(
        ExternalItem(
            external_library_id=str(ext_lib.id),
            kind="track",
            provider_key="no-entity",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    artist = Artist(name="A")
    track = Track(title="T", artist_id=str(artist.id) if artist.id else "x")
    db_session.add(artist)
    await db_session.flush()
    track = Track(title="T", artist_id=str(artist.id))
    db_session.add(track)
    await db_session.flush()

    db_session.add(
        ExternalItem(
            external_library_id=str(ext_lib.id),
            kind="track",
            provider_key="two-entities",
            track_id=str(track.id),
            artist_id=str(artist.id),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_external_item_unique_per_library_kind_key(db_session, _make_external_library):
    ext_lib = await _make_external_library()
    artist = Artist(name="A")
    db_session.add(artist)
    await db_session.flush()

    db_session.add(
        ExternalItem(
            external_library_id=str(ext_lib.id),
            kind="artist",
            provider_key="k1",
            artist_id=str(artist.id),
        )
    )
    await db_session.flush()

    other = Artist(name="B")
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        ExternalItem(
            external_library_id=str(ext_lib.id),
            kind="artist",
            provider_key="k1",
            artist_id=str(other.id),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_external_items_cascade_with_library(db_session, _make_external_library):
    await db_session.execute(text("PRAGMA foreign_keys = ON"))
    ext_lib = await _make_external_library()
    artist = Artist(name="A")
    db_session.add(artist)
    await db_session.flush()
    db_session.add(
        ExternalItem(
            external_library_id=str(ext_lib.id),
            kind="artist",
            provider_key="k1",
            artist_id=str(artist.id),
        )
    )
    await db_session.flush()

    await db_session.delete(ext_lib)
    await db_session.flush()

    remaining = await db_session.scalar(select(func.count()).select_from(ExternalItem))
    assert remaining == 0


@pytest.mark.asyncio
async def test_track_external_item_relationship(db_session, _make_external_library):
    ext_lib = await _make_external_library()
    artist = Artist(name="A")
    db_session.add(artist)
    await db_session.flush()
    track = Track(title="T", artist_id=str(artist.id), source="external")
    db_session.add(track)
    await db_session.flush()
    db_session.add(
        ExternalItem(
            external_library_id=str(ext_lib.id),
            kind="track",
            provider_key="jelly-1",
            track_id=str(track.id),
        )
    )
    await db_session.flush()
    db_session.expunge_all()

    loaded = await db_session.scalar(select(Track).where(Track.id == str(track.id)))
    assert loaded is not None
    assert loaded.external_item is not None
    assert loaded.external_item.provider_key == "jelly-1"

    album = Album(title="Al", artist_id=str(artist.id))
    playlist = Playlist(name="Pl", owner_id=None)
    db_session.add(album)
    db_session.add(playlist)
    await db_session.flush()
    db_session.add(
        ExternalItem(
            external_library_id=str(ext_lib.id),
            kind="album",
            provider_key="alb-1",
            album_id=str(album.id),
        )
    )
    await db_session.flush()
