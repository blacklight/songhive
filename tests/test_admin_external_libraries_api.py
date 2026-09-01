"""Tests for the admin external-library API."""

from typing import Any

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.models.audit_log import AuditLog
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_sync_run import ExternalSyncRun
from songhive.models.external_track import ExternalTrack


def _sync_delay(*args: Any, **kwargs: Any):
    """Return a stand-in Celery task result."""
    return type("Result", (), {"id": "task-123"})()


def _admin_create_payload(overrides: dict | None = None) -> dict:
    """Return a default create payload for the admin fake adapter."""
    payload = {
        "provider_type": "fake",
        "library_name": "Admin External Library",
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
            "secret_key": "admin-secret",
        },
    }
    if overrides:
        payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_admin_providers_requires_admin(client, regular_user, auth_headers):
    """GET /admin/external-libraries/providers requires an admin."""
    response = client.get(
        "/api/v1/admin/external-libraries/providers",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_admin_providers_lists_all(client, admin_user, auth_headers):
    """Admins see all provider types with their real capabilities."""
    response = client.get(
        "/api/v1/admin/external-libraries/providers",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["provider_type"] == "fake"
    assert data[0]["user_configurable"] is True
    assert data[0]["capabilities_summary"]["list_items"] is True


@pytest.mark.asyncio
async def test_admin_create(client, admin_user, auth_headers, monkeypatch, db_session):
    """Admins can create an admin-scoped external library."""
    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/admin/external-libraries",
        json=_admin_create_payload(),
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["scope"] == "admin"
    assert data["include_in_library_index"] is False
    assert data["config"]["secret_key"] == "<redacted>"
    assert data["can_manage"] is True

    external_library = await db_session.get(ExternalLibrary, data["id"])
    assert external_library is not None
    assert external_library.scope == "admin"


@pytest.mark.asyncio
async def test_admin_create_ignores_user_disable(client, admin_user, auth_headers, monkeypatch):
    """Admin external-library creation is not blocked by allow_user_created_libraries."""
    client.app.state.config.external_libraries.allow_user_created_libraries = False
    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/admin/external-libraries",
        json=_admin_create_payload(),
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.asyncio
async def test_admin_create_include_in_library_index(client, admin_user, auth_headers, monkeypatch, db_session):
    """Admins can opt an admin library into the library index when allowed."""
    client.app.state.config.external_libraries.allow_admin_library_index_inclusion = True
    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/admin/external-libraries",
        json=_admin_create_payload({"include_in_library_index": True}),
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["include_in_library_index"] is True


@pytest.mark.asyncio
async def test_admin_create_include_in_library_index_rejected_when_disabled(
    client, admin_user, auth_headers, monkeypatch
):
    """include_in_library_index is rejected when the admin inclusion flag is disabled."""
    client.app.state.config.external_libraries.allow_admin_library_index_inclusion = False
    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/admin/external-libraries",
        json=_admin_create_payload({"include_in_library_index": True}),
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


async def _create_user_library(client, regular_user, auth_headers, monkeypatch) -> dict:
    """Create a user-scoped external library through the user route."""
    client.app.state.config.external_libraries.allow_user_created_libraries = True
    monkeypatch.setattr(
        "songhive.api.routes.external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/external-libraries",
        json={
            "provider_type": "fake",
            "library_name": "User External Library",
            "config": {"items": {}},
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == status.HTTP_201_CREATED
    return response.json()


@pytest.mark.asyncio
async def test_admin_list_scope_filter(
    client,
    regular_user,
    admin_user,
    auth_headers,
    monkeypatch,
    db_session,
):
    """Admin list shows only admin-scoped rows by default and can include user rows."""
    user_data = await _create_user_library(client, regular_user, auth_headers, monkeypatch)

    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    admin_data = await _admin_create(client, admin_user, auth_headers, monkeypatch)

    response = client.get(
        "/api/v1/admin/external-libraries",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    items = response.json()
    assert len(items) == 1
    assert items[0]["id"] == admin_data["id"]

    response = client.get(
        "/api/v1/admin/external-libraries?include_user=true",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    items = response.json()
    assert len(items) == 2
    ids = {item["id"] for item in items}
    assert user_data["id"] in ids
    assert admin_data["id"] in ids


async def _admin_create(client, admin_user, auth_headers, monkeypatch) -> dict:
    """Create an admin-scoped external library and return its JSON."""
    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )
    response = client.post(
        "/api/v1/admin/external-libraries",
        json=_admin_create_payload(),
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_201_CREATED
    return response.json()


@pytest.mark.asyncio
async def test_admin_update(client, admin_user, auth_headers, monkeypatch, db_session):
    """Admins can update an external library, including include_in_library_index."""
    data = await _admin_create(client, admin_user, auth_headers, monkeypatch)

    response = client.patch(
        f"/api/v1/admin/external-libraries/{data['id']}",
        json={
            "name": "Updated Admin Library",
            "include_in_library_index": True,
            "enabled": False,
        },
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    updated = response.json()
    assert updated["name"] == "Updated Admin Library"
    assert updated["include_in_library_index"] is True
    assert updated["enabled"] is False


@pytest.mark.asyncio
async def test_admin_delete(client, admin_user, auth_headers, monkeypatch, db_session):
    """Admins can delete any external library."""
    data = await _admin_create(client, admin_user, auth_headers, monkeypatch)

    response = client.delete(
        f"/api/v1/admin/external-libraries/{data['id']}",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    assert await db_session.get(ExternalLibrary, data["id"]) is None

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.target_id == data["id"], AuditLog.action == "external_library.admin_delete")
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_admin_sync(client, admin_user, auth_headers, monkeypatch, db_session):
    """Admin sync endpoint creates a queued sync run."""
    data = await _admin_create(client, admin_user, auth_headers, monkeypatch)
    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        _sync_delay,
    )

    response = client.post(
        f"/api/v1/admin/external-libraries/{data['id']}/sync",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_202_ACCEPTED
    body = response.json()
    assert body["sync_run_id"]

    run = await db_session.get(ExternalSyncRun, body["sync_run_id"])
    assert run is not None
    assert run.status == "queued"
    assert str(run.triggered_by_user_id) == str(admin_user.id)


@pytest.mark.asyncio
async def test_admin_tracks_list(client, admin_user, auth_headers, monkeypatch, db_session):
    """Admins can list external tracks for any library."""
    data = await _admin_create(client, admin_user, auth_headers, monkeypatch)
    external_library = await db_session.get(ExternalLibrary, data["id"])

    track = ExternalTrack(
        external_library_id=str(external_library.id),
        provider_key="song1.mp3",
        state="active",
        sha256="abc123",
    )
    db_session.add(track)
    await db_session.commit()

    response = client.get(
        f"/api/v1/admin/external-libraries/{data['id']}/tracks",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == status.HTTP_200_OK
    items = response.json()
    assert len(items) == 1
    assert items[0]["provider_key"] == "song1.mp3"


@pytest.mark.asyncio
async def test_non_admin_cannot_access_admin_routes(client, regular_user, auth_headers):
    """Regular users are forbidden from all admin external-library routes."""
    for method, path in [
        ("get", "/api/v1/admin/external-libraries"),
        ("post", "/api/v1/admin/external-libraries"),
        ("get", "/api/v1/admin/external-libraries/providers"),
    ]:
        response = getattr(client, method)(path, headers=auth_headers(regular_user))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_admin_audit_details_redacted(client, admin_user, auth_headers, monkeypatch, db_session):
    """Admin create audit details never contain raw provider secrets."""
    data = await _admin_create(client, admin_user, auth_headers, monkeypatch)

    result = await db_session.execute(select(AuditLog).where(AuditLog.target_id == data["id"]))
    logs = result.scalars().all()
    for log in logs:
        assert "admin-secret" not in str(log.details)
