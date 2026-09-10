"""Tests for the aggregate /api/v1/search/ endpoint."""

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.genre import Genre, GenreTrack
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.tag import Tag, TagTrack
from songhive.models.track import Track
from songhive.models.user import User


async def _make_artist(session, name: str = "Test Artist") -> Artist:
    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _make_album(
    session,
    artist: Artist,
    title: str = "Test Album",
    owner: User | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Album:
    album = Album(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(album)
    await session.flush()
    return album


async def _make_track(
    session,
    artist: Artist,
    album: Album | None = None,
    title: str = "Test Track",
    owner: User | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Track:
    track = Track(
        title=title,
        artist_id=artist.id,
        album_id=album.id if album is not None else None,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(track)
    await session.flush()
    return track


async def _make_playlist(
    session,
    owner: User,
    name: str = "Test Playlist",
    description: str | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Playlist:
    playlist = Playlist(
        name=name,
        owner_id=owner.id,
        description=description,
        visibility=visibility,
    )
    session.add(playlist)
    await session.flush()
    return playlist


async def _make_library(
    session,
    owner: User,
    name: str = "Test Library",
    description: str | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Library:
    library = Library(
        name=name,
        owner_id=owner.id,
        description=description,
        visibility=visibility,
    )
    session.add(library)
    await session.flush()
    return library


async def _make_tag(session, name: str) -> Tag:
    tag = Tag(name=name)
    session.add(tag)
    await session.flush()
    return tag


async def _make_genre(session, name: str) -> Genre:
    genre = Genre(name=name)
    session.add(genre)
    await session.flush()
    return genre


def _section_ids(data, entity):
    for section in data["sections"]:
        if section["entity"] == entity:
            return [item["id"] for item in section["items"]]
    return []


def _section_total(data, entity):
    for section in data["sections"]:
        if section["entity"] == entity:
            return section["total"]
    return 0


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(client):
    """An empty q short-circuits to an empty response."""
    response = client.get("/api/v1/search")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == ""
    assert data["sections"] == []

    response = client.get("/api/v1/search?q=")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == ""
    assert data["sections"] == []


@pytest.mark.asyncio
async def test_search_entity_allowlist(client, regular_user, db_session, auth_headers):
    """The ``entities`` allowlist limits the returned sections."""
    artist = await _make_artist(db_session, name="Rep")
    album = await _make_album(db_session, artist, title="Repeater", owner=regular_user)
    await db_session.commit()

    response = client.get("/api/v1/search?q=rep&entities=artists,albums")
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["albums", "artists"]
    assert _section_ids(data, "artists") == [str(artist.id)]
    assert _section_ids(data, "albums") == [str(album.id)]
    assert _section_total(data, "tracks") == 0


@pytest.mark.asyncio
async def test_search_respects_limit(client, regular_user, db_session):
    """``limit`` caps per-section items but total counts remain unfiltered."""
    artist = await _make_artist(db_session, name="Shared Artist")
    for i in range(3):
        await _make_track(
            db_session,
            artist,
            title=f"Track {i}",
            owner=regular_user,
        )
    await db_session.commit()

    response = client.get("/api/v1/search?q=track&entities=tracks&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["sections"]) == 1
    section = data["sections"][0]
    assert section["entity"] == "tracks"
    assert len(section["items"]) == 2
    assert section["total"] == 3


@pytest.mark.asyncio
async def test_search_visibility_anonymous(client, regular_user, other_user, db_session, auth_headers):
    """Anonymous users only see public items; owners see their private ones."""
    artist = await _make_artist(db_session, name="Visible Artist")
    public_track = await _make_track(
        db_session,
        artist,
        title="Public Track",
        owner=regular_user,
        visibility=Visibility.PUBLIC.value,
    )
    private_track = await _make_track(
        db_session,
        artist,
        title="Private Track",
        owner=regular_user,
        visibility=Visibility.PRIVATE.value,
    )
    other_private = await _make_track(
        db_session,
        artist,
        title="Other Private Track",
        owner=other_user,
        visibility=Visibility.PRIVATE.value,
    )
    await db_session.commit()

    response = client.get("/api/v1/search?q=track&entities=tracks")
    assert response.status_code == 200
    data = response.json()
    ids = _section_ids(data, "tracks")
    assert str(public_track.id) in ids
    assert str(private_track.id) not in ids
    assert str(other_private.id) not in ids

    response = client.get(
        "/api/v1/search?q=track&entities=tracks",
        headers=auth_headers(regular_user),
    )
    data = response.json()
    ids = _section_ids(data, "tracks")
    assert str(public_track.id) in ids
    assert str(private_track.id) in ids
    assert str(other_private.id) not in ids


@pytest.mark.asyncio
async def test_search_includes_subtitles_and_urls(client, regular_user, db_session):
    """Tracks, albums, and artists include useful subtitle and URL metadata."""
    artist = await _make_artist(db_session, name="Paranoid Collective")
    album = await _make_album(db_session, artist, title="Paranoid", owner=regular_user)
    track = await _make_track(db_session, artist, album, title="Paranoid Android", owner=regular_user)
    await db_session.commit()

    response = client.get("/api/v1/search?q=paranoid&entities=tracks,albums,artists")
    assert response.status_code == 200
    data = response.json()

    track_items = [s for s in data["sections"] if s["entity"] == "tracks"][0]["items"]
    assert len(track_items) == 1
    assert track_items[0]["url"] == f"/tracks/{track.id}"
    assert "Paranoid Collective" in track_items[0]["subtitle"]
    assert "Paranoid" in track_items[0]["subtitle"]

    album_items = [s for s in data["sections"] if s["entity"] == "albums"][0]["items"]
    assert album_items[0]["url"] == f"/albums/{album.id}"
    assert album_items[0]["subtitle"] == "Paranoid Collective"

    artist_items = [s for s in data["sections"] if s["entity"] == "artists"][0]["items"]
    assert artist_items[0]["url"] == f"/artists/{artist.id}"


@pytest.mark.asyncio
async def test_search_playlists_and_libraries(client, regular_user, db_session):
    """Playlists and libraries are searchable by name and description."""
    playlist = await _make_playlist(
        db_session,
        regular_user,
        name="Summer Vibes",
        description="Sunny archive tracks",
    )
    library = await _make_library(
        db_session,
        regular_user,
        name="Summer Archive",
        description="Sunny vibes files",
    )
    await db_session.commit()

    for query in ["Summer", "sunny", "VIBES", "archive"]:
        response = client.get(f"/api/v1/search?q={query}&entities=playlists,libraries")
        assert response.status_code == 200
        data = response.json()
        assert str(playlist.id) in _section_ids(data, "playlists")
        assert str(library.id) in _section_ids(data, "libraries")


@pytest.mark.asyncio
async def test_search_users(client, regular_user, other_user, db_session):
    """Users appear in search as active public profiles."""
    regular_user.display_name = "Alice"
    other_user.is_active = False
    await db_session.commit()

    response = client.get("/api/v1/search?q=ali&entities=users")
    assert response.status_code == 200
    data = response.json()
    ids = _section_ids(data, "users")
    assert str(regular_user.username) in ids
    assert str(other_user.username) not in ids

    user_item = [s for s in data["sections"] if s["entity"] == "users"][0]["items"][0]
    assert user_item["url"] == f"/@{regular_user.username}"
    assert user_item["title"] == "Alice"


@pytest.mark.asyncio
async def test_search_tags_and_genres(client, regular_user, db_session):
    """Tags and genres are returned with item counts and named links."""
    track = await _make_track(
        db_session,
        await _make_artist(db_session, name="Artist"),
        title="Tagged Track",
        owner=regular_user,
    )
    tag = await _make_tag(db_session, "summer_beats")
    genre = await _make_genre(db_session, "indie")
    db_session.add(TagTrack(tag_id=tag.id, track_id=track.id, user_id=regular_user.id))
    db_session.add(GenreTrack(genre_id=genre.id, track_id=track.id))
    await db_session.commit()

    response = client.get("/api/v1/search?q=summer&entities=tags")
    assert response.status_code == 200
    data = response.json()
    tag_items = [s for s in data["sections"] if s["entity"] == "tags"][0]["items"]
    assert any(item["id"] == "summer_beats" and item["url"] == "/tags/summer_beats" for item in tag_items)

    response = client.get("/api/v1/search?q=indie&entities=genres")
    assert response.status_code == 200
    data = response.json()
    genre_items = [s for s in data["sections"] if s["entity"] == "genres"][0]["items"]
    assert any(item["id"] == "indie" and item["url"] == "/genres/indie" for item in genre_items)


@pytest.mark.asyncio
async def test_search_unknown_entities_ignored(client):
    """Unknown values in ``entities`` are ignored."""
    response = client.get("/api/v1/search?q=test&entities=tracks,unknown")
    assert response.status_code == 200
    data = response.json()
    assert [s["entity"] for s in data["sections"]] == ["tracks"]
