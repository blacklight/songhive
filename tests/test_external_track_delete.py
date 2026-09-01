"""Tests for external track deletion, restore, and admin bulk deletion."""

from typing import Any

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.external._fake import FakeExternalAdapter
from songhive.external.errors import ExternalItemNotFound, ExternalPermissionDenied
from songhive.models.audit_log import AuditLog
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.library_track import LibraryTrack
from songhive.models.track import Track
from songhive.models.user import User


def _sync_delay(*args: Any, **kwargs: Any):
    """Return a stand-in Celery task result."""
    return type("Result", (), {"id": "task-123"})()


def _create_payload(overrides: dict | None = None) -> dict:
    """Return a default create payload for the fake adapter."""
    payload = {
        "provider_type": "fake",
        "library_name": "My External Library",
        "config": {
            "items": {
                "song1.mp3": {
                    "data": [0, 1, 2, 3, 4, 5, 6, 7],
                    "metadata": {
                        "title": "Song 1",
                        "artist": "Artist",
                    },
                },
                "song2.mp3": {
                    "data": [7, 6, 5, 4, 3, 2, 1, 0],
                    "metadata": {
                        "title": "Song 2",
                        "artist": "Artist",
                    },
                },
            },
            "secret_key": "super-secret",
            "password": "hunter2",
            "token": "abc123",
        },
    }
    if overrides:
        payload.update(overrides)
    return payload


async def _create_user_external_library(
    client,
    regular_user: User,
    auth_headers,
    monkeypatch,
    overrides: dict | None = None,
) -> dict:
    """Create a user-scoped external library through the API."""
    client.app.state.config.external_libraries.allow_user_created_libraries = True
    client.app.state.config.external_libraries.allowed_user_providers = ["fake"]
    monkeypatch.setattr(
        "songhive.api.routes.external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/external-libraries",
        json=_create_payload(overrides),
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_201_CREATED
    return response.json()


async def _create_admin_external_library(
    client,
    admin_user: User,
    auth_headers,
    monkeypatch,
    overrides: dict | None = None,
) -> dict:
    """Create an admin-scoped external library through the API."""
    client.app.state.config.external_libraries.allow_admin_library_index_inclusion = True
    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/admin/external-libraries",
        json=_create_payload(overrides),
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_201_CREATED
    return response.json()


async def _make_track_for_external(
    db_session,
    external_library: ExternalLibrary,
    user: User,
    state: str = "active",
    provider_key: str = "song1.mp3",
) -> ExternalTrack:
    """Create a Songhive track and external track pair."""
    from songhive.models.artist import Artist

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Test Track",
        artist_id=artist.id,
        owner_id=user.id,
        visibility="private",
    )
    db_session.add(track)
    await db_session.flush()

    library_track = LibraryTrack(
        library_id=external_library.library_id,
        track_id=track.id,
        added_by_id=user.id,
    )
    db_session.add(library_track)

    external_track = ExternalTrack(
        external_library_id=str(external_library.id),
        track_id=str(track.id),
        provider_key=provider_key,
        state=state,
        sha256="abc123",
        raw_metadata={"display_path": provider_key},
    )
    db_session.add(external_track)
    await db_session.commit()
    return external_track


async def _make_two_tracks_for_external(
    db_session,
    external_library: ExternalLibrary,
    user: User,
) -> tuple[ExternalTrack, ExternalTrack]:
    """Create two Songhive tracks and external track pairs."""
    track1 = await _make_track_for_external(db_session, external_library, user, provider_key="song1.mp3")
    track2 = await _make_track_for_external(db_session, external_library, user, provider_key="song2.mp3")
    return track1, track2


@pytest.mark.asyncio
async def test_default_delete_tombstones_and_removes_library_track(
    client,
    regular_user,
    auth_headers,
    monkeypatch,
    db_session,
):
    """Default DELETE tombstones the external track and removes the LibraryTrack."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)

    response = client.delete(
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    await db_session.refresh(external_track)
    assert external_track.state == "tombstoned"

    result = await db_session.execute(
        select(LibraryTrack).where(
            LibraryTrack.library_id == external_library.library_id,
            LibraryTrack.track_id == external_track.track_id,
        )
    )
    assert result.scalar_one_or_none() is None

    track = await db_session.get(Track, external_track.track_id)
    assert track is not None

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.target_id == str(external_track.id),
            AuditLog.action == "external_track.tombstone",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.details["delete_source"] is False


@pytest.mark.asyncio
async def test_destructive_delete_requires_confirm(client, regular_user, auth_headers, monkeypatch, db_session):
    """Destructive DELETE without confirm="DELETE" returns 422."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = True
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)

    response = client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    await db_session.refresh(external_track)
    assert external_track.state == "active"


@pytest.mark.asyncio
async def test_destructive_delete_forbidden_when_disabled(client, regular_user, auth_headers, monkeypatch, db_session):
    """Destructive DELETE with allow_destructive_delete=false returns 403."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = False
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)

    response = client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True, "confirm": "DELETE"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_destructive_delete_unsupported_adapter(client, regular_user, auth_headers, monkeypatch, db_session):
    """Destructive DELETE on an adapter without delete_source returns 422."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = True
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)

    external_library.capabilities = {"delete_source": False}
    await db_session.commit()

    response = client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True, "confirm": "DELETE"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_destructive_delete_success(client, regular_user, auth_headers, monkeypatch, db_session):
    """Successful destructive DELETE calls the adapter and marks the track missing."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = True
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)

    delete_calls = []
    original_delete_source = FakeExternalAdapter.delete_source

    async def _spy_delete_source(self, config, item):
        delete_calls.append(item.provider_key)
        return await original_delete_source(self, config, item)

    monkeypatch.setattr(FakeExternalAdapter, "delete_source", _spy_delete_source)

    response = client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True, "confirm": "DELETE"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert delete_calls == ["song1.mp3"]

    await db_session.refresh(external_track)
    assert external_track.state == "missing"

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.target_id == str(external_track.id),
            AuditLog.action == "external_track.delete_source",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.details["success"] is True
    assert log.details["delete_source"] is True
    assert "mutation" in log.details


@pytest.mark.asyncio
async def test_destructive_delete_removes_songhive_track(client, regular_user, auth_headers, monkeypatch, db_session):
    """Destructive DELETE with remove_songhive_track removes the Songhive track."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = True
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)
    track_id = str(external_track.track_id)

    response = client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True, "confirm": "DELETE", "remove_songhive_track": True},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    external_track_gone = await db_session.get(ExternalTrack, external_track.id)
    assert external_track_gone is None

    track = await db_session.get(Track, track_id)
    assert track is None

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.target_id == str(external_track.id),
            AuditLog.action == "external_track.delete_source",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.details["remove_songhive_track"] is True


@pytest.mark.asyncio
async def test_destructive_delete_idempotent(client, regular_user, auth_headers, monkeypatch, db_session):
    """A retry of a destructive DELETE must not re-delete the provider item."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = True
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)

    response = client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True, "confirm": "DELETE"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    await db_session.refresh(external_track)
    assert external_track.state == "missing"

    async def _fail_if_called(self, config, item):
        raise AssertionError("delete_source must not be called when the track is already missing")

    monkeypatch.setattr(FakeExternalAdapter, "delete_source", _fail_if_called)

    response = client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True, "confirm": "DELETE"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.asyncio
async def test_destructive_delete_not_found_is_success(client, regular_user, auth_headers, monkeypatch, db_session):
    """Destructive DELETE treats an already-missing provider item as success."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = True
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)

    async def _raise_not_found(self, config, item):
        raise ExternalItemNotFound(f"Item not found: {item.provider_key}")

    monkeypatch.setattr(FakeExternalAdapter, "delete_source", _raise_not_found)

    response = client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True, "confirm": "DELETE"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    await db_session.refresh(external_track)
    assert external_track.state == "missing"


@pytest.mark.asyncio
async def test_destructive_delete_adapter_failure(client, regular_user, auth_headers, monkeypatch, db_session):
    """Destructive DELETE failure returns 502, leaves provider data, and sets sync_error."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = True
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)

    async def _raise_permission_denied(self, config, item):
        raise ExternalPermissionDenied("permission denied")

    monkeypatch.setattr(FakeExternalAdapter, "delete_source", _raise_permission_denied)

    response = client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True, "confirm": "DELETE"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_502_BAD_GATEWAY

    await db_session.refresh(external_track)
    assert external_track.state == "active"
    assert external_track.sync_error is not None

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.target_id == str(external_track.id),
            AuditLog.action == "external_track.delete_source",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.details["success"] is False
    assert "error" in log.details


@pytest.mark.asyncio
async def test_restore_tombstoned_track(client, regular_user, auth_headers, monkeypatch, db_session):
    """Restore flips a tombstoned track to active and recreates the LibraryTrack."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user, state="tombstoned")

    response = client.post(
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}/restore",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["state"] == "active"

    await db_session.refresh(external_track)
    assert external_track.state == "active"
    assert external_track.sync_error is None

    result = await db_session.execute(
        select(LibraryTrack).where(
            LibraryTrack.library_id == external_library.library_id,
            LibraryTrack.track_id == external_track.track_id,
        )
    )
    assert result.scalar_one_or_none() is not None

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.target_id == str(external_track.id),
            AuditLog.action == "external_track.restore",
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_restore_missing_provider_item(client, regular_user, auth_headers, monkeypatch, db_session):
    """Restoring a tombstoned track whose provider item is missing returns 404."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(
        db_session, external_library, regular_user, state="tombstoned", provider_key="missing.mp3"
    )

    response = client.post(
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}/restore",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND

    await db_session.refresh(external_track)
    assert external_track.state == "tombstoned"


@pytest.mark.asyncio
async def test_admin_bulk_tombstone(client, admin_user, auth_headers, monkeypatch, db_session):
    """Admin bulk tombstone removes LibraryTracks and produces a single audit entry."""
    data = await _create_admin_external_library(client, admin_user, auth_headers, monkeypatch)
    external_library = await db_session.get(ExternalLibrary, data["id"])
    track1, track2 = await _make_two_tracks_for_external(db_session, external_library, admin_user)

    response = client.post(
        f"/api/v1/admin/external-libraries/{data['id']}/tracks/bulk-delete",
        json={
            "external_track_ids": [str(track1.id), str(track2.id)],
            "delete_source": False,
        },
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    await db_session.refresh(track1)
    await db_session.refresh(track2)
    assert track1.state == "tombstoned"
    assert track2.state == "tombstoned"

    for track in (track1, track2):
        result = await db_session.execute(
            select(LibraryTrack).where(
                LibraryTrack.library_id == external_library.library_id,
                LibraryTrack.track_id == track.track_id,
            )
        )
        assert result.scalar_one_or_none() is None

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "external_track.bulk_tombstone",
            AuditLog.target_id == data["id"],
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert str(track1.id) in log.details["external_track_ids"]
    assert str(track2.id) in log.details["external_track_ids"]
    assert log.details["delete_source"] is False


@pytest.mark.asyncio
async def test_admin_bulk_delete_source(client, admin_user, auth_headers, monkeypatch, db_session):
    """Admin bulk destructive delete removes provider items and produces a single audit entry."""
    data = await _create_admin_external_library(client, admin_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = True
    external_library = await db_session.get(ExternalLibrary, data["id"])
    track1, track2 = await _make_two_tracks_for_external(db_session, external_library, admin_user)

    delete_calls = []
    original_delete_source = FakeExternalAdapter.delete_source

    async def _spy_delete_source(self, config, item):
        delete_calls.append(item.provider_key)
        return await original_delete_source(self, config, item)

    monkeypatch.setattr(FakeExternalAdapter, "delete_source", _spy_delete_source)

    response = client.post(
        f"/api/v1/admin/external-libraries/{data['id']}/tracks/bulk-delete",
        json={
            "external_track_ids": [str(track1.id), str(track2.id)],
            "delete_source": True,
            "confirm": "DELETE",
        },
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert set(delete_calls) == {"song1.mp3", "song2.mp3"}

    await db_session.refresh(track1)
    await db_session.refresh(track2)
    assert track1.state == "missing"
    assert track2.state == "missing"

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "external_track.bulk_delete_source",
            AuditLog.target_id == data["id"],
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert str(track1.id) in log.details["external_track_ids"]
    assert str(track2.id) in log.details["external_track_ids"]
    assert log.details["delete_source"] is True


@pytest.mark.asyncio
async def test_audit_details_never_contain_raw_config(client, regular_user, auth_headers, monkeypatch, db_session):
    """Audit details for delete and restore never contain raw provider secrets."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    client.app.state.config.external_libraries.allow_destructive_delete = True
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user)

    client.request(
        "DELETE",
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}",
        json={"delete_source": True, "confirm": "DELETE"},
        headers=auth_headers(regular_user),
    )

    result = await db_session.execute(select(AuditLog).where(AuditLog.target_id == str(external_track.id)))
    logs = result.scalars().all()
    assert logs
    for log in logs:
        assert "super-secret" not in str(log.details)
        assert "hunter2" not in str(log.details)
        assert "abc123" not in str(log.details)
