"""
Tests for the tag API endpoints.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.services.tags import add_tags_to_entity


@pytest.fixture
async def tagged_track(db_session, regular_user):
    """Create a public track with a tag attached."""
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

    await add_tags_to_entity(db_session, "track", track.id, ["rock"], user_id=regular_user.id)
    return track


@pytest.fixture
async def tagged_album(db_session, regular_user):
    """Create a public album with a tag attached."""
    artist = Artist(name="API Album Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="API Album",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(album)
    await db_session.flush()

    await add_tags_to_entity(db_session, "album", album.id, ["rock"], user_id=regular_user.id)
    return album


def test_list_tag_items(tagged_track, client):
    """A valid tag returns its tagged items."""
    response = client.get("/api/v1/tags/rock")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["type"] == "track"
    assert body[0]["id"] == str(tagged_track.id)


def test_list_tag_items_by_type(tagged_track, tagged_album, client):
    """The type query parameter filters tag items by entity type."""
    response = client.get("/api/v1/tags/rock?type=track")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["type"] == "track"
    assert body[0]["id"] == str(tagged_track.id)

    response = client.get("/api/v1/tags/rock?type=album")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["type"] == "album"
    assert body[0]["id"] == str(tagged_album.id)


def test_list_tag_items_invalid_type(client):
    """An invalid type query parameter returns a 422 error."""
    response = client.get("/api/v1/tags/rock?type=unknown")
    assert response.status_code == 422


def test_list_tag_items_invalid_name_returns_404(client):
    """Malformed tag names in the URL return 404 instead of 500."""
    response = client.get("/api/v1/tags/foo%20bar")
    assert response.status_code == 404


def test_list_user_tag_items_invalid_name_returns_404(client, regular_user):
    """Malformed tag names on the user-scoped endpoint return 404."""
    response = client.get(f"/api/v1/users/{regular_user.id}/tags/foo%20bar")
    assert response.status_code == 404


def test_delete_global_tag(tagged_track, client, admin_user, auth_headers, db_session):
    """Admins can delete a tag globally."""
    response = client.delete("/api/v1/tags/rock", headers=auth_headers(admin_user))
    assert response.status_code == 204


def test_delete_global_tag_invalid_name_returns_404(client, admin_user, auth_headers):
    """Deleting a malformed tag name returns 404."""
    response = client.delete("/api/v1/tags/%23%23%23", headers=auth_headers(admin_user))
    assert response.status_code == 404


def test_delete_global_tag_missing_returns_404(client, admin_user, auth_headers):
    """Deleting a non-existent valid tag name returns 404."""
    response = client.delete("/api/v1/tags/nope", headers=auth_headers(admin_user))
    assert response.status_code == 404
