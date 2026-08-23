"""
Tests for the favorites API.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.track import Track


async def _make_track(db_session, owner, visibility: str = Visibility.PUBLIC.value) -> Track:
    """Create and persist a test track with an artist."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Test Track",
        artist_id=artist.id,
        owner_id=str(owner.id) if owner is not None else None,
        visibility=visibility,
    )
    db_session.add(track)
    await db_session.commit()
    return track


@pytest.mark.asyncio
async def test_add_favorite(client, db_session, regular_user, auth_headers):
    """POST /favorites/{track_id} adds the track to the user's favorites."""
    track = await _make_track(db_session, owner=regular_user)
    headers = auth_headers(regular_user)

    post = client.post(f"/api/v1/favorites/{track.id}", headers=headers)
    assert post.status_code == 201
    data = post.json()
    assert data["track_id"] == str(track.id)
    assert "id" in data
    assert "created_at" in data

    get = client.get("/api/v1/favorites/", headers=headers)
    assert get.status_code == 200
    items = get.json()
    assert len(items) == 1
    assert items[0]["track_id"] == str(track.id)


@pytest.mark.asyncio
async def test_add_favorite_idempotent(client, db_session, regular_user, auth_headers):
    """Favoriting the same track twice is idempotent."""
    track = await _make_track(db_session, owner=regular_user)
    headers = auth_headers(regular_user)

    first = client.post(f"/api/v1/favorites/{track.id}", headers=headers)
    assert first.status_code == 201
    second = client.post(f"/api/v1/favorites/{track.id}", headers=headers)
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    get = client.get("/api/v1/favorites/", headers=headers)
    assert len(get.json()) == 1


@pytest.mark.asyncio
async def test_add_public_track_owned_by_other_user(client, db_session, regular_user, other_user, auth_headers):
    """A user can favorite another user's public track."""
    track = await _make_track(db_session, owner=other_user, visibility=Visibility.PUBLIC.value)
    response = client.post(f"/api/v1/favorites/{track.id}", headers=auth_headers(regular_user))
    assert response.status_code == 201
    assert response.json()["track_id"] == str(track.id)


@pytest.mark.asyncio
async def test_add_private_track_favorite_forbidden(client, db_session, regular_user, other_user, auth_headers):
    """Favoriting a private track owned by another user returns 403."""
    track = await _make_track(db_session, owner=other_user, visibility=Visibility.PRIVATE.value)
    response = client.post(f"/api/v1/favorites/{track.id}", headers=auth_headers(regular_user))
    assert response.status_code == 403
    assert response.json()["detail"] == "Access denied"


def test_add_favorite_requires_auth(client):
    """POST /favorites/{track_id} without authentication returns 401."""
    response = client.post("/api/v1/favorites/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_add_favorite_missing_track(client, regular_user, auth_headers):
    """Favoriting a non-existent track returns 404."""
    response = client.post(
        "/api/v1/favorites/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_favorite(client, db_session, regular_user, auth_headers):
    """DELETE /favorites/{track_id} removes the track from favorites."""
    track = await _make_track(db_session, owner=regular_user)
    headers = auth_headers(regular_user)
    client.post(f"/api/v1/favorites/{track.id}", headers=headers)

    delete = client.delete(f"/api/v1/favorites/{track.id}", headers=headers)
    assert delete.status_code == 204

    get = client.get("/api/v1/favorites/", headers=headers)
    assert get.json() == []


def test_remove_favorite_nonexistent(client, regular_user, auth_headers):
    """DELETE on a track that is not favorited returns 204."""
    response = client.delete(
        "/api/v1/favorites/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_remove_favorite_requires_auth(client):
    """DELETE /favorites/{track_id} without authentication returns 401."""
    response = client.delete("/api/v1/favorites/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_favorite_list_isolated_per_user(client, db_session, regular_user, other_user, auth_headers):
    """Each user only sees their own favorites."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PUBLIC.value)
    regular_headers = auth_headers(regular_user)
    other_headers = auth_headers(other_user)

    client.post(f"/api/v1/favorites/{track.id}", headers=regular_headers)
    client.post(f"/api/v1/favorites/{track.id}", headers=other_headers)

    regular_list = client.get("/api/v1/favorites/", headers=regular_headers)
    other_list = client.get("/api/v1/favorites/", headers=other_headers)

    assert len(regular_list.json()) == 1
    assert len(other_list.json()) == 1
    assert regular_list.json()[0]["track_id"] == str(track.id)
    assert regular_list.json()[0]["id"] != other_list.json()[0]["id"]
