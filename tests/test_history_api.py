"""
Tests for the listening history API.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.track import Track


@pytest.mark.asyncio
async def test_record_listen(client, db_session, regular_user, auth_headers):
    """POST /history/{track_id} records a listen and GET /history returns it."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="History Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.commit()

    headers = auth_headers(regular_user)
    post = client.post(f"/api/v1/history/{track.id}", headers=headers)
    assert post.status_code == 201
    assert post.json() == {"track_id": str(track.id), "recorded": True}

    get = client.get("/api/v1/history/", headers=headers)
    assert get.status_code == 200
    data = get.json()
    assert len(data) == 1
    assert data[0]["track_id"] == str(track.id)
    assert data[0]["title"] == "History Track"
    assert data[0]["artist"] == "Artist"
    assert "created_at" in data[0]


@pytest.mark.asyncio
async def test_history_list_respects_limit_offset(client, db_session, regular_user, auth_headers):
    """GET /history supports limit and offset pagination."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    tracks = []
    for i in range(3):
        track = Track(
            title=f"Track {i}",
            artist_id=artist.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
        )
        db_session.add(track)
        tracks.append(track)
    await db_session.commit()

    headers = auth_headers(regular_user)
    for track in tracks:
        response = client.post(f"/api/v1/history/{track.id}", headers=headers)
        assert response.status_code == 201

    get = client.get("/api/v1/history/?limit=2&offset=1", headers=headers)
    assert get.status_code == 200
    data = get.json()
    assert len(data) == 2


def test_history_post_requires_auth(client):
    """POST /history/{track_id} without authentication returns 401."""
    response = client.post("/api/v1/history/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 401


def test_history_get_requires_auth(client):
    """GET /history without authentication returns 401."""
    response = client.get("/api/v1/history/")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_history_post_missing_track(client, db_session, regular_user, auth_headers):
    """POST on a missing track returns 404."""
    response = client.post(
        "/api/v1/history/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_history_post_private_track_forbidden(client, db_session, regular_user, other_user, auth_headers):
    """POST on a track the user cannot access returns 403."""
    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Private Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.commit()

    response = client.post(f"/api/v1/history/{track.id}", headers=auth_headers(other_user))
    assert response.status_code == 403
