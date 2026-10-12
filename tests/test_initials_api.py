"""Tests for the ``/initials`` endpoints that back the A–Z list index."""

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.playlist import Playlist
from songhive.models.track import Track

PUBLIC = Visibility.PUBLIC.value
PRIVATE = Visibility.PRIVATE.value


async def _artist(db_session, name: str, visibility: str = PUBLIC) -> Artist:
    """Create an artist with one track so it shows up in listings."""
    artist = Artist(name=name)
    db_session.add(artist)
    await db_session.flush()
    db_session.add(Track(title=f"{name} track", artist_id=artist.id, visibility=visibility))
    await db_session.flush()
    return artist


async def _album(
    db_session,
    title: str,
    artist: Artist,
    visibility: str = PUBLIC,
) -> Album:
    """Create an album owned by nobody in particular."""
    album = Album(title=title, artist_id=artist.id, visibility=visibility)
    db_session.add(album)
    await db_session.flush()
    return album


async def _playlist(
    db_session,
    name: str,
    owner=None,
    visibility: str = PUBLIC,
) -> Playlist:
    """Create a playlist."""
    playlist = Playlist(
        name=name,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    db_session.add(playlist)
    await db_session.flush()
    return playlist


@pytest.mark.asyncio
async def test_artist_initials_offsets(client, db_session):
    """Each present letter maps to the offset of its first artist."""
    for name in ["123 Band", "Alpha", "Beta", "Zulu"]:
        await _artist(db_session, name)
    await db_session.commit()

    response = client.get("/api/v1/artists/initials")
    assert response.status_code == 200
    assert response.json() == {"letters": {"#": 0, "A": 1, "B": 2, "Z": 3}}


@pytest.mark.asyncio
async def test_artist_initials_folds_diacritics(client, db_session):
    """Accented names bucket under their base letter."""
    await _artist(db_session, "Élodie")
    await db_session.commit()

    response = client.get("/api/v1/artists/initials")
    assert response.json() == {"letters": {"E": 0}}


@pytest.mark.asyncio
async def test_artist_initials_match_list_offsets(client, db_session):
    """Jumping to a reported offset lands on an artist under that letter."""
    for name in ["Alpha", "Alps", "Beta", "Zulu"]:
        await _artist(db_session, name)
    await db_session.commit()

    letters = client.get("/api/v1/artists/initials").json()["letters"]
    assert letters == {"A": 0, "B": 2, "Z": 3}

    for letter, offset in letters.items():
        page = client.get(f"/api/v1/artists/?offset={offset}&limit=1&sort_by=name").json()
        assert page[0]["name"].upper().startswith(letter)


@pytest.mark.asyncio
async def test_artist_initials_desc_order(client, db_session):
    """Offsets reflect the requested sort direction."""
    for name in ["Alpha", "Beta", "Zulu"]:
        await _artist(db_session, name)
    await db_session.commit()

    letters = client.get("/api/v1/artists/initials?sort_dir=desc").json()["letters"]
    assert letters == {"Z": 0, "B": 1, "A": 2}


@pytest.mark.asyncio
async def test_artist_initials_honours_query_filter(client, db_session):
    """The initials only cover artists matching the search query."""
    for name in ["Alpha", "Beta", "Alps"]:
        await _artist(db_session, name)
    await db_session.commit()

    letters = client.get("/api/v1/artists/initials?q=alp").json()["letters"]
    assert letters == {"A": 0}


@pytest.mark.asyncio
async def test_artist_initials_respects_acl(client, db_session):
    """Artists with no accessible content are not indexed."""
    await _artist(db_session, "Hidden Act", visibility=PRIVATE)
    await _artist(db_session, "Public Act")
    await db_session.commit()

    letters = client.get("/api/v1/artists/initials").json()["letters"]
    assert letters == {"P": 0}


@pytest.mark.asyncio
async def test_album_initials_by_title(client, db_session):
    """Album initials follow album titles when sorted by title."""
    artist = await _artist(db_session, "The Band")
    for title in ["123 Overture", "Apple Pie", "Zebra"]:
        await _album(db_session, title, artist)
    await db_session.commit()

    response = client.get("/api/v1/albums/initials")
    assert response.status_code == 200
    assert response.json() == {"letters": {"#": 0, "A": 1, "Z": 2}}


@pytest.mark.asyncio
async def test_album_initials_by_artist_name(client, db_session):
    """With ``sort_by=artist_name`` the albums bucket under their artist."""
    aaron = await _artist(db_session, "Aaron")
    zed = await _artist(db_session, "Zed")
    await _album(db_session, "Zebra", aaron)
    await _album(db_session, "Apple Pie", zed)
    await db_session.commit()

    letters = client.get("/api/v1/albums/initials?sort_by=artist_name").json()["letters"]
    assert letters == {"A": 0, "Z": 1}


@pytest.mark.asyncio
async def test_album_initials_respects_acl(client, db_session, regular_user):
    """Private albums are not indexed for anonymous callers."""
    artist = await _artist(db_session, "The Band")
    await _album(db_session, "Secret Album", artist, visibility=PRIVATE)
    await _album(db_session, "Open Album", artist)
    await db_session.commit()

    letters = client.get("/api/v1/albums/initials").json()["letters"]
    assert letters == {"O": 0}


@pytest.mark.asyncio
async def test_album_initials_honours_filters(client, db_session):
    """Year-range filters narrow the indexed album set."""
    artist = await _artist(db_session, "The Band")
    old = await _album(db_session, "Ancient LP", artist)
    old.release_year = 1970
    new = await _album(db_session, "New LP", artist)
    new.release_year = 2020
    await db_session.commit()

    letters = client.get("/api/v1/albums/initials?year_from=2000").json()["letters"]
    assert letters == {"N": 0}


@pytest.mark.asyncio
async def test_playlist_initials_offsets(client, db_session):
    """Each present letter maps to the offset of its first playlist."""
    for name in ["123 Mix", "Morning", "Zen"]:
        await _playlist(db_session, name)
    await db_session.commit()

    response = client.get("/api/v1/playlists/initials")
    assert response.status_code == 200
    assert response.json() == {"letters": {"#": 0, "M": 1, "Z": 2}}


@pytest.mark.asyncio
async def test_playlist_initials_respects_visibility(client, db_session, regular_user, other_user, auth_headers):
    """Private playlists are only indexed for users who can see them."""
    await _playlist(db_session, "Other Mix", owner=other_user, visibility=PRIVATE)
    await _playlist(db_session, "Public Mix", owner=regular_user)
    await db_session.commit()

    letters = client.get("/api/v1/playlists/initials").json()["letters"]
    assert letters == {"P": 0}

    letters = client.get("/api/v1/playlists/initials", headers=auth_headers(other_user)).json()["letters"]
    assert letters == {"O": 0, "P": 1}


@pytest.mark.asyncio
async def test_playlist_initials_editable_filter(client, db_session, regular_user, other_user, auth_headers):
    """``editable=true`` restricts the index to playlists the user can edit."""
    await _playlist(db_session, "Mine", owner=regular_user, visibility=PRIVATE)
    await _playlist(db_session, "Theirs", owner=other_user, visibility=PUBLIC)
    await db_session.commit()

    letters = client.get("/api/v1/playlists/initials?editable=true", headers=auth_headers(regular_user)).json()[
        "letters"
    ]
    assert letters == {"M": 0}


@pytest.mark.asyncio
async def test_initials_empty_result(client):
    """Empty collections yield an empty letter map."""
    assert client.get("/api/v1/artists/initials").json() == {"letters": {}}
    assert client.get("/api/v1/albums/initials").json() == {"letters": {}}
    assert client.get("/api/v1/playlists/initials").json() == {"letters": {}}


@pytest.mark.asyncio
async def test_initials_owner_filter_not_found(client):
    """Unknown ``owner_username`` values are rejected."""
    response = client.get("/api/v1/artists/initials?owner_username=nobody")
    assert response.status_code == 404
