"""
Tests for the admin feature backend additions.

Covers audit logging, user search/delete, runtime settings, content reports,
admin track deletion, system stats, bulk operations, and registration mode
integration.
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.audit_log import AuditLog
from songhive.models.report import Report
from songhive.models.setting import Setting
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import audit as audit_service
from songhive.users.tokens import issue_token_pair, validate_refresh_token


@pytest.mark.asyncio
async def test_audit_log_service_creates_log(db_session):
    """The audit log service persists entries with details."""
    log = await audit_service.log_action(
        db_session,
        actor_id="actor-1",
        action="user.login",
        target_type="user",
        target_id="target-1",
        details={"ip": "127.0.0.1"},
    )
    await db_session.commit()

    assert log.id is not None
    assert log.action == "user.login"
    assert log.target_type == "user"


@pytest.mark.asyncio
async def test_admin_list_audit_logs(client, db_session, make_user, auth_headers):
    """Admins can list audit log entries."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice")
    headers = auth_headers(admin)

    await audit_service.log_action(
        db_session,
        actor_id=admin.id,
        action="user.promote",
        target_type="user",
        target_id=user.id,
        details={"new_role": "admin"},
    )
    await db_session.commit()

    response = client.get("/api/v1/admin/audit", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["action"] == "user.promote"
    assert "X-Total-Count" in response.headers
    assert int(response.headers["X-Total-Count"]) >= 1


@pytest.mark.asyncio
async def test_admin_audit_filtering(client, db_session, make_user, auth_headers):
    """Audit log listing supports filtering by action and target type."""
    admin = await make_user("admin", role="admin")
    await make_user("alice")
    headers = auth_headers(admin)

    await audit_service.log_action(db_session, actor_id=admin.id, action="user.activate")
    await audit_service.log_action(db_session, actor_id=admin.id, action="oauth_client.create")
    await db_session.commit()

    response = client.get("/api/v1/admin/audit?action=user.activate", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert all(log["action"] == "user.activate" for log in data)


@pytest.mark.asyncio
async def test_admin_search_users(client, db_session, make_user, auth_headers):
    """Admins can search users by username or email."""
    admin = await make_user("admin", role="admin")
    await make_user("alicelia", email="alicelia@example.com")
    await make_user("bob", email="bob@example.com")
    headers = auth_headers(admin)

    response = client.get("/api/v1/admin/users?q=alice", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["username"] == "alicelia"
    assert "X-Total-Count" in response.headers


@pytest.mark.asyncio
async def test_admin_delete_user(client, db_session, make_user, auth_headers):
    """Admins can delete a user and dependent data."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice")
    headers = auth_headers(admin)

    response = client.delete(f"/api/v1/admin/users/{user.id}", headers=headers)
    assert response.status_code == status.HTTP_204_NO_CONTENT

    result = await db_session.execute(select(User).where(User.id == user.id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_admin_cannot_delete_last_active_admin(client, db_session, make_user, auth_headers):
    """Deleting the last active admin is rejected."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.delete(f"/api/v1/admin/users/{admin.id}", headers=headers)
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_admin_delete_user_revokes_refresh_tokens(client, config, fake_redis, make_user, auth_headers):
    """Deleting a user revokes all refresh tokens in Redis."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice")
    headers = auth_headers(admin)

    token_pair = await issue_token_pair(user, config, fake_redis)
    assert await validate_refresh_token(token_pair.refresh_token, fake_redis) is not None

    response = client.delete(f"/api/v1/admin/users/{user.id}", headers=headers)
    assert response.status_code == status.HTTP_204_NO_CONTENT

    assert await validate_refresh_token(token_pair.refresh_token, fake_redis) is None


@pytest.mark.asyncio
async def test_deactivate_user_revokes_refresh_tokens(client, config, db_session, fake_redis, make_user, auth_headers):
    """Deactivating a user revokes all refresh tokens in Redis."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice")
    headers = auth_headers(admin)

    token_pair = await issue_token_pair(user, config, fake_redis)
    assert await validate_refresh_token(token_pair.refresh_token, fake_redis) is not None

    response = client.post(f"/api/v1/admin/users/{user.id}/deactivate", headers=headers)
    assert response.status_code == status.HTTP_200_OK

    assert await validate_refresh_token(token_pair.refresh_token, fake_redis) is None


@pytest.mark.asyncio
async def test_admin_list_settings(client, db_session, make_user, auth_headers):
    """Admins can list runtime settings and see defaults."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.get("/api/v1/admin/settings", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = {item["key"]: item["value"] for item in response.json()}
    assert data["registration_mode"] == "open"
    assert data["federation_enabled"] is True


@pytest.mark.asyncio
async def test_admin_update_setting(client, db_session, fake_redis, make_user, auth_headers):
    """Admins can update a runtime setting and it is reflected in the DB."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.put(
        "/api/v1/admin/settings/registration_mode",
        headers=headers,
        json={"value": "closed"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["value"] == "closed"

    # Cache should be invalidated on write.
    cached = await fake_redis.get("setting:registration_mode")
    assert cached is None

    row = (await db_session.execute(select(Setting).where(Setting.key == "registration_mode"))).scalar_one()
    assert json.loads(row.value) == "closed"


@pytest.mark.asyncio
async def test_registration_mode_override_takes_effect(client, db_session, fake_redis, make_user, auth_headers):
    """Changing registration mode via settings prevents new registrations."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    client.put(
        "/api/v1/admin/settings/registration_mode",
        headers=headers,
        json={"value": "closed"},
    )

    response = client.post(
        "/api/v1/auth/register",
        json={"username": "newbie", "email": "newbie@example.com", "password": "secretsecret"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_instance_name_overlays_federation_config(client, db_session, fake_redis, make_user, auth_headers):
    """Changing instance_name updates the federation config in app state."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.put(
        "/api/v1/admin/settings/instance_name",
        headers=headers,
        json={"value": "My Songhive"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert client.app.state.config.federation.instance_name == "My Songhive"


@pytest.mark.asyncio
async def test_create_report(client, db_session, make_user, auth_headers):
    """Authenticated users can submit content reports."""
    reporter = await make_user("reporter", email_verified=True)
    target = await make_user("target", email_verified=True)
    headers = auth_headers(reporter)

    response = client.post(
        "/api/v1/reports",
        headers=headers,
        json={
            "target_type": "user",
            "target_id": target.id,
            "reason": "harassment",
            "description": " abusive behavior ",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["reporter_id"] == reporter.id
    assert data["target_type"] == "user"
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_admin_list_reports(client, db_session, make_user, auth_headers):
    """Admins can list reports."""
    admin = await make_user("admin", role="admin")
    reporter = await make_user("reporter", email_verified=True)
    target = await make_user("target", email_verified=True)
    admin_headers = auth_headers(admin)

    report = Report(
        reporter_id=reporter.id,
        target_type="user",
        target_id=target.id,
        reason="spam",
    )
    db_session.add(report)
    await db_session.commit()

    response = client.get("/api/v1/admin/reports", headers=admin_headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["reason"] == "spam"
    assert "X-Total-Count" in response.headers


@pytest.mark.asyncio
async def test_admin_update_report(client, db_session, make_user, auth_headers):
    """Admins can resolve a report and an audit log is created."""
    admin = await make_user("admin", role="admin")
    reporter = await make_user("reporter", email_verified=True)
    target = await make_user("target", email_verified=True)
    admin_headers = auth_headers(admin)

    report = Report(
        reporter_id=reporter.id,
        target_type="user",
        target_id=target.id,
        reason="spam",
    )
    db_session.add(report)
    await db_session.commit()

    response = client.put(
        f"/api/v1/admin/reports/{report.id}",
        headers=admin_headers,
        json={"status": "resolved", "resolution_notes": "Banned user"},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "resolved"
    assert data["reviewed_by"] == admin.id
    assert data["resolution_notes"] == "Banned user"

    result = await db_session.execute(select(AuditLog).where(AuditLog.action == "report.update"))
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_admin_track_delete_override(client, db_session, make_user, auth_headers):
    """Admins can delete any track and are audit-logged."""
    admin = await make_user("admin", role="admin")
    owner = await make_user("owner", email_verified=True)
    headers = auth_headers(admin)

    artist = Artist(name="Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(title="Delete Me", artist_id=artist.id, owner_id=owner.id, duration=1.0)
    db_session.add(track)
    await db_session.commit()

    response = client.delete(f"/api/v1/admin/tracks/{track.id}", headers=headers)
    assert response.status_code == status.HTTP_204_NO_CONTENT

    result = await db_session.execute(select(Track).where(Track.id == track.id))
    assert result.scalar_one_or_none() is None

    result = await db_session.execute(select(AuditLog).where(AuditLog.action == "track.admin_delete"))
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_admin_stats_endpoint(client, db_session, make_user, auth_headers):
    """The stats endpoint returns user and content counts."""
    admin = await make_user("admin", role="admin")
    await make_user("alice", email_verified=True)
    headers = auth_headers(admin)

    response = client.get("/api/v1/admin/stats", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["users"]["total_users"] >= 2
    assert data["federation"]["enabled"] is False
    assert "celery" in data
    assert "available" in data["celery"]


@pytest.mark.asyncio
async def test_bulk_deactivate_users(client, config, db_session, fake_redis, make_user, auth_headers):
    """Bulk deactivation revokes tokens and returns per-user status."""
    admin = await make_user("admin", role="admin")
    u1 = await make_user("u1")
    u2 = await make_user("u2")
    headers = auth_headers(admin)

    pair1 = await issue_token_pair(u1, config, fake_redis)
    pair2 = await issue_token_pair(u2, config, fake_redis)
    assert await validate_refresh_token(pair1.refresh_token, fake_redis) is not None
    assert await validate_refresh_token(pair2.refresh_token, fake_redis) is not None

    response = client.post(
        "/api/v1/admin/users/bulk",
        headers=headers,
        json={"action": "deactivate", "user_ids": [u1.id, u2.id]},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["processed"] == 2
    assert data["failed"] == []

    assert await validate_refresh_token(pair1.refresh_token, fake_redis) is None
    assert await validate_refresh_token(pair2.refresh_token, fake_redis) is None


@pytest.mark.asyncio
async def test_bulk_delete_skips_last_admin(client, db_session, fake_redis, make_user, auth_headers):
    """Bulk delete records failures without aborting the batch."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/users/bulk",
        headers=headers,
        json={"action": "delete", "user_ids": [admin.id, user.id]},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["processed"] == 1
    assert len(data["failed"]) == 1
    assert data["failed"][0]["user_id"] == admin.id


@pytest.mark.asyncio
async def test_bulk_delete_continues_after_missing_user(client, db_session, make_user, auth_headers):
    """Bulk delete processes valid users even when one user does not exist."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/users/bulk",
        headers=headers,
        json={
            "action": "delete",
            "user_ids": ["00000000-0000-0000-0000-000000000000", user.id],
        },
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["processed"] == 1
    assert len(data["failed"]) == 1
    assert data["failed"][0]["user_id"] == "00000000-0000-0000-0000-000000000000"

    result = await db_session.execute(select(User).where(User.id == user.id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_bulk_action_invalid_action(client, db_session, make_user, auth_headers):
    """Bulk action rejects unknown actions."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/users/bulk",
        headers=headers,
        json={"action": "ban", "user_ids": [user.id]},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_settings_list_includes_updated_at(client, db_session, make_user, auth_headers):
    """Setting list returns updated_at once a setting has been written."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    client.put(
        "/api/v1/admin/settings/instance_name",
        headers=headers,
        json={"value": "Updated Name"},
    )

    response = client.get("/api/v1/admin/settings", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = {item["key"]: item for item in response.json()}
    assert data["instance_name"]["value"] == "Updated Name"
    assert data["instance_name"]["updated_at"] is not None


# CLI tests


def _make_args(**kwargs):
    return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_admin_main_disable_user(db_session, monkeypatch, capsys):
    """The disable-user handler deactivates a user."""
    from songhive.cli import admin as cli_admin
    from songhive.services.auth import create_user

    user = await create_user(db_session, "alice", "alice@example.com", "secret", role="user")
    await db_session.flush()

    @asynccontextmanager
    async def _fake_session():
        yield db_session

    monkeypatch.setattr(cli_admin, "get_session", _fake_session)
    args = _make_args(username="alice")
    await cli_admin._handle_disable_user(args)
    captured = capsys.readouterr()
    assert "deactivated" in captured.out
    assert user.is_active is False


@pytest.mark.asyncio
async def test_admin_main_reset_password(db_session, monkeypatch, capsys):
    """The reset-password handler updates a user's password."""
    from songhive.cli import admin as cli_admin
    from songhive.services.auth import create_user

    user = await create_user(db_session, "alice", "alice@example.com", "secret", role="user")
    await db_session.flush()
    old_hash = user.password_hash

    @asynccontextmanager
    async def _fake_session():
        yield db_session

    monkeypatch.setattr(cli_admin, "get_session", _fake_session)
    args = _make_args(username="alice", password="newsecret")
    await cli_admin._handle_reset_password(args)
    captured = capsys.readouterr()
    assert "Password reset" in captured.out

    # Password was hashed using the same user object.
    assert user.password_hash != old_hash


@pytest.mark.asyncio
async def test_admin_sync_tags_endpoint(client, db_session, make_user, auth_headers, monkeypatch):
    """Admins can enqueue tag sync for a single track and the action is audited."""
    admin = await make_user("admin", role="admin")

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Test Track",
        artist_id=artist.id,
        owner_id=str(admin.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.commit()

    sync_mock = MagicMock()
    monkeypatch.setattr("songhive.api.routes.admin.sync_track_tags", sync_mock)

    response = client.post(
        "/api/v1/admin/sync-tags",
        headers=auth_headers(admin),
        json={"track_id": str(track.id)},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["enqueued"] == 1
    assert data["status"] == "queued"
    sync_mock.delay.assert_called_once_with(str(track.id))

    result = await db_session.execute(select(AuditLog).where(AuditLog.action == "tags.sync"))
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.actor_id == str(admin.id)
    assert log.details["enqueued"] == 1
