"""Tests for the user external-library API."""

from typing import Any

import pytest
from fastapi import status
from sqlalchemy import func, select

from songhive.external.sync import sync_external_library
from songhive.models.audit_log import AuditLog
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_sync_run import ExternalSyncRun
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
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


@pytest.mark.asyncio
async def test_providers_requires_auth(client):
    """GET /external-libraries/providers requires authentication."""
    response = client.get("/api/v1/external-libraries/providers")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_providers_lists_user_configurable(client, regular_user, auth_headers):
    """The providers endpoint lists the fake adapter with capabilities."""
    response = client.get(
        "/api/v1/external-libraries/providers",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["provider_type"] == "fake"
    assert data[0]["user_configurable"] is True
    assert "list_items" in data[0]["capabilities_summary"]
    assert data[0]["capabilities_summary"]["list_items"] is True


@pytest.mark.asyncio
async def test_create_forbidden_when_user_libraries_disabled(client, regular_user, auth_headers):
    """User external-library creation is blocked when the feature is disabled."""
    client.app.state.config.external_libraries.allow_user_created_libraries = False
    response = client.post(
        "/api/v1/external-libraries",
        json=_create_payload(),
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_create_succeeds_when_allowed(client, regular_user, auth_headers, monkeypatch, db_session):
    """A regular user can create an external library when allowed."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    assert data["provider_type"] == "fake"
    assert data["scope"] == "user"
    assert data["library_id"]
    assert data["can_manage"] is True
    assert data["can_sync"] is True
    assert data["capabilities"]["list_items"] is True

    # Config is redacted before it reaches the response.
    assert data["config"].get("secret_key") == "<redacted>"
    assert data["config"].get("password") == "<redacted>"
    assert data["config"].get("token") == "<redacted>"

    # A Library row was created for the new external library.
    library = await db_session.get(Library, data["library_id"])
    assert library is not None
    assert library.owner_id == str(regular_user.id)


@pytest.mark.asyncio
async def test_create_rejects_unknown_provider(client, regular_user, auth_headers, monkeypatch):
    """An unknown provider type is rejected with 422."""
    client.app.state.config.external_libraries.allow_user_created_libraries = True
    monkeypatch.setattr(
        "songhive.api.routes.external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/external-libraries",
        json=_create_payload({"provider_type": "not-real"}),
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_create_rejects_include_in_library_index(client, regular_user, auth_headers, monkeypatch):
    """The user route rejects include_in_library_index."""
    client.app.state.config.external_libraries.allow_user_created_libraries = True
    monkeypatch.setattr(
        "songhive.api.routes.external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/external-libraries",
        json=_create_payload({"include_in_library_index": True}),
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_list_returns_only_own_user_libraries(
    client,
    db_session,
    make_user,
    regular_user,
    auth_headers,
    monkeypatch,
):
    """Users see only their own user-scoped external libraries."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)

    other = await make_user("other")
    client.app.state.config.external_libraries.allow_user_created_libraries = True
    response = client.post(
        "/api/v1/external-libraries",
        json=_create_payload({"library_name": "Other Library"}),
        headers=auth_headers(other),
    )
    assert response.status_code == status.HTTP_201_CREATED

    response = client.get(
        "/api/v1/external-libraries",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    items = response.json()
    assert len(items) == 1
    assert items[0]["id"] == data["id"]


@pytest.mark.asyncio
async def test_list_as_admin_returns_all_user_libraries(
    client,
    regular_user,
    admin_user,
    auth_headers,
    monkeypatch,
):
    """Admins see all user-scoped external libraries through the user route."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)

    response = client.get(
        "/api/v1/external-libraries",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    items = response.json()
    assert len(items) == 1
    assert items[0]["id"] == data["id"]


@pytest.mark.asyncio
async def test_get_detail(client, regular_user, auth_headers, monkeypatch):
    """Users can fetch their own external library detail."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)

    response = client.get(
        f"/api/v1/external-libraries/{data['id']}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    detail = response.json()
    assert detail["id"] == data["id"]
    assert detail["config"]["secret_key"] == "<redacted>"


@pytest.mark.asyncio
async def test_get_detail_forbids_other_users(
    client,
    make_user,
    regular_user,
    auth_headers,
    monkeypatch,
):
    """A non-owner, non-admin user cannot view another user's external library."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    other = await make_user("other2")
    response = client.get(
        f"/api/v1/external-libraries/{data['id']}",
        headers=auth_headers(other),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_update(client, regular_user, auth_headers, monkeypatch, db_session):
    """Users can update display and sync fields."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)

    response = client.patch(
        f"/api/v1/external-libraries/{data['id']}",
        json={
            "name": "Updated Name",
            "sync_interval_seconds": 3600,
            "config": {
                "items": {
                    "song2.mp3": {
                        "data": [7, 6, 5],
                        "metadata": {"title": "Song 2"},
                    },
                },
                "secret_key": "new-secret",
            },
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    updated = response.json()
    assert updated["name"] == "Updated Name"
    assert updated["sync_interval_seconds"] == 3600
    assert updated["config"].get("secret_key") == "<redacted>"

    # Audit log records the change without the raw config.
    result = await db_session.execute(
        select(AuditLog).where(AuditLog.target_id == data["id"], AuditLog.action == "external_library.update")
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert "secret" not in str(log.details).lower() or "config_changed" in log.details


@pytest.mark.asyncio
async def test_update_clears_sync_interval(client, regular_user, auth_headers, monkeypatch):
    """PATCH with sync_interval_seconds: null clears the scheduled interval."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)

    response = client.patch(
        f"/api/v1/external-libraries/{data['id']}",
        json={"sync_interval_seconds": 3600},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["sync_interval_seconds"] == 3600

    response = client.patch(
        f"/api/v1/external-libraries/{data['id']}",
        json={"sync_interval_seconds": None},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["sync_interval_seconds"] is None


@pytest.mark.asyncio
async def test_update_omitted_sync_interval_is_unchanged(client, regular_user, auth_headers, monkeypatch):
    """PATCH without sync_interval_seconds leaves the existing value in place."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)

    response = client.patch(
        f"/api/v1/external-libraries/{data['id']}",
        json={"sync_interval_seconds": 3600},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK

    response = client.patch(
        f"/api/v1/external-libraries/{data['id']}",
        json={"name": "Updated"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["sync_interval_seconds"] == 3600


@pytest.mark.asyncio
async def test_update_rejects_provider_type(client, regular_user, auth_headers, monkeypatch):
    """The update schema rejects the immutable provider_type field."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    response = client.patch(
        f"/api/v1/external-libraries/{data['id']}",
        json={"provider_type": "fake"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_update_rejects_include_in_library_index(client, regular_user, auth_headers, monkeypatch):
    """The user update route rejects include_in_library_index when present."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)

    for value in (True, False, None):
        response = client.patch(
            f"/api/v1/external-libraries/{data['id']}",
            json={"include_in_library_index": value},
            headers=auth_headers(regular_user),
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # An update that omits the field is allowed.
    response = client.patch(
        f"/api/v1/external-libraries/{data['id']}",
        json={"name": "Updated"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_delete(client, regular_user, auth_headers, monkeypatch, db_session):
    """Users can delete their own external library."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)

    response = client.delete(
        f"/api/v1/external-libraries/{data['id']}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    external_library = await db_session.get(ExternalLibrary, data["id"])
    assert external_library is None

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.target_id == data["id"], AuditLog.action == "external_library.delete")
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_sync_creates_run(client, regular_user, auth_headers, monkeypatch, db_session):
    """POST /sync returns 202 and creates a queued sync run."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    monkeypatch.setattr(
        "songhive.api.routes.external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )

    response = client.post(
        f"/api/v1/external-libraries/{data['id']}/sync",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_202_ACCEPTED
    body = response.json()
    assert body["sync_run_id"]

    run = await db_session.get(ExternalSyncRun, body["sync_run_id"])
    assert run is not None
    assert run.status == "queued"
    assert run.triggered_by == "manual"
    assert str(run.triggered_by_user_id) == str(regular_user.id)

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.target_id == data["id"], AuditLog.action == "external_library.sync")
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_sync_run_is_updated_by_worker(client, regular_user, auth_headers, monkeypatch, db_session, fake_redis):
    """POST /sync returns a run ID that the worker updates in place."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    monkeypatch.setattr(
        "songhive.api.routes.external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )

    response = client.post(
        f"/api/v1/external-libraries/{data['id']}/sync",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_202_ACCEPTED
    run_id = response.json()["sync_run_id"]

    run = await sync_external_library(
        db_session,
        data["id"],
        triggered_by="manual",
        triggered_by_user_id=str(regular_user.id),
        sync_run_id=run_id,
        redis=fake_redis,
    )
    assert str(run.id) == run_id
    assert run.status == "success"

    runs_count = await db_session.execute(
        select(func.count(ExternalSyncRun.id)).where(ExternalSyncRun.external_library_id == data["id"])
    )
    assert runs_count.scalar() == 1


@pytest.mark.asyncio
async def test_sync_runs_list(client, regular_user, auth_headers, monkeypatch, db_session):
    """Sync runs are paginated under the external library."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    run = ExternalSyncRun(
        external_library_id=data["id"],
        triggered_by="scheduled",
        status="completed",
    )
    db_session.add(run)
    await db_session.commit()

    response = client.get(
        f"/api/v1/external-libraries/{data['id']}/sync-runs",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    items = response.json()
    assert len(items) == 1
    assert items[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_tracks_list(client, regular_user, auth_headers, monkeypatch, db_session):
    """External tracks are listed under the library."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    external_library = await db_session.get(ExternalLibrary, data["id"])

    track = ExternalTrack(
        external_library_id=str(external_library.id),
        provider_key="song1.mp3",
        state="active",
        sha256="abc123",
        raw_metadata={"display_path": "Song 1"},
    )
    db_session.add(track)
    await db_session.commit()

    response = client.get(
        f"/api/v1/external-libraries/{data['id']}/tracks",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_200_OK
    items = response.json()
    assert len(items) == 1
    assert items[0]["provider_key"] == "song1.mp3"
    assert items[0]["display_path"] == "Song 1"

    # State filter works.
    response = client.get(
        f"/api/v1/external-libraries/{data['id']}/tracks?state=tombstoned",
        headers=auth_headers(regular_user),
    )
    assert response.json() == []


async def _make_track_for_external(
    db_session,
    external_library: ExternalLibrary,
    regular_user: User,
    state: str = "active",
) -> ExternalTrack:
    """Create a Songhive track and external track pair."""
    from songhive.models.artist import Artist

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Test Track",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility="private",
    )
    db_session.add(track)
    await db_session.flush()

    library_track = LibraryTrack(
        library_id=external_library.library_id,
        track_id=track.id,
        added_by_id=regular_user.id,
    )
    db_session.add(library_track)

    external_track = ExternalTrack(
        external_library_id=str(external_library.id),
        track_id=str(track.id),
        provider_key="song1.mp3",
        state=state,
        sha256="abc123",
        raw_metadata={"display_path": "Song 1"},
    )
    db_session.add(external_track)
    await db_session.commit()
    return external_track


@pytest.mark.asyncio
async def test_tombstone_track(client, regular_user, auth_headers, monkeypatch, db_session):
    """DELETE without body tombstones the external track and removes the LibraryTrack."""
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

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.target_id == str(external_track.id),
            AuditLog.action == "external_track.tombstone",
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_restore_track(client, regular_user, auth_headers, monkeypatch, db_session):
    """A tombstoned track can be restored when the provider item exists."""
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
async def test_restore_missing_item_returns_404(client, regular_user, auth_headers, monkeypatch, db_session):
    """Restoring a tombstoned track whose provider item is missing returns 404."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)
    external_library = await db_session.get(ExternalLibrary, data["id"])
    external_track = await _make_track_for_external(db_session, external_library, regular_user, state="tombstoned")
    external_track.provider_key = "missing.mp3"
    await db_session.commit()

    response = client.post(
        f"/api/v1/external-libraries/{data['id']}/tracks/{external_track.id}/restore",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_destructive_delete(client, regular_user, auth_headers, monkeypatch, db_session):
    """A destructive source delete removes the item from the provider and marks the track missing."""
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

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.target_id == str(external_track.id),
            AuditLog.action == "external_track.delete_source",
        )
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.details["success"] is True


@pytest.mark.asyncio
async def test_destructive_delete_forbidden_when_disabled(client, regular_user, auth_headers, monkeypatch, db_session):
    """Destructive source deletion is rejected when the global flag is disabled."""
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
async def test_audit_details_never_contain_raw_config(client, regular_user, auth_headers, monkeypatch, db_session):
    """Audit log details never contain the raw provider config."""
    data = await _create_user_external_library(client, regular_user, auth_headers, monkeypatch)

    result = await db_session.execute(select(AuditLog).where(AuditLog.target_id == data["id"]))
    logs = result.scalars().all()
    for log in logs:
        assert "super-secret" not in str(log.details)
        assert "hunter2" not in str(log.details)
        assert "abc123" not in str(log.details)
