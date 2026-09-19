"""
Tests for the collection API endpoints and collection list filtering.
"""

import pytest
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.collection_item import CollectionItem
from songhive.models.track import Track


@pytest.fixture
def sample_libraries(client, regular_user, auth_headers):
    """Create a public, local, and private library owned by ``regular_user``."""
    headers = auth_headers(regular_user)
    libraries = {}
    for key, visibility in [
        ("public", Visibility.PUBLIC),
        ("local", Visibility.LOCAL),
        ("private", Visibility.PRIVATE),
    ]:
        response = client.post(
            "/api/v1/libraries/",
            params={"visibility": visibility.value},
            json={"name": f"{key} library"},
            headers=headers,
        )
        assert response.status_code == 201
        libraries[key] = response.json()
    return libraries


def _names(response):
    """Return the set of entity names in a list response."""
    return {item["name"] for item in response.json()}


def test_save_and_remove_library(client, sample_libraries, other_user, auth_headers):
    """A user can save an accessible library and remove it later."""
    headers = auth_headers(other_user)
    library = sample_libraries["public"]

    response = client.post(f"/api/v1/collection/library/{library['id']}", headers=headers)
    assert response.status_code == 201
    data = response.json()
    assert data["item_type"] == "library"
    assert data["item_id"] == library["id"]

    listing = client.get("/api/v1/collection/", headers=headers)
    assert listing.status_code == 200
    assert [item["item_id"] for item in listing.json()] == [library["id"]]

    response = client.delete(f"/api/v1/collection/library/{library['id']}", headers=headers)
    assert response.status_code == 204
    assert client.get("/api/v1/collection/", headers=headers).json() == []


def test_save_is_idempotent(client, sample_libraries, other_user, auth_headers):
    """Saving the same item twice keeps a single collection row."""
    headers = auth_headers(other_user)
    library = sample_libraries["public"]

    for _ in range(2):
        response = client.post(f"/api/v1/collection/library/{library['id']}", headers=headers)
        assert response.status_code == 201

    listing = client.get("/api/v1/collection/", headers=headers)
    assert len(listing.json()) == 1


def test_remove_unsaved_item_returns_204(client, sample_libraries, other_user, auth_headers):
    """Removing an item that was never saved is a no-op 204."""
    response = client.delete(
        f"/api/v1/collection/library/{sample_libraries['public']['id']}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 204


def test_save_private_library_denied(client, sample_libraries, other_user, auth_headers):
    """Content the requester cannot access cannot be saved."""
    response = client.post(
        f"/api/v1/collection/library/{sample_libraries['private']['id']}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_save_local_library_allowed(client, sample_libraries, other_user, auth_headers):
    """Local content is accessible to authenticated users and can be saved."""
    response = client.post(
        f"/api/v1/collection/library/{sample_libraries['local']['id']}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 201


def test_save_missing_item_returns_404(client, other_user, auth_headers):
    """Saving a nonexistent item returns 404."""
    response = client.post(
        "/api/v1/collection/library/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 404


def test_save_invalid_item_type_rejected(client, sample_libraries, other_user, auth_headers):
    """Unsupported item types are rejected."""
    response = client.post(
        f"/api/v1/collection/file/{sample_libraries['public']['id']}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 422


def test_collection_endpoints_require_auth(client, sample_libraries):
    """Save, remove, and list all require an authenticated user."""
    library = sample_libraries["public"]
    assert client.post(f"/api/v1/collection/library/{library['id']}").status_code == 401
    assert client.delete(f"/api/v1/collection/library/{library['id']}").status_code == 401
    assert client.get("/api/v1/collection/").status_code == 401


def test_collection_filter_shows_owned_and_saved(client, sample_libraries, regular_user, other_user, auth_headers):
    """collection=1 lists owned content plus explicitly saved items."""
    owner = client.get("/api/v1/libraries", params={"collection": 1}, headers=auth_headers(regular_user))
    assert _names(owner) == {"public library", "local library", "private library"}

    headers = auth_headers(other_user)
    client.post(
        f"/api/v1/collection/library/{sample_libraries['public']['id']}",
        headers=headers,
    )
    other = client.get("/api/v1/libraries", params={"collection": 1}, headers=headers)
    assert _names(other) == {"public library"}


def test_collection_filter_anonymous_returns_nothing(client, sample_libraries):
    """Anonymous requesters have no collection, so the filter matches nothing."""
    response = client.get("/api/v1/libraries", params={"collection": 1})
    assert response.status_code == 200
    assert response.json() == []


def test_in_collection_flag(client, sample_libraries, regular_user, other_user, auth_headers):
    """Responses expose in_collection for owned and saved content."""
    library = sample_libraries["public"]

    owner = client.get(f"/api/v1/libraries/{library['id']}", headers=auth_headers(regular_user))
    assert owner.json()["in_collection"] is True

    headers = auth_headers(other_user)
    before = client.get(f"/api/v1/libraries/{library['id']}", headers=headers)
    assert before.json()["in_collection"] is False

    client.post(f"/api/v1/collection/library/{library['id']}", headers=headers)

    after = client.get(f"/api/v1/libraries/{library['id']}", headers=headers)
    assert after.json()["in_collection"] is True

    listed = client.get("/api/v1/libraries", params={"collection": 1}, headers=headers)
    assert listed.json()[0]["in_collection"] is True


async def test_favorited_track_counts_as_collection(client, db_session, regular_user, other_user, auth_headers):
    """Favoriting a track places it in the user's collection."""
    artist = Artist(name="Sample Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="Fave Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    headers = auth_headers(other_user)
    response = client.post(f"/api/v1/favorites/{track.id}", headers=headers)
    assert response.status_code == 201

    listed = client.get("/api/v1/tracks", params={"collection": 1}, headers=headers)
    titles = {t["title"] for t in listed.json()}
    assert "Fave Track" in titles


async def test_deleting_saved_item_cleans_collection(
    client, db_session, sample_libraries, regular_user, other_user, auth_headers
):
    """Deleting a saved entity removes its collection rows."""
    headers = auth_headers(other_user)
    library = sample_libraries["public"]
    client.post(f"/api/v1/collection/library/{library['id']}", headers=headers)

    response = client.delete(f"/api/v1/libraries/{library['id']}", headers=auth_headers(regular_user))
    assert response.status_code == 204

    result = await db_session.execute(select(CollectionItem).where(CollectionItem.item_id == library["id"]))
    assert result.scalars().all() == []


def test_collection_list_item_type_filter(client, sample_libraries, other_user, auth_headers):
    """The collection list can be filtered by item type."""
    headers = auth_headers(other_user)
    client.post(
        f"/api/v1/collection/library/{sample_libraries['public']['id']}",
        headers=headers,
    )

    libraries = client.get("/api/v1/collection/", params={"item_type": "library"}, headers=headers)
    assert len(libraries.json()) == 1

    playlists = client.get("/api/v1/collection/", params={"item_type": "playlist"}, headers=headers)
    assert playlists.json() == []

    invalid = client.get("/api/v1/collection/", params={"item_type": "bogus"}, headers=headers)
    assert invalid.status_code == 422
