"""
Tests for the artist API endpoints.
"""

import io

import pytest

from songhive.models.artist import Artist


@pytest.fixture
async def sample_artists(db_session):
    """Create artists for testing."""
    artists = []
    for name in ["Public Artist", "Local Artist"]:
        artist = Artist(name=name)
        db_session.add(artist)
        artists.append(artist)
    await db_session.flush()
    return artists


def test_get_artist(client, sample_artists):
    """Fetching an artist returns its public profile."""
    artist = sample_artists[0]
    response = client.get(f"/api/v1/artists/{artist.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(artist.id)
    assert body["name"] == artist.name


def test_admin_update_artist(client, admin_user, sample_artists, auth_headers):
    """Admins can update artist metadata."""
    artist = sample_artists[0]
    response = client.patch(
        f"/api/v1/artists/{artist.id}",
        json={"name": "Renamed Artist", "bio": "A bio"},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed Artist"
    assert body["bio"] == "A bio"


def test_non_admin_cannot_update_artist(client, regular_user, sample_artists, auth_headers):
    """Regular users cannot update artist metadata."""
    artist = sample_artists[0]
    response = client.patch(
        f"/api/v1/artists/{artist.id}",
        json={"name": "Renamed"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 403


def test_admin_upload_and_delete_artist_images(client, admin_user, sample_artists, auth_headers):
    """Admins can upload and remove artist image and cover."""
    artist = sample_artists[0]
    headers = auth_headers(admin_user)

    image = client.post(
        f"/api/v1/artists/{artist.id}/image",
        files={"file": ("image.jpg", io.BytesIO(b"fake image"), "image/jpeg")},
        headers=headers,
    )
    assert image.status_code == 200
    assert image.json()["image_url"] is not None

    cover = client.post(
        f"/api/v1/artists/{artist.id}/cover",
        files={"file": ("cover.jpg", io.BytesIO(b"fake cover"), "image/jpeg")},
        headers=headers,
    )
    assert cover.status_code == 200
    assert cover.json()["cover_url"] is not None

    delete = client.delete(f"/api/v1/artists/{artist.id}/image", headers=headers)
    assert delete.status_code == 200
    assert delete.json()["image_url"] is None

    delete_cover = client.delete(f"/api/v1/artists/{artist.id}/cover", headers=headers)
    assert delete_cover.status_code == 200
    assert delete_cover.json()["cover_url"] is None
