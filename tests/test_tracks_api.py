"""
Tests for the track API endpoints.
"""

import hashlib
import io
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.stored_file import StoredFile
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


def test_list_tracks_filters_by_hashtag(client, sample_tracks, regular_user, auth_headers):
    """List endpoints can filter tracks by hashtag."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    headers = auth_headers(regular_user)
    client.post(
        f"/api/v1/tracks/{track.id}/hashtags",
        json={"hashtags": ["rock"]},
        headers=headers,
    )

    response = client.get("/api/v1/tracks", params={"hashtag": "rock"}, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == track.id
    assert int(response.headers["X-Total-Count"]) == 1


def test_list_tracks_filters_by_genre(client, sample_tracks, regular_user, auth_headers):
    """List endpoints can filter tracks by genre, using normalised associations."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)
    headers = auth_headers(regular_user)

    # Store a raw, mixed-case genre string. The endpoint should still be
    # findable by the normalised genre name because filtering uses the
    # GenreTrack association table.
    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"genre": "Rock, Pop"},
        headers=headers,
    )
    assert response.status_code == 200

    response = client.get("/api/v1/tracks", params={"genre": "rock"}, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == track.id
    assert int(response.headers["X-Total-Count"]) == 1

    response = client.get("/api/v1/tracks", params={"genre": "pop"}, headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1


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


@pytest.mark.asyncio
async def test_update_track_enqueues_tag_sync(
    client, sample_tracks, regular_user, db_session, auth_headers, monkeypatch
):
    """Updating tag-relevant track metadata enqueues a sync for that track."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    stored = StoredFile(
        storage_path=f"/tmp/{track.id}.mp3",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1234,
        sha256=track.id.replace("-", ""),
        owner_id=regular_user.id,
    )
    db_session.add(stored)
    await db_session.flush()
    track.audio_file_id = str(stored.id)
    await db_session.flush()

    headers = auth_headers(regular_user)
    sync_mock = MagicMock()
    monkeypatch.setattr("songhive.api.routes.tracks._enqueue_track_tag_sync", sync_mock)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"title": "Updated Title"},
        headers=headers,
    )
    assert response.status_code == 200
    sync_mock.assert_called_once_with(str(track.id))


def test_update_track_visibility_does_not_enqueue_tag_sync(
    client, sample_tracks, regular_user, auth_headers, monkeypatch
):
    """Updating only visibility does not enqueue a tag sync."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    headers = auth_headers(regular_user)

    sync_mock = MagicMock()
    monkeypatch.setattr("songhive.api.routes.tracks._enqueue_track_tag_sync", sync_mock)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"visibility": "public"},
        headers=headers,
    )
    assert response.status_code == 200
    sync_mock.assert_not_called()


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
    assert int(response.headers["X-List-Offset"]) == 4
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


@pytest.mark.asyncio
async def test_list_tracks_for_album_sorted_by_disc_and_track_number(client, db_session, regular_user, auth_headers):
    """Tracks filtered by album are always returned in disc/track order."""
    artist = Artist(name="Sort Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Sort Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(album)
    await db_session.flush()

    tracks = [
        Track(
            title="Track 2",
            artist_id=artist.id,
            album_id=album.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            disc_number=1,
            track_number=2,
        ),
        Track(
            title="Track 1",
            artist_id=artist.id,
            album_id=album.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            disc_number=1,
            track_number=1,
        ),
        Track(
            title="Disc 2 Track 1",
            artist_id=artist.id,
            album_id=album.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            disc_number=2,
            track_number=1,
        ),
    ]
    for track in tracks:
        db_session.add(track)
    await db_session.commit()

    response = client.get(
        f"/api/v1/tracks?album_id={album.id}&sort_by=title&sort_dir=desc",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert [track["title"] for track in data] == [
        "Track 1",
        "Track 2",
        "Disc 2 Track 1",
    ]


@pytest.mark.asyncio
async def test_list_tracks_sorted_by_release_year(client, db_session, regular_user, auth_headers):
    """Sorting tracks by release year falls back to the album's release year."""
    artist = Artist(name="Release Year Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Release Year Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
        release_year=1999,
    )
    db_session.add(album)
    await db_session.flush()

    tracks = [
        Track(
            title="Album Track",
            artist_id=artist.id,
            album_id=album.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            release_year=None,
        ),
        Track(
            title="Explicit 2020",
            artist_id=artist.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            release_year=2020,
        ),
        Track(
            title="Older Album",
            artist_id=artist.id,
            album_id=album.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            release_year=1985,
        ),
    ]
    for track in tracks:
        db_session.add(track)
    await db_session.commit()

    response = client.get(
        "/api/v1/tracks?sort_by=release_year&sort_dir=asc",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert [track["title"] for track in data] == [
        "Older Album",
        "Album Track",
        "Explicit 2020",
    ]


@pytest.fixture
async def sortable_tracks(db_session, regular_user):
    """Create artists, albums and tracks that exercise every track sort key."""
    artists = [
        Artist(name="Artist A"),
        Artist(name="Artist B"),
        Artist(name="Artist C"),
    ]
    for artist in artists:
        db_session.add(artist)
    await db_session.flush()

    albums = [
        Album(
            title="Alpha Album",
            artist_id=artists[0].id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            release_year=1999,
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        ),
        Album(
            title="Beta Album",
            artist_id=artists[2].id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            release_year=2020,
            created_at=datetime(2020, 1, 2, tzinfo=timezone.utc),
            updated_at=datetime(2020, 1, 2, tzinfo=timezone.utc),
        ),
    ]
    for album in albums:
        db_session.add(album)
    await db_session.flush()

    tracks = [
        Track(
            title="Alpha",
            artist_id=artists[0].id,
            album_id=albums[0].id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            release_year=None,
            created_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        ),
        Track(
            title="Beta",
            artist_id=artists[2].id,
            album_id=albums[1].id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            release_year=2020,
            created_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
        ),
        Track(
            title="Gamma",
            artist_id=artists[1].id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            release_year=None,
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    for track in tracks:
        db_session.add(track)
    await db_session.commit()
    return tracks


@pytest.mark.parametrize(
    "sort_by,sort_dir,expected",
    [
        ("title", "asc", ["Alpha", "Beta", "Gamma"]),
        ("title", "desc", ["Gamma", "Beta", "Alpha"]),
        ("artist_name", "asc", ["Alpha", "Gamma", "Beta"]),
        ("artist_name", "desc", ["Beta", "Gamma", "Alpha"]),
        ("album_title", "asc", ["Alpha", "Beta", "Gamma"]),
        ("album_title", "desc", ["Beta", "Alpha", "Gamma"]),
        ("release_year", "asc", ["Alpha", "Beta", "Gamma"]),
        ("release_year", "desc", ["Beta", "Alpha", "Gamma"]),
        ("created_at", "asc", ["Alpha", "Beta", "Gamma"]),
        ("created_at", "desc", ["Gamma", "Beta", "Alpha"]),
        ("updated_at", "asc", ["Gamma", "Beta", "Alpha"]),
        ("updated_at", "desc", ["Alpha", "Beta", "Gamma"]),
    ],
)
@pytest.mark.asyncio
async def test_list_tracks_sorts(
    client,
    regular_user,
    auth_headers,
    sortable_tracks,
    sort_by,
    sort_dir,
    expected,
):
    """The track list endpoint honours every supported sort key and direction."""
    response = client.get(
        f"/api/v1/tracks?sort_by={sort_by}&sort_dir={sort_dir}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert [track["title"] for track in data] == expected


@pytest.mark.asyncio
async def test_update_track_creates_artist_and_album(
    client,
    db_session,
    regular_user,
    auth_headers,
):
    """Updating a track with unknown artist/album names creates them."""
    artist = Artist(name="Original Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Original Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.flush()

    response = client.patch(
        f"/api/v1/tracks/{track.id}?include=artist,album",
        json={
            "title": "Renamed Track",
            "artist_name": "New Artist",
            "album_title": "New Album",
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Renamed Track"
    assert data["artist"]["name"] == "New Artist"
    assert data["album"]["title"] == "New Album"

    new_artist = await db_session.get(Artist, data["artist_id"])
    new_album = await db_session.get(Album, data["album_id"])
    assert new_artist is not None
    assert new_artist.name == "New Artist"
    assert new_album is not None
    assert new_album.title == "New Album"
    assert str(new_album.artist_id) == str(new_artist.id)

    db_session.expunge(track)
    reloaded = await db_session.get(Track, track.id)
    assert str(reloaded.artist_id) == data["artist_id"]
    assert str(reloaded.album_id) == data["album_id"]


@pytest.mark.asyncio
async def test_update_track_reuses_existing_artist_and_album(
    client,
    db_session,
    regular_user,
    auth_headers,
):
    """Updating a track reuses artist/album records that already exist."""
    artist = Artist(name="Existing Artist")
    db_session.add(artist)
    await db_session.flush()
    album = Album(
        title="Existing Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(album)
    await db_session.flush()

    other_artist = Artist(name="Other Artist")
    db_session.add(other_artist)
    await db_session.flush()
    track = Track(
        title="Some Track",
        artist_id=other_artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={
            "artist_name": "Existing Artist",
            "album_title": "Existing Album",
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["artist_id"] == str(artist.id)
    assert data["album_id"] == str(album.id)


@pytest.mark.asyncio
async def test_update_track_removes_album(
    client,
    db_session,
    regular_user,
    auth_headers,
):
    """Sending an empty album title removes the track from its album."""
    artist = Artist(name="Solo Artist")
    db_session.add(artist)
    await db_session.flush()
    album = Album(
        title="Solo Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(album)
    await db_session.flush()
    track = Track(
        title="Solo Track",
        artist_id=artist.id,
        album_id=album.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"album_title": ""},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["album_id"] is None


def test_update_track_ignores_empty_artist_name(
    client,
    sample_tracks,
    regular_user,
    auth_headers,
):
    """Updating a track with a blank artist name leaves the artist unchanged."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)
    original_artist_id = str(track.artist_id)
    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"artist_name": "   "},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert response.json()["artist_id"] == original_artist_id


@pytest.mark.asyncio
async def test_delete_track_cleans_up_empty_artist_and_album(
    client,
    db_session,
    regular_user,
    auth_headers,
):
    """Deleting a track removes its artist and album when no tracks remain."""
    artist = Artist(name="Lone Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Lone Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(album)
    await db_session.flush()

    track = Track(
        title="Lone Track",
        artist_id=artist.id,
        album_id=album.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.flush()

    artist_id = str(artist.id)
    album_id = str(album.id)

    response = client.delete(f"/api/v1/tracks/{track.id}", headers=auth_headers(regular_user))
    assert response.status_code == 204

    artist_result = await db_session.execute(select(Artist).where(Artist.id == artist_id))
    assert artist_result.scalar_one_or_none() is None

    album_result = await db_session.execute(select(Album).where(Album.id == album_id))
    assert album_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_track_keeps_artist_and_album_with_other_tracks(
    client,
    db_session,
    regular_user,
    auth_headers,
):
    """Deleting one track of several does not remove the shared artist/album."""
    artist = Artist(name="Shared Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Shared Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(album)
    await db_session.flush()

    tracks = []
    for title in ("Track One", "Track Two"):
        track = Track(
            title=title,
            artist_id=artist.id,
            album_id=album.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PRIVATE.value,
        )
        db_session.add(track)
        tracks.append(track)
    await db_session.flush()

    artist_id = str(artist.id)
    album_id = str(album.id)

    response = client.delete(f"/api/v1/tracks/{tracks[0].id}", headers=auth_headers(regular_user))
    assert response.status_code == 204

    artist_result = await db_session.execute(select(Artist).where(Artist.id == artist_id))
    assert artist_result.scalar_one_or_none() is not None

    album_result = await db_session.execute(select(Album).where(Album.id == album_id))
    assert album_result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_update_track_cleans_up_empty_old_artist_and_album(
    client,
    db_session,
    regular_user,
    auth_headers,
):
    """Changing a track's artist and album removes the now-empty old ones."""
    old_artist = Artist(name="Old Artist")
    db_session.add(old_artist)
    await db_session.flush()

    old_album = Album(
        title="Old Album",
        artist_id=old_artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(old_album)
    await db_session.flush()

    track = Track(
        title="Moving Track",
        artist_id=old_artist.id,
        album_id=old_album.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.flush()

    old_artist_id = str(old_artist.id)
    old_album_id = str(old_album.id)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={
            "artist_name": "New Artist",
            "album_title": "New Album",
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200

    old_artist_result = await db_session.execute(select(Artist).where(Artist.id == old_artist_id))
    assert old_artist_result.scalar_one_or_none() is None

    old_album_result = await db_session.execute(select(Album).where(Album.id == old_album_id))
    assert old_album_result.scalar_one_or_none() is None

    new_artist_result = await db_session.execute(select(Artist).where(Artist.name == "New Artist"))
    new_artist = new_artist_result.scalar_one_or_none()
    assert new_artist is not None

    new_album_result = await db_session.execute(select(Album).where(Album.title == "New Album"))
    new_album = new_album_result.scalar_one_or_none()
    assert new_album is not None
    assert str(new_album.artist_id) == str(new_artist.id)


@pytest.mark.asyncio
async def test_update_track_keeps_old_artist_with_other_tracks(
    client,
    db_session,
    regular_user,
    auth_headers,
):
    """Changing the artist of one track does not delete an artist used elsewhere."""
    old_artist = Artist(name="Still Used Artist")
    db_session.add(old_artist)
    await db_session.flush()

    for title in ("Track One", "Track Two"):
        track = Track(
            title=title,
            artist_id=old_artist.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PRIVATE.value,
        )
        db_session.add(track)
    await db_session.flush()

    track = await db_session.execute(select(Track).where(Track.title == "Track One"))
    track = track.scalar_one()
    old_artist_id = str(old_artist.id)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"artist_name": "New Artist"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200

    old_artist_result = await db_session.execute(select(Artist).where(Artist.id == old_artist_id))
    assert old_artist_result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_update_track_removes_old_album_when_cleared(
    client,
    db_session,
    regular_user,
    auth_headers,
):
    """Clearing a track's album removes the old empty album but keeps the artist."""
    artist = Artist(name="Album-Less Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Emptying Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(album)
    await db_session.flush()

    track = Track(
        title="Unalbum Track",
        artist_id=artist.id,
        album_id=album.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.flush()

    album_id = str(album.id)
    artist_id = str(artist.id)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"album_title": ""},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200

    album_result = await db_session.execute(select(Album).where(Album.id == album_id))
    assert album_result.scalar_one_or_none() is None

    artist_result = await db_session.execute(select(Artist).where(Artist.id == artist_id))
    assert artist_result.scalar_one_or_none() is not None


async def _make_external_track_for_user(db_session, user, provider_key="song1.mp3"):
    """Create a track backed by a fake external library."""
    artist = Artist(name="External Artist")
    db_session.add(artist)
    await db_session.flush()

    library = Library(name="External Library", owner_id=str(user.id), visibility="private")
    db_session.add(library)
    await db_session.flush()

    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type="fake",
        config={
            "items": {
                provider_key: {
                    "data": "fake audio data",
                    "metadata": {"title": "Song", "artist": "Artist"},
                }
            }
        },
        capabilities={
            "list_items": True,
            "read_bytes": True,
            "read_tags": True,
            "write_tags": True,
            "rename_source": True,
            "download": True,
        },
    )
    db_session.add(external_library)
    await db_session.flush()

    track = Track(
        title="External Track",
        artist_id=artist.id,
        owner_id=str(user.id),
        visibility="private",
        source="external",
    )
    db_session.add(track)
    await db_session.flush()

    db_session.add(LibraryTrack(library_id=str(library.id), track_id=str(track.id), added_by_id=str(user.id)))

    sha256_value = hashlib.sha256("fake audio data".encode()).hexdigest()
    external_track = ExternalTrack(
        external_library_id=str(external_library.id),
        track_id=str(track.id),
        provider_key=provider_key,
        state="active",
        sha256=sha256_value,
        raw_metadata={"display_path": provider_key},
    )
    db_session.add(external_track)
    await db_session.commit()
    return track, external_track, external_library


@pytest.mark.asyncio
async def test_update_track_filename_renames_stored_file(client, sample_tracks, regular_user, auth_headers, db_session):
    """PATCH with filename updates original_filename without rehashing or changing FKs."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    stored = StoredFile(
        storage_path=f"files/{track.id}/audio.mp3",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1234,
        sha256=track.id.replace("-", ""),
        original_filename="old_name.mp3",
        owner_id=str(regular_user.id),
    )
    db_session.add(stored)
    await db_session.flush()
    track.audio_file_id = str(stored.id)
    await db_session.flush()

    old_sha256 = stored.sha256
    old_storage_path = stored.storage_path
    old_audio_file_id = track.audio_file_id

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"filename": "new_name"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "new_name.mp3"

    await db_session.refresh(stored)
    assert stored.original_filename == "new_name.mp3"
    assert stored.sha256 == old_sha256
    assert stored.storage_path == old_storage_path
    assert track.audio_file_id == old_audio_file_id


@pytest.mark.asyncio
async def test_update_track_filename_as_admin(client, sample_tracks, admin_user, auth_headers, db_session):
    """Admins can rename a track's media file."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PUBLIC.value)

    stored = StoredFile(
        storage_path=f"files/{track.id}/audio.mp3",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1234,
        sha256=track.id.replace("-", ""),
        original_filename="old_name.mp3",
        owner_id=str(track.owner_id),
    )
    db_session.add(stored)
    await db_session.flush()
    track.audio_file_id = str(stored.id)
    await db_session.flush()

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"filename": "admin_name"},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "admin_name.mp3"


@pytest.mark.asyncio
async def test_update_track_filename_denied_for_other_user(client, sample_tracks, other_user, auth_headers, db_session):
    """Non-owners cannot rename a track's media file."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    stored = StoredFile(
        storage_path=f"files/{track.id}/audio.mp3",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1234,
        sha256=track.id.replace("-", ""),
        original_filename="old_name.mp3",
        owner_id=str(track.owner_id),
    )
    db_session.add(stored)
    await db_session.flush()
    track.audio_file_id = str(stored.id)
    await db_session.flush()

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"filename": "hacked"},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_track_filename_renames_external_source(client, regular_user, auth_headers, db_session):
    """PATCH with filename renames an external source file when the adapter supports it."""
    track, external_track, _ = await _make_external_track_for_user(db_session, regular_user, provider_key="song1.mp3")

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"filename": "renamed"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "renamed.mp3"
    assert data["can_rename_source"] is True

    expected_sha256 = hashlib.sha256("fake audio data".encode()).hexdigest()
    result = await db_session.execute(select(ExternalTrack).where(ExternalTrack.id == external_track.id))
    refreshed = result.scalar_one()
    assert refreshed.provider_key == "renamed.mp3"
    assert refreshed.raw_metadata.get("display_path") == "renamed.mp3"
    assert refreshed.sha256 == expected_sha256
    assert str(refreshed.track_id) == str(track.id)


@pytest.mark.asyncio
async def test_update_track_filename_rejected_when_no_media(client, sample_tracks, regular_user, auth_headers):
    """PATCH with filename fails for a track with no stored or external media."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"filename": "renamed"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_track_filename_rejected_when_external_rename_unsupported(
    client, regular_user, auth_headers, db_session
):
    """PATCH with filename fails when the external adapter does not support renaming."""
    track, external_track, external_library = await _make_external_track_for_user(
        db_session, regular_user, provider_key="song1.mp3"
    )
    external_library.capabilities = {
        **(external_library.capabilities or {}),
        "rename_source": False,
    }
    await db_session.commit()

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"filename": "renamed"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422
    data = response.json()
    assert "does not support renaming" in data["detail"]

    await db_session.refresh(external_track)
    assert external_track.provider_key == "song1.mp3"


@pytest.mark.asyncio
async def test_update_track_filename_sanitizes_path_traversal(
    client, sample_tracks, regular_user, auth_headers, db_session
):
    """PATCH with a path-traversal filename only updates the basename and leaves storage_path intact."""
    track = next(t for t in sample_tracks if t.visibility == Visibility.PRIVATE.value)

    stored = StoredFile(
        storage_path=f"files/{track.id}/audio.mp3",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1234,
        sha256=track.id.replace("-", ""),
        original_filename="old_name.mp3",
        owner_id=str(regular_user.id),
    )
    db_session.add(stored)
    await db_session.flush()
    track.audio_file_id = str(stored.id)
    await db_session.flush()

    old_storage_path = stored.storage_path

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"filename": "/etc/passwd/hacked"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "hacked.mp3"

    await db_session.refresh(stored)
    assert stored.original_filename == "hacked.mp3"
    assert stored.storage_path == old_storage_path
    assert stored.sha256 == track.id.replace("-", "")

    # A bare ".." is rejected outright.
    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"filename": ".."},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_track_filename_external_sanitizes_path_traversal(client, regular_user, auth_headers, db_session):
    """PATCH with a path-traversal filename renames an external source without changing its parent."""
    track, external_track, _ = await _make_external_track_for_user(
        db_session, regular_user, provider_key="music/song1.mp3"
    )

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"filename": "../hacked"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "hacked.mp3"

    result = await db_session.execute(select(ExternalTrack).where(ExternalTrack.id == external_track.id))
    refreshed = result.scalar_one()
    assert refreshed.provider_key == "music/hacked.mp3"
    assert refreshed.raw_metadata.get("display_path") == "music/hacked.mp3"
