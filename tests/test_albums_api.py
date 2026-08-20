"""
Tests for the album API endpoints.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist


@pytest.fixture
async def sample_albums(db_session, regular_user):
    """Create a public, local, and private album owned by ``regular_user``."""
    artist = Artist(name="Sample Artist")
    db_session.add(artist)
    await db_session.flush()

    albums = []
    for title, visibility in [
        ("Public Album", Visibility.PUBLIC),
        ("Local Album", Visibility.LOCAL),
        ("Private Album", Visibility.PRIVATE),
    ]:
        album = Album(
            title=title,
            artist_id=artist.id,
            owner_id=str(regular_user.id),
            visibility=visibility.value,
        )
        db_session.add(album)
        albums.append(album)
    await db_session.flush()
    return albums


def _titles(response):
    """Return the set of album titles in a list response."""
    return {album["title"] for album in response.json()}


def test_list_albums_filters_by_visibility(client, sample_albums, regular_user, other_user, auth_headers):
    """List endpoints only return albums the requester may access."""
    assert _titles(client.get("/api/v1/albums")) == {"Public Album"}

    other = client.get("/api/v1/albums", headers=auth_headers(other_user))
    assert _titles(other) == {"Public Album", "Local Album"}

    owner = client.get("/api/v1/albums", headers=auth_headers(regular_user))
    assert _titles(owner) == {"Public Album", "Local Album", "Private Album"}


def test_get_public_album_redacts_owner_for_non_owner(client, sample_albums, other_user, auth_headers):
    """Non-owners see a null owner_id for public albums."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PUBLIC.value)

    response = client.get(f"/api/v1/albums/{album.id}", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] is None
    assert response.json()["visibility"] == "public"


def test_get_private_album_denied_for_other_user(client, sample_albums, other_user, auth_headers):
    """Private albums are denied (403) for other authenticated users."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)

    response = client.get(f"/api/v1/albums/{album.id}", headers=auth_headers(other_user))
    assert response.status_code == 403


def test_get_album_as_owner_sees_owner_id(client, sample_albums, regular_user, auth_headers):
    """The owner sees their own owner_id on an album."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)

    response = client.get(f"/api/v1/albums/{album.id}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] == str(regular_user.id)


def test_get_missing_album_returns_404(client):
    """Requesting a missing album returns 404."""
    response = client.get("/api/v1/albums/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_private_album_with_share_token(client, sample_albums, regular_user, auth_headers):
    """A share URL token grants anonymous access to a private album."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)

    create = client.post(
        "/api/v1/share-urls",
        json={"item_type": "album", "item_id": str(album.id)},
        headers=auth_headers(regular_user),
    )
    assert create.status_code == 201
    token = create.json()["token"]

    response = client.get(f"/api/v1/albums/{album.id}?token={token}")
    assert response.status_code == 200
    assert response.json()["id"] == str(album.id)

    no_token = client.get(f"/api/v1/albums/{album.id}")
    assert no_token.status_code == 403
