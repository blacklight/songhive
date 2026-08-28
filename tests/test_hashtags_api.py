"""
Tests for the hashtag API endpoints.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.services.hashtags import add_hashtags_to_entity


@pytest.fixture
async def tagged_track(db_session, regular_user):
    """Create a public track with a hashtag attached."""
    artist = Artist(name="API Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="API Track",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    await add_hashtags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)
    return track


def test_list_hashtag_items(tagged_track, client):
    """A valid hashtag returns its tagged items."""
    response = client.get("/api/v1/hashtags/rock")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["type"] == "track"
    assert body[0]["id"] == str(tagged_track.id)


def test_list_hashtag_items_invalid_name_returns_404(client):
    """Malformed hashtag names in the URL return 404 instead of 500."""
    response = client.get("/api/v1/hashtags/foo%20bar")
    assert response.status_code == 404


def test_list_user_hashtag_items_invalid_name_returns_404(client, regular_user):
    """Malformed hashtag names on the user-scoped endpoint return 404."""
    response = client.get(f"/api/v1/users/{regular_user.id}/hashtags/foo%20bar")
    assert response.status_code == 404


def test_delete_global_hashtag(tagged_track, client, admin_user, auth_headers, db_session):
    """Admins can delete a hashtag globally."""
    response = client.delete("/api/v1/hashtags/rock", headers=auth_headers(admin_user))
    assert response.status_code == 204


def test_delete_global_hashtag_invalid_name_returns_404(client, admin_user, auth_headers):
    """Deleting a malformed hashtag name returns 404."""
    response = client.delete("/api/v1/hashtags/%23%23%23", headers=auth_headers(admin_user))
    assert response.status_code == 404


def test_delete_global_hashtag_missing_returns_404(client, admin_user, auth_headers):
    """Deleting a non-existent valid hashtag name returns 404."""
    response = client.delete("/api/v1/hashtags/nope", headers=auth_headers(admin_user))
    assert response.status_code == 404
