"""
Admin user management API tests.
"""

import pytest
from fastapi import status

from songhive.api.middleware.auth import create_access_token
from songhive.models.user import UserRole
from songhive.services.auth import create_user


async def _make_user(db_session, *args, **kwargs):
    """Helper to create a user and flush it to the test session."""
    user = await create_user(db_session, *args, **kwargs)
    await db_session.flush()
    return user


async def _admin_token(user, config):
    """Return an access token for the given user."""
    return create_access_token(user.id, config.auth.secret_key)


@pytest.mark.asyncio
async def test_admin_list_users_requires_authentication(client):
    """Test that listing users requires authentication."""
    response = client.get("/api/v1/admin/users")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_admin_list_users_forbids_non_admin(client, db_session, config):
    """Test that non-admin users cannot list users."""
    user = await _make_user(db_session, "alice", "alice@example.com", "secret")
    token = await _admin_token(user, config)

    response = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_admin_list_users_returns_users(client, db_session, config):
    """Test that admins can list users."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await _make_user(db_session, "alice", "alice@example.com", "secret")
    token = await _admin_token(admin, config)

    response = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 2

    usernames = {u["username"] for u in data}
    assert usernames == {"admin", "alice"}

    sensitive_fields = {
        "password_hash",
        "email_verification_token",
        "password_reset_token",
        "password_reset_expires_at",
        "private_key_pem",
        "public_key_pem",
    }
    for user in data:
        for field in sensitive_fields:
            assert field not in user, f"Sensitive field {field!r} leaked into admin list"


@pytest.mark.asyncio
async def test_admin_list_users_paginates(client, db_session, config):
    """Test that the user list supports limit and offset."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await _make_user(db_session, "alice", "alice@example.com", "secret")
    await _make_user(db_session, "bob", "bob@example.com", "secret")
    token = await _admin_token(admin, config)

    response = client.get(
        "/api/v1/admin/users?limit=2&offset=0",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 2

    response = client.get(
        "/api/v1/admin/users?limit=2&offset=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_admin_promote_user(client, db_session, config):
    """Test that an admin can promote a user to admin."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    user = await _make_user(db_session, "alice", "alice@example.com", "secret")
    token = await _admin_token(admin, config)

    response = client.post(
        f"/api/v1/admin/users/{user.id}/promote",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["role"] == UserRole.ADMIN
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_admin_demote_user(client, db_session, config):
    """Test that an admin can demote another admin to user."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    other_admin = await _make_user(db_session, "alice", "alice@example.com", "secret", role="admin")
    token = await _admin_token(admin, config)

    response = client.post(
        f"/api/v1/admin/users/{other_admin.id}/demote",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["role"] == UserRole.USER


@pytest.mark.asyncio
async def test_admin_cannot_demote_last_admin(client, db_session, config):
    """Test that the last active admin cannot be demoted."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = await _admin_token(admin, config)

    response = client.post(
        f"/api/v1/admin/users/{admin.id}/demote",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_admin_approve_user(client, db_session, config):
    """Test that an admin can approve an inactive user."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    user = await _make_user(db_session, "alice", "alice@example.com", "secret", is_active=False)
    token = await _admin_token(admin, config)

    response = client.post(
        f"/api/v1/admin/users/{user.id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_active"] is True


@pytest.mark.asyncio
async def test_admin_deactivate_user(client, db_session, config):
    """Test that an admin can deactivate a user."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    user = await _make_user(db_session, "alice", "alice@example.com", "secret")
    token = await _admin_token(admin, config)

    response = client.post(
        f"/api/v1/admin/users/{user.id}/deactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_active"] is False


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_last_admin(client, db_session, config):
    """Test that the last active admin cannot be deactivated."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = await _admin_token(admin, config)

    response = client.post(
        f"/api/v1/admin/users/{admin.id}/deactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_admin_activate_user(client, db_session, config):
    """Test that an admin can reactivate a deactivated user."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    user = await _make_user(db_session, "alice", "alice@example.com", "secret", is_active=False)
    token = await _admin_token(admin, config)

    response = client.post(
        f"/api/v1/admin/users/{user.id}/activate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_active"] is True


@pytest.mark.asyncio
async def test_admin_action_forbids_non_admin(client, db_session, config):
    """Test that non-admin users cannot use admin lifecycle endpoints."""
    user = await _make_user(db_session, "alice", "alice@example.com", "secret")
    target = await _make_user(db_session, "bob", "bob@example.com", "secret")
    token = await _admin_token(user, config)

    for path in [
        f"/api/v1/admin/users/{target.id}/promote",
        f"/api/v1/admin/users/{target.id}/demote",
        f"/api/v1/admin/users/{target.id}/approve",
        f"/api/v1/admin/users/{target.id}/activate",
        f"/api/v1/admin/users/{target.id}/deactivate",
    ]:
        response = client.post(path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == status.HTTP_403_FORBIDDEN, path


@pytest.mark.asyncio
async def test_admin_action_returns_404_for_missing_user(client, db_session, config):
    """Test that admin actions return 404 for unknown users."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = await _admin_token(admin, config)

    for path in [
        "/api/v1/admin/users/missing-id/promote",
        "/api/v1/admin/users/missing-id/demote",
        "/api/v1/admin/users/missing-id/approve",
        "/api/v1/admin/users/missing-id/activate",
        "/api/v1/admin/users/missing-id/deactivate",
    ]:
        response = client.post(path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == status.HTTP_404_NOT_FOUND, path
