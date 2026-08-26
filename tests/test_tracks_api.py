"""
Tests for the track API endpoints.
"""

import io
import uuid
from unittest.mock import MagicMock

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
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


def test_update_track(client, sample_tracks, regular_user, auth_headers):
    """Owners can partially update a track."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    headers = auth_headers(regular_user)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"title": "Updated Title", "genre": "Rock", "track_number": 3, "disc_number": 2},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["genre"] == "Rock"
    assert data["track_number"] == 3
    assert data["disc_number"] == 2


def test_delete_track(client, sample_tracks, regular_user, auth_headers):
    """Owners can delete a track."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    headers = auth_headers(regular_user)

    response = client.delete(f"/api/v1/tracks/{track.id}", headers=headers)
    assert response.status_code == 204

    get_response = client.get(f"/api/v1/tracks/{track.id}", headers=headers)
    assert get_response.status_code == 404


def test_update_missing_track_returns_404(client, auth_headers, regular_user):
    """Updating a missing track returns 404."""
    response = client.patch(
        "/api/v1/tracks/00000000-0000-0000-0000-000000000000",
        json={"title": "Updated"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_update_track_denied_for_other_user(client, sample_tracks, other_user, auth_headers):
    """Non-owners cannot update another user's track."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"title": "Hacked"},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_delete_track_denied_for_other_user(client, sample_tracks, other_user, auth_headers):
    """Non-owners cannot delete another user's track."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    response = client.delete(f"/api/v1/tracks/{track.id}", headers=auth_headers(other_user))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_delete_track_logs_audit_entry(
    client, db_session, sample_tracks, admin_user, auth_headers, monkeypatch
):
    """Admins deleting someone else's track via /tracks log an audit entry."""
    from sqlalchemy import select

    from songhive.models.audit_log import AuditLog

    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    assert str(track.owner_id) != str(admin_user.id)

    monkeypatch.setattr("songhive.api.routes.tracks.unpublish_track_activity", MagicMock(return_value=0))

    response = client.delete(f"/api/v1/tracks/{track.id}", headers=auth_headers(admin_user))
    assert response.status_code == 204

    result = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "track.admin_delete",
            AuditLog.target_id == str(track.id),
        )
    )
    assert result is not None


def test_delete_missing_track_returns_404(client, auth_headers, regular_user):
    """Deleting a missing track returns 404."""
    response = client.delete(
        "/api/v1/tracks/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def _patch_publish(monkeypatch):
    """Replace the route-level ``publish_track_activity`` with a recording mock."""
    mock = MagicMock(return_value=0)
    monkeypatch.setattr("songhive.api.routes.tracks.publish_track_activity", mock)
    return mock


def test_update_track_to_public_enqueues_publish(client, sample_tracks, regular_user, auth_headers, monkeypatch):
    """A private -> public visibility transition enqueues federation publication."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    mock = _patch_publish(monkeypatch)
    headers = auth_headers(regular_user)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"visibility": "public"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["visibility"] == "public"

    mock.assert_called_once()
    call_track, call_artist, call_owner, call_config, call_object_id = mock.call_args[0]
    assert str(call_track.id) == str(track.id)
    assert str(call_artist.id) == str(track.artist_id)
    assert str(call_owner.id) == str(regular_user.id)
    assert call_config is client.app.state.config
    assert call_object_id
    uuid.UUID(call_object_id)  # validates the generated publication id


def test_update_public_track_does_not_enqueue_publish(client, sample_tracks, regular_user, auth_headers, monkeypatch):
    """A public -> public visibility change does not re-enqueue publication."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    mock = _patch_publish(monkeypatch)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"title": "Still Public"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert mock.call_count == 0


def test_update_public_to_private_does_not_enqueue_publish(
    client, sample_tracks, regular_user, auth_headers, monkeypatch
):
    """A public -> private visibility change does not enqueue publication."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    mock = _patch_publish(monkeypatch)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"visibility": "private"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert mock.call_count == 0


def test_update_private_to_local_does_not_enqueue_publish(
    client, sample_tracks, regular_user, auth_headers, monkeypatch
):
    """A private -> local visibility change does not enqueue publication."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    mock = _patch_publish(monkeypatch)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"visibility": "local"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert mock.call_count == 0


def _patch_unpublish(monkeypatch):
    """Replace the route-level ``unpublish_track_activity`` with a recording mock."""
    mock = MagicMock(return_value=0)
    monkeypatch.setattr("songhive.api.routes.tracks.unpublish_track_activity", mock)
    return mock


def test_update_track_to_private_enqueues_unpublish(client, sample_tracks, regular_user, auth_headers, monkeypatch):
    """A public -> private visibility transition enqueues federation unpublish."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    mock = _patch_unpublish(monkeypatch)
    headers = auth_headers(regular_user)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"visibility": "private"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["visibility"] == "private"

    mock.assert_called_once()
    call_track, call_artist, call_owner, call_config, call_object_id = mock.call_args[0]
    assert str(call_track.id) == str(track.id)
    assert str(call_artist.id) == str(track.artist_id)
    assert str(call_owner.id) == str(regular_user.id)
    assert call_config is client.app.state.config
    assert call_object_id is None


def test_update_track_to_local_enqueues_unpublish(client, sample_tracks, regular_user, auth_headers, monkeypatch):
    """A public -> local visibility transition enqueues federation unpublish."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    mock = _patch_unpublish(monkeypatch)
    headers = auth_headers(regular_user)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"visibility": "local"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["visibility"] == "local"

    mock.assert_called_once()


def test_delete_public_track_enqueues_unpublish(client, sample_tracks, regular_user, auth_headers, monkeypatch):
    """Deleting a public track enqueues federation unpublish."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    mock = _patch_unpublish(monkeypatch)
    headers = auth_headers(regular_user)

    response = client.delete(f"/api/v1/tracks/{track.id}", headers=headers)
    assert response.status_code == 204

    mock.assert_called_once()
    call_track, _, call_owner, call_config, call_object_id = mock.call_args[0]
    assert str(call_track.id) == str(track.id)
    assert str(call_owner.id) == str(regular_user.id)
    assert call_config is client.app.state.config
    assert call_object_id is None


def test_delete_private_track_does_not_enqueue_unpublish(
    client, sample_tracks, regular_user, auth_headers, monkeypatch
):
    """Deleting a private track does not enqueue federation unpublish."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    mock = _patch_unpublish(monkeypatch)
    headers = auth_headers(regular_user)

    response = client.delete(f"/api/v1/tracks/{track.id}", headers=headers)
    assert response.status_code == 204
    assert mock.call_count == 0


def test_admin_delete_public_track_enqueues_unpublish(client, sample_tracks, admin_user, auth_headers, monkeypatch):
    """An admin deleting someone else's public track enqueues unpublish as the owner."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    mock = MagicMock(return_value=0)
    monkeypatch.setattr("songhive.api.routes.admin.unpublish_track_activity", mock)

    response = client.delete(f"/api/v1/admin/tracks/{track.id}", headers=auth_headers(admin_user))
    assert response.status_code == 204

    mock.assert_called_once()
    call_track, _, call_owner, call_config, call_object_id = mock.call_args[0]
    assert str(call_track.id) == str(track.id)
    assert str(call_owner.id) == str(track.owner_id)
    assert call_config is client.app.state.config
    assert call_object_id is None


def test_public_private_public_uses_fresh_object_id(client, sample_tracks, regular_user, auth_headers, monkeypatch):
    """A track re-published after an unpublish gets a brand-new ActivityPub object id."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    publish_mock = MagicMock(return_value=0)
    unpublish_mock = MagicMock(return_value=0)
    monkeypatch.setattr("songhive.api.routes.tracks.publish_track_activity", publish_mock)
    monkeypatch.setattr("songhive.api.routes.tracks.unpublish_track_activity", unpublish_mock)
    headers = auth_headers(regular_user)

    client.patch(f"/api/v1/tracks/{track.id}", json={"visibility": "public"}, headers=headers)
    client.patch(f"/api/v1/tracks/{track.id}", json={"visibility": "private"}, headers=headers)
    client.patch(f"/api/v1/tracks/{track.id}", json={"visibility": "public"}, headers=headers)

    assert publish_mock.call_count == 2
    assert unpublish_mock.call_count == 1

    first_object_id = publish_mock.call_args_list[0][0][4]
    unpublish_object_id = unpublish_mock.call_args[0][4]
    second_object_id = publish_mock.call_args_list[1][0][4]

    assert first_object_id == unpublish_object_id
    assert second_object_id
    assert second_object_id != first_object_id
    uuid.UUID(first_object_id)
    uuid.UUID(second_object_id)


def _patch_track_enrich(monkeypatch):
    """Replace the enrichment helper with a no-op success."""
    monkeypatch.setattr(
        "songhive.api.routes.tracks._enqueue_track_enrichment",
        lambda track_id, force=True: True,
    )


def test_enrich_track_allows_owner(client, sample_tracks, regular_user, auth_headers, monkeypatch):
    """Owners can request MusicBrainz enrichment for their track."""
    _patch_track_enrich(monkeypatch)
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    response = client.post(
        f"/api/v1/tracks/{track.id}/enrich",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["track_id"] == str(track.id)
    assert data["enqueued"] is True


def test_enrich_track_allows_admin(client, sample_tracks, admin_user, auth_headers, monkeypatch):
    """Admins can request MusicBrainz enrichment for any track."""
    _patch_track_enrich(monkeypatch)
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    response = client.post(
        f"/api/v1/tracks/{track.id}/enrich",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 200


def test_enrich_track_denied_for_other_user(client, sample_tracks, other_user, auth_headers, monkeypatch):
    """Non-owners cannot request enrichment for someone else's track."""
    _patch_track_enrich(monkeypatch)
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    response = client.post(
        f"/api/v1/tracks/{track.id}/enrich",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_enrich_track_requires_auth(client, sample_tracks, monkeypatch):
    """Anonymous users cannot request enrichment."""
    _patch_track_enrich(monkeypatch)
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)

    response = client.post(f"/api/v1/tracks/{track.id}/enrich")
    assert response.status_code == 401


def test_enrich_missing_track_returns_404(client, auth_headers, regular_user, monkeypatch):
    """Requesting enrichment for a missing track returns 404."""
    _patch_track_enrich(monkeypatch)
    response = client.post(
        "/api/v1/tracks/00000000-0000-0000-0000-000000000000/enrich",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_enrich_track_logs_audit_entry(
    client, db_session, sample_tracks, regular_user, auth_headers, monkeypatch
):
    """Enriching a track creates an audit log entry."""
    from sqlalchemy import select

    from songhive.models.audit_log import AuditLog

    _patch_track_enrich(monkeypatch)
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    response = client.post(
        f"/api/v1/tracks/{track.id}/enrich",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200

    result = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "track.enrich",
            AuditLog.target_id == str(track.id),
        )
    )
    assert result is not None


@pytest.mark.asyncio
async def test_list_tracks_around_track_id(client, db_session, regular_user, auth_headers):
    """The around_track_id parameter returns a chunk centered on the target track."""
    artist = Artist(name="Around Artist")
    db_session.add(artist)
    await db_session.flush()

    tracks = []
    for i in range(25):
        track = Track(
            title=f"Track {i}",
            artist_id=artist.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
        )
        db_session.add(track)
        tracks.append(track)
    await db_session.flush()

    target = tracks[15]
    headers = auth_headers(regular_user)
    response = client.get(
        "/api/v1/tracks",
        params={
            "around_track_id": target.id,
            "limit": 10,
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 10
    assert int(response.headers["X-List-Offset"]) == 10
    assert data[5]["id"] == target.id


def test_update_track_metadata(client, sample_tracks, regular_user, auth_headers):
    """Owners can update track title, release year, and track number."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"title": "Updated Track", "track_number": 5, "release_year": 1999},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Updated Track"
    assert body["track_number"] == 5
    assert body["release_year"] == 1999


@pytest.mark.asyncio
async def test_track_release_year_inherits_album(client, db_session, regular_user, auth_headers):
    """Tracks inherit an album's release year when no override is set."""
    artist = Artist(name="Album Artist")
    db_session.add(artist)
    await db_session.flush()
    album = Album(title="Test Album", artist_id=artist.id, owner_id=str(regular_user.id), release_year=2005)
    db_session.add(album)
    await db_session.flush()
    track = Track(
        title="Inherit Year",
        artist_id=artist.id,
        album_id=album.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    response = client.get(f"/api/v1/tracks/{track.id}?include=album", headers=auth_headers(regular_user))
    assert response.status_code == 200
    body = response.json()
    assert body["release_year"] == 2005

    patch = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"release_year": 2010},
        headers=auth_headers(regular_user),
    )
    assert patch.status_code == 200
    assert patch.json()["release_year"] == 2010


def test_upload_and_delete_track_image(client, sample_tracks, regular_user, auth_headers):
    """Owners can upload and remove a track image."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    headers = auth_headers(regular_user)

    image = client.post(
        f"/api/v1/tracks/{track.id}/image",
        files={"file": ("image.jpg", io.BytesIO(b"fake image"), "image/jpeg")},
        headers=headers,
    )
    assert image.status_code == 200
    assert image.json()["image_url"] is not None

    delete = client.delete(f"/api/v1/tracks/{track.id}/image", headers=headers)
    assert delete.status_code == 200
    assert delete.json()["image_url"] is None
