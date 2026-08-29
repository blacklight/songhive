"""
Tests for the genre API endpoints.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.services.genres import set_genres_for_entity


@pytest.fixture
async def genre_track(db_session, regular_user):
    """Create a public track with a genre attached."""
    artist = Artist(name="Genre Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Genre Track",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        genre="rock",
    )
    db_session.add(track)
    await db_session.flush()

    await set_genres_for_entity(db_session, "track", track.id, ["rock"])
    return track


@pytest.fixture
async def genre_album(db_session, regular_user):
    """Create a public album with a genre attached."""
    artist = Artist(name="Genre Album Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Genre Album",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        genre="jazz",
    )
    db_session.add(album)
    await db_session.flush()

    await set_genres_for_entity(db_session, "album", album.id, ["jazz"])
    return album


@pytest.fixture
async def private_track(db_session, regular_user):
    """Create a private track owned by ``regular_user``."""
    artist = Artist(name="Private Track Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Private Track",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.flush()
    return track


@pytest.fixture
async def private_album(db_session, regular_user):
    """Create a private album owned by ``regular_user``."""
    artist = Artist(name="Private Album Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Private Album",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(album)
    await db_session.flush()
    return album


def _find_genre(response, name):
    """Return the genre summary with the given name, if any."""
    for item in response.json():
        if item["name"] == name:
            return item
    return None


def test_list_genres(genre_track, client):
    """Genres visible to the requester are listed with counts."""
    response = client.get("/api/v1/genres")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "rock"
    assert body[0]["item_count"] == 1


def test_list_genres_search(genre_track, genre_album, client):
    """The q parameter filters genre names."""
    response = client.get("/api/v1/genres?q=roc")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "rock"


def test_list_genre_items(genre_track, client):
    """A valid genre returns its tagged items."""
    response = client.get("/api/v1/genres/rock")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["type"] == "track"
    assert body[0]["id"] == str(genre_track.id)


def test_list_genre_items_invalid_name_returns_404(client):
    """Malformed genre names in the URL return 404 instead of 500."""
    response = client.get("/api/v1/genres/foo%23bar")
    assert response.status_code == 404


def test_delete_global_genre(genre_track, client, admin_user, auth_headers, db_session):
    """Admins can delete a genre globally."""
    response = client.delete("/api/v1/genres/rock", headers=auth_headers(admin_user))
    assert response.status_code == 204


def test_delete_global_genre_invalid_name_returns_404(client, admin_user, auth_headers):
    """Deleting a malformed genre name returns 404."""
    response = client.delete("/api/v1/genres/%23%23%23", headers=auth_headers(admin_user))
    assert response.status_code == 404


def test_delete_global_genre_missing_returns_404(client, admin_user, auth_headers):
    """Deleting a non-existent valid genre name returns 404."""
    response = client.delete("/api/v1/genres/nope", headers=auth_headers(admin_user))
    assert response.status_code == 404


def test_set_track_genres(client, private_track, regular_user, auth_headers):
    """Owners can set a track's genres via the sub-route."""
    track = private_track
    headers = auth_headers(regular_user)

    response = client.post(
        f"/api/v1/tracks/{track.id}/genres",
        json={"genres": ["rock", "indie"]},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["genre"] == "rock; indie"
    assert set(data["genres"]) == {"rock", "indie"}


def test_remove_track_genre(client, private_track, regular_user, auth_headers):
    """Owners can remove a single genre from a track."""
    track = private_track
    headers = auth_headers(regular_user)

    client.post(
        f"/api/v1/tracks/{track.id}/genres",
        json={"genres": ["rock", "indie"]},
        headers=headers,
    )

    response = client.delete(
        f"/api/v1/tracks/{track.id}/genres/rock",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["genre"] == "indie"
    assert data["genres"] == ["indie"]


def test_set_track_genres_denied_for_other_user(client, private_track, other_user, auth_headers):
    """Non-owners cannot set a track's genres."""
    response = client.post(
        f"/api/v1/tracks/{private_track.id}/genres",
        json={"genres": ["rock"]},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_track_response_includes_genres_with_include(client, private_track, regular_user, auth_headers):
    """The genres list is populated when explicitly included."""
    client.post(
        f"/api/v1/tracks/{private_track.id}/genres",
        json={"genres": ["rock"]},
        headers=auth_headers(regular_user),
    )

    response = client.get(
        f"/api/v1/tracks/{private_track.id}?include=genres",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert response.json()["genres"] == ["rock"]


def test_update_track_genre_via_patch(client, private_track, regular_user, auth_headers, db_session):
    """Updating a track's genre via PATCH also creates genre associations."""
    response = client.patch(
        f"/api/v1/tracks/{private_track.id}",
        json={"genre": "Rock, Pop"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["genre"] == "Rock, Pop"
    assert set(data["genres"]) == {"pop", "rock"}


def test_set_album_genres(client, private_album, regular_user, auth_headers):
    """Owners can set an album's genres via the sub-route."""
    album = private_album
    headers = auth_headers(regular_user)

    response = client.post(
        f"/api/v1/albums/{album.id}/genres",
        json={"genres": ["jazz", "fusion"]},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["genre"] == "jazz; fusion"
    assert set(data["genres"]) == {"fusion", "jazz"}


def test_remove_album_genre(client, private_album, regular_user, auth_headers):
    """Owners can remove a single genre from an album."""
    album = private_album
    headers = auth_headers(regular_user)

    client.post(
        f"/api/v1/albums/{album.id}/genres",
        json={"genres": ["jazz", "fusion"]},
        headers=headers,
    )

    response = client.delete(
        f"/api/v1/albums/{album.id}/genres/jazz",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["genre"] == "fusion"
    assert data["genres"] == ["fusion"]


def test_album_response_includes_genres_with_include(client, private_album, regular_user, auth_headers):
    """The album genres list is populated when explicitly included."""
    client.post(
        f"/api/v1/albums/{private_album.id}/genres",
        json={"genres": ["jazz"]},
        headers=auth_headers(regular_user),
    )

    response = client.get(
        f"/api/v1/albums/{private_album.id}?include=genres",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert response.json()["genres"] == ["jazz"]


def test_update_album_genre_via_patch(client, private_album, regular_user, auth_headers):
    """Updating an album's genre via PATCH also creates genre associations."""
    response = client.patch(
        f"/api/v1/albums/{private_album.id}",
        json={"genre": "Jazz; Blues"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["genre"] == "Jazz; Blues"
    assert set(data["genres"]) == {"blues", "jazz"}
