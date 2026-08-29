"""
Admin user management API tests.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status

from songhive.models.user import UserRole
from songhive.users.invites import create_invite


@pytest.mark.asyncio
async def test_admin_list_users_requires_authentication(client):
    """Test that listing users requires authentication."""
    response = client.get("/api/v1/admin/users")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_admin_list_users_forbids_non_admin(client, db_session, config, make_user, auth_headers):
    """Test that non-admin users cannot list users."""
    user = await make_user("alice")
    headers = auth_headers(user)

    response = client.get(
        "/api/v1/admin/users",
        headers=headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_admin_list_users_returns_users(client, db_session, config, make_user, auth_headers):
    """Test that admins can list users."""
    admin = await make_user("admin", role="admin")
    await make_user("alice")
    headers = auth_headers(admin)

    response = client.get(
        "/api/v1/admin/users",
        headers=headers,
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
async def test_admin_list_users_paginates(client, db_session, config, make_user, auth_headers):
    """Test that the user list supports limit and offset."""
    admin = await make_user("admin", role="admin")
    await make_user("alice")
    await make_user("bob")
    headers = auth_headers(admin)

    response = client.get(
        "/api/v1/admin/users?limit=2&offset=0",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 2

    response = client.get(
        "/api/v1/admin/users?limit=2&offset=2",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_admin_list_users_ignores_empty_query(client, db_session, config, make_user, auth_headers):
    """An empty or whitespace-only query must not trigger a 422 and should list all users."""
    admin = await make_user("admin", role="admin")
    await make_user("alice")
    headers = auth_headers(admin)

    response = client.get(
        "/api/v1/admin/users?q=&limit=25&offset=0",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 2
    usernames = {u["username"] for u in data}
    assert usernames == {"admin", "alice"}

    response = client.get(
        "/api/v1/admin/users?q=%20%20&limit=25&offset=0",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_admin_promote_user(client, db_session, config, make_user, auth_headers):
    """Test that an admin can promote a user to admin."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice")
    headers = auth_headers(admin)

    response = client.post(
        f"/api/v1/admin/users/{user.id}/promote",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["role"] == UserRole.ADMIN
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_admin_demote_user(client, db_session, config, make_user, auth_headers):
    """Test that an admin can demote another admin to user."""
    admin = await make_user("admin", role="admin")
    other_admin = await make_user("alice", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        f"/api/v1/admin/users/{other_admin.id}/demote",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["role"] == UserRole.USER


@pytest.mark.asyncio
async def test_admin_cannot_demote_last_admin(client, db_session, config, make_user, auth_headers):
    """Test that the last active admin cannot be demoted."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        f"/api/v1/admin/users/{admin.id}/demote",
        headers=headers,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_admin_approve_user(client, db_session, config, make_user, auth_headers):
    """Test that an admin can approve an inactive user."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice", is_active=False)
    headers = auth_headers(admin)

    response = client.post(
        f"/api/v1/admin/users/{user.id}/approve",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_active"] is True


@pytest.mark.asyncio
async def test_admin_deactivate_user(client, db_session, config, make_user, auth_headers):
    """Test that an admin can deactivate a user."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice")
    headers = auth_headers(admin)

    response = client.post(
        f"/api/v1/admin/users/{user.id}/deactivate",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_active"] is False


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_last_admin(client, db_session, config, make_user, auth_headers):
    """Test that the last active admin cannot be deactivated."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        f"/api/v1/admin/users/{admin.id}/deactivate",
        headers=headers,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_admin_activate_user(client, db_session, config, make_user, auth_headers):
    """Test that an admin can reactivate a deactivated user."""
    admin = await make_user("admin", role="admin")
    user = await make_user("alice", is_active=False)
    headers = auth_headers(admin)

    response = client.post(
        f"/api/v1/admin/users/{user.id}/activate",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_active"] is True


@pytest.mark.asyncio
async def test_admin_action_forbids_non_admin(client, db_session, config, make_user, auth_headers):
    """Test that non-admin users cannot use admin lifecycle endpoints."""
    user = await make_user("alice")
    target = await make_user("bob")
    headers = auth_headers(user)

    for path in [
        f"/api/v1/admin/users/{target.id}/promote",
        f"/api/v1/admin/users/{target.id}/demote",
        f"/api/v1/admin/users/{target.id}/approve",
        f"/api/v1/admin/users/{target.id}/activate",
        f"/api/v1/admin/users/{target.id}/deactivate",
    ]:
        response = client.post(path, headers=headers)
        assert response.status_code == status.HTTP_403_FORBIDDEN, path


@pytest.mark.asyncio
async def test_admin_action_returns_404_for_missing_user(client, db_session, config, make_user, auth_headers):
    """Test that admin actions return 404 for unknown users."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    for path in [
        "/api/v1/admin/users/missing-id/promote",
        "/api/v1/admin/users/missing-id/demote",
        "/api/v1/admin/users/missing-id/approve",
        "/api/v1/admin/users/missing-id/activate",
        "/api/v1/admin/users/missing-id/deactivate",
    ]:
        response = client.post(path, headers=headers)
        assert response.status_code == status.HTTP_404_NOT_FOUND, path


@pytest.mark.asyncio
async def test_admin_list_invites_requires_authentication(client):
    """Test that listing invites requires authentication."""
    response = client.get("/api/v1/admin/invites")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_admin_list_invites_forbids_non_admin(client, db_session, config, make_user, auth_headers):
    """Test that non-admin users cannot list invites."""
    user = await make_user("alice")
    headers = auth_headers(user)

    response = client.get(
        "/api/v1/admin/invites",
        headers=headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_admin_list_invites_returns_invites(client, db_session, config, make_user, auth_headers):
    """Test that admins can list invite codes."""
    admin = await make_user("admin", role="admin")
    invite = await create_invite(db_session, created_by=admin.id, max_uses=5)
    await db_session.flush()
    headers = auth_headers(admin)

    response = client.get(
        "/api/v1/admin/invites",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["code"] == invite.code
    assert data[0]["created_by"] == admin.id
    assert data[0]["max_uses"] == 5
    assert data[0]["uses"] == 0
    assert "X-Total-Count" in response.headers
    assert response.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
async def test_admin_create_invite(client, db_session, config, make_user, auth_headers):
    """Test that an admin can create an invite code."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    response = client.post(
        "/api/v1/admin/invites",
        headers=headers,
        json={"max_uses": 10, "expires_at": expires_at},
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["created_by"] == admin.id
    assert data["max_uses"] == 10
    assert data["uses"] == 0
    assert data["code"]
    assert data["expires_at"]


@pytest.mark.asyncio
async def test_admin_create_invite_rejects_invalid_max_uses(client, db_session, config, make_user, auth_headers):
    """Test that invalid invite parameters are rejected."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/invites",
        headers=headers,
        json={"max_uses": 0},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_admin_create_invite_forbids_non_admin(client, db_session, config, make_user, auth_headers):
    """Test that non-admin users cannot create invites."""
    user = await make_user("alice")
    headers = auth_headers(user)

    response = client.post(
        "/api/v1/admin/invites",
        headers=headers,
        json={"max_uses": 1},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_admin_delete_invite(client, db_session, config, make_user, auth_headers):
    """Test that an admin can revoke an invite code."""
    admin = await make_user("admin", role="admin")
    invite = await create_invite(db_session, created_by=admin.id)
    await db_session.flush()
    headers = auth_headers(admin)

    response = client.delete(
        f"/api/v1/admin/invites/{invite.code}",
        headers=headers,
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    list_response = client.get(
        "/api/v1/admin/invites",
        headers=headers,
    )
    assert list_response.json() == []


@pytest.mark.asyncio
async def test_admin_delete_invite_missing(client, db_session, config, make_user, auth_headers):
    """Test that deleting a missing invite returns 404."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.delete(
        "/api/v1/admin/invites/not-a-real-code",
        headers=headers,
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_admin_delete_invite_forbids_non_admin(client, db_session, config, make_user, auth_headers):
    """Test that non-admin users cannot delete invites."""
    user = await make_user("alice")
    headers = auth_headers(user)

    response = client.delete(
        "/api/v1/admin/invites/not-a-real-code",
        headers=headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
