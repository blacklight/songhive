"""
Tests for the playlist API endpoints.
"""

import io

import pytest

from songhive.models._enums import Visibility


@pytest.fixture
def sample_playlists(client, regular_user, auth_headers):
    """Create a public, local, and private playlist owned by ``regular_user``."""
    headers = auth_headers(regular_user)
    playlists = []
    for name, visibility in [
        ("Public Playlist", Visibility.PUBLIC),
        ("Local Playlist", Visibility.LOCAL),
        ("Private Playlist", Visibility.PRIVATE),
    ]:
        response = client.post(
            "/api/v1/playlists/",
            params={"visibility": visibility.value},
            json={"name": name},
            headers=headers,
        )
        assert response.status_code == 201
        playlists.append(response.json())
    return playlists


def _names(response):
    """Return the set of playlist names in a list response."""
    return {playlist["name"] for playlist in response.json()}


def test_list_playlists_filters_by_visibility(client, sample_playlists, regular_user, other_user, auth_headers):
    """List endpoints only return playlists the requester may access."""
    assert _names(client.get("/api/v1/playlists")) == {"Public Playlist"}

    other = client.get("/api/v1/playlists", headers=auth_headers(other_user))
    assert _names(other) == {"Public Playlist", "Local Playlist"}

    owner = client.get("/api/v1/playlists", headers=auth_headers(regular_user))
    assert _names(owner) == {"Public Playlist", "Local Playlist", "Private Playlist"}


def test_get_public_playlist_redacts_owner_for_non_owner(client, sample_playlists, other_user, auth_headers):
    """Non-owners see a null owner_id for public playlists."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "public")

    response = client.get(f"/api/v1/playlists/{playlist['id']}", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] is None
    assert response.json()["visibility"] == "public"


def test_get_private_playlist_denied_for_other_user(client, sample_playlists, other_user, auth_headers):
    """Private playlists are denied (403) for other authenticated users."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "private")

    response = client.get(f"/api/v1/playlists/{playlist['id']}", headers=auth_headers(other_user))
    assert response.status_code == 403


def test_get_playlist_as_owner_sees_owner_id(client, sample_playlists, regular_user, auth_headers):
    """The owner sees their own owner_id on a playlist."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "private")

    response = client.get(f"/api/v1/playlists/{playlist['id']}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] == str(regular_user.id)


def test_create_playlist_sets_owner_and_visibility(client, regular_user, auth_headers):
    """Creating a playlist sets owner and visibility from the query parameter."""
    response = client.post(
        "/api/v1/playlists/?visibility=public",
        json={"name": "My Playlist"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["owner_id"] == str(regular_user.id)
    assert data["visibility"] == "public"


def test_create_playlist_invalid_visibility_returns_422(client, regular_user, auth_headers):
    """Creating a playlist with an unknown visibility value returns 422."""
    response = client.post(
        "/api/v1/playlists/?visibility=publick",
        json={"name": "Bad Playlist"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_get_missing_playlist_returns_404(client):
    """Requesting a missing playlist returns 404."""
    response = client.get("/api/v1/playlists/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_private_playlist_with_share_token(client, sample_playlists, regular_user, auth_headers):
    """A share URL token grants anonymous access to a private playlist."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "private")

    create = client.post(
        "/api/v1/share-urls",
        json={"item_type": "playlist", "item_id": playlist["id"]},
        headers=auth_headers(regular_user),
    )
    assert create.status_code == 201
    token = create.json()["token"]

    response = client.get(f"/api/v1/playlists/{playlist['id']}?token={token}")
    assert response.status_code == 200
    assert response.json()["id"] == playlist["id"]


def test_update_playlist_metadata(client, sample_playlists, regular_user, auth_headers):
    """Owners can update a playlist's name, description, and visibility."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "private")
    response = client.patch(
        f"/api/v1/playlists/{playlist['id']}",
        json={"name": "Updated", "description": "Updated description", "visibility": "public"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Updated"
    assert body["description"] == "Updated description"
    assert body["visibility"] == "public"
    assert "image_url" in body
    assert "cover_url" in body


def test_upload_and_delete_playlist_images(client, sample_playlists, regular_user, auth_headers):
    """Owners can upload and remove playlist image and cover."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "public")
    headers = auth_headers(regular_user)

    image = client.post(
        f"/api/v1/playlists/{playlist['id']}/image",
        files={"file": ("image.jpg", io.BytesIO(b"fake image"), "image/jpeg")},
        headers=headers,
    )
    assert image.status_code == 200
    assert image.json()["image_url"] is not None

    cover = client.post(
        f"/api/v1/playlists/{playlist['id']}/cover",
        files={"file": ("cover.jpg", io.BytesIO(b"fake cover"), "image/jpeg")},
        headers=headers,
    )
    assert cover.status_code == 200
    assert cover.json()["cover_url"] is not None

    delete = client.delete(f"/api/v1/playlists/{playlist['id']}/image", headers=headers)
    assert delete.status_code == 200
    assert delete.json()["image_url"] is None

    delete_cover = client.delete(f"/api/v1/playlists/{playlist['id']}/cover", headers=headers)
    assert delete_cover.status_code == 200
    assert delete_cover.json()["cover_url"] is None
