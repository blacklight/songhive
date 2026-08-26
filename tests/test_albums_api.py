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


def test_get_album_without_cover_file_uses_cover_url(client, sample_albums, regular_user, auth_headers):
    """Albums with no cover file fall back to the configured cover_url."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PUBLIC.value)
    album.cover_url = "https://example.com/cover.jpg"
    album.cover_file_id = None

    response = client.get(f"/api/v1/albums/{album.id}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["cover_url"] == "https://example.com/cover.jpg"


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


def test_update_album(client, sample_albums, regular_user, auth_headers):
    """Owners can partially update an album."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)
    headers = auth_headers(regular_user)

    response = client.patch(
        f"/api/v1/albums/{album.id}",
        json={
            "title": "Updated Album",
            "release_year": 2024,
            "description": "A new description",
            "visibility": "public",
        },
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Album"
    assert data["release_year"] == 2024
    assert data["description"] == "A new description"
    assert data["visibility"] == "public"


def test_delete_album(client, sample_albums, regular_user, auth_headers):
    """Owners can delete an album."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)
    headers = auth_headers(regular_user)

    response = client.delete(f"/api/v1/albums/{album.id}", headers=headers)
    assert response.status_code == 204

    get_response = client.get(f"/api/v1/albums/{album.id}", headers=headers)
    assert get_response.status_code == 404


def test_get_missing_album_returns_404_authenticated(client, auth_headers, regular_user):
    """Requesting a missing album returns 404 for authenticated users."""
    response = client.get(
        "/api/v1/albums/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_update_missing_album_returns_404(client, auth_headers, regular_user):
    """Updating a missing album returns 404."""
    response = client.patch(
        "/api/v1/albums/00000000-0000-0000-0000-000000000000",
        json={"title": "Updated"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_update_album_denied_for_other_user(client, sample_albums, other_user, auth_headers):
    """Non-owners cannot update another user's album."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)
    response = client.patch(
        f"/api/v1/albums/{album.id}",
        json={"title": "Hacked"},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_delete_album_denied_for_other_user(client, sample_albums, other_user, auth_headers):
    """Non-owners cannot delete another user's album."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)
    response = client.delete(f"/api/v1/albums/{album.id}", headers=auth_headers(other_user))
    assert response.status_code == 403


def test_update_album_visibility(client, sample_albums, regular_user, auth_headers):
    """Owners can change an album's visibility."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)
    response = client.patch(
        f"/api/v1/albums/{album.id}",
        json={"visibility": "public"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert response.json()["visibility"] == "public"


def test_delete_missing_album_returns_404(client, auth_headers, regular_user):
    """Deleting a missing album returns 404."""
    response = client.delete(
        "/api/v1/albums/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_get_album_uses_cover_url_when_cover_file_missing(client, sample_albums, regular_user, auth_headers):
    """Albums with a missing cover file fall back to the configured cover_url."""
    album = next(a for a in sample_albums if a.visibility == Visibility.PUBLIC.value)
    album.cover_file_id = "00000000-0000-0000-0000-000000000000"
    album.cover_url = "https://example.com/fallback-cover.jpg"

    response = client.get(f"/api/v1/albums/{album.id}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["cover_url"] == "https://example.com/fallback-cover.jpg"


@pytest.mark.asyncio
async def test_get_album_with_cover_file_returns_download_url(
    client, db_session, sample_albums, regular_user, auth_headers
):
    """Albums with a stored cover file return the file download URL."""
    from songhive.models.stored_file import StoredFile

    stored = StoredFile(
        storage_path="covers/abc",
        storage_backend="local",
        content_type="image/jpeg",
        size=0,
        sha256="abc",
        owner_id=str(regular_user.id),
    )
    db_session.add(stored)
    await db_session.flush()

    album = next(a for a in sample_albums if a.visibility == Visibility.PUBLIC.value)
    album.cover_file_id = str(stored.id)
    await db_session.flush()

    response = client.get(f"/api/v1/albums/{album.id}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["cover_url"] == f"/api/v1/files/{stored.id}/download"


def _patch_album_enrich(monkeypatch, *, count: int = 1):
    """Replace the album enrichment helper with a no-op that reports ``count``."""

    def _enqueue(track_id, force=True):
        return True

    async def _track_ids_for_album(*args, **kwargs):
        return [f"track-{i}" for i in range(count)]

    monkeypatch.setattr(
        "songhive.api.routes.albums._enqueue_track_enrichment",
        _enqueue,
    )
    monkeypatch.setattr(
        "songhive.services.music.get_track_ids_for_album",
        _track_ids_for_album,
    )


def test_enrich_album_allows_owner(client, sample_albums, regular_user, auth_headers, monkeypatch):
    """Owners can request MusicBrainz enrichment for an album's tracks."""
    _patch_album_enrich(monkeypatch, count=3)
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)

    response = client.post(
        f"/api/v1/albums/{album.id}/enrich",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["album_id"] == str(album.id)
    assert data["enqueued"] == 0  # mocked tracks do not exist


def test_enrich_album_denied_for_other_user(client, sample_albums, other_user, auth_headers, monkeypatch):
    """Non-owners cannot request enrichment for someone else's album."""
    _patch_album_enrich(monkeypatch, count=3)
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)

    response = client.post(
        f"/api/v1/albums/{album.id}/enrich",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_enrich_album_requires_auth(client, sample_albums, monkeypatch):
    """Anonymous users cannot request album enrichment."""
    _patch_album_enrich(monkeypatch, count=1)
    album = next(a for a in sample_albums if a.visibility == Visibility.PUBLIC.value)

    response = client.post(f"/api/v1/albums/{album.id}/enrich")
    assert response.status_code == 401


def test_enrich_missing_album_returns_404(client, auth_headers, regular_user, monkeypatch):
    """Requesting enrichment for a missing album returns 404."""
    _patch_album_enrich(monkeypatch, count=1)
    response = client.post(
        "/api/v1/albums/00000000-0000-0000-0000-000000000000/enrich",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_enrich_album_logs_audit_entry(
    client, db_session, sample_albums, regular_user, auth_headers, monkeypatch
):
    """Enriching an album creates an audit log entry."""
    from sqlalchemy import select

    from songhive.models.audit_log import AuditLog

    _patch_album_enrich(monkeypatch, count=3)
    album = next(a for a in sample_albums if a.visibility == Visibility.PRIVATE.value)

    response = client.post(
        f"/api/v1/albums/{album.id}/enrich",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200

    result = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "album.enrich",
            AuditLog.target_id == str(album.id),
        )
    )
    assert result is not None
