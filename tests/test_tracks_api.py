"""
Tests for the track API endpoints.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.track import Track


@pytest.fixture
async def sample_tracks(db_session, regular_user):
    """Create a public, local, and private track owned by ``regular_user``."""
    artist = Artist(name="Sample Artist")
    db_session.add(artist)
    await db_session.flush()

    tracks = []
    for title, visibility in [
        ("Public Track", Visibility.PUBLIC),
        ("Local Track", Visibility.LOCAL),
        ("Private Track", Visibility.PRIVATE),
    ]:
        track = Track(
            title=title,
            artist_id=artist.id,
            owner_id=str(regular_user.id),
            visibility=visibility.value,
        )
        db_session.add(track)
        tracks.append(track)
    await db_session.flush()
    return tracks


def _titles(response):
    """Return the set of track titles in a list response."""
    return {track["title"] for track in response.json()}


def test_list_tracks_filters_by_visibility(client, sample_tracks, regular_user, other_user, auth_headers):
    """List endpoints only return tracks the requester may access."""
    assert _titles(client.get("/api/v1/tracks")) == {"Public Track"}

    other = client.get("/api/v1/tracks", headers=auth_headers(other_user))
    assert _titles(other) == {"Public Track", "Local Track"}

    owner = client.get("/api/v1/tracks", headers=auth_headers(regular_user))
    assert _titles(owner) == {"Public Track", "Local Track", "Private Track"}


def test_get_public_track_redacts_owner_for_non_owner(client, sample_tracks, other_user, auth_headers):
    """Non-owners see a null owner_id for public tracks."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)

    response = client.get(f"/api/v1/tracks/{track.id}", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] is None
    assert response.json()["visibility"] == "public"


def test_get_private_track_denied_for_other_user(client, sample_tracks, other_user, auth_headers):
    """Private tracks are denied (403) for other authenticated users."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    response = client.get(f"/api/v1/tracks/{track.id}", headers=auth_headers(other_user))
    assert response.status_code == 403


def test_get_track_as_owner_sees_owner_id(client, sample_tracks, regular_user, auth_headers):
    """The owner sees their own owner_id on a track."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    response = client.get(f"/api/v1/tracks/{track.id}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] == str(regular_user.id)


def test_get_missing_track_returns_404(client):
    """Requesting a missing track returns 404."""
    response = client.get("/api/v1/tracks/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_private_track_with_share_token(client, sample_tracks, regular_user, auth_headers):
    """A share URL token grants anonymous access to a private track."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    create = client.post(
        "/api/v1/share-urls",
        json={"item_type": "track", "item_id": str(track.id)},
        headers=auth_headers(regular_user),
    )
    assert create.status_code == 201
    token = create.json()["token"]

    response = client.get(f"/api/v1/tracks/{track.id}?token={token}")
    assert response.status_code == 200
    assert response.json()["id"] == str(track.id)

    no_token = client.get(f"/api/v1/tracks/{track.id}")
    assert no_token.status_code == 403
