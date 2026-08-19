"""
OAuth2 client registration and admin API tests.
"""

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.api.middleware.auth import create_access_token
from songhive.models.oauth_client import OAuth2Client
from songhive.services.auth import create_user
from songhive.users import oauth as oauth_client_service


async def _make_user(db_session, *args, **kwargs):
    """Helper to create a user and flush it to the test session."""
    user = await create_user(db_session, *args, **kwargs)
    await db_session.flush()
    return user


def _admin_token(user, config):
    """Return an access token for the given user."""
    return create_access_token(user.id, config.auth.secret_key)


@pytest.mark.asyncio
async def test_oauth_list_clients_requires_authentication(client):
    """Test that listing OAuth clients requires authentication."""
    response = client.get("/api/v1/admin/oauth/clients")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_oauth_list_clients_forbids_non_admin(client, db_session, config):
    """Test that non-admin users cannot list OAuth clients."""
    user = await _make_user(db_session, "alice", "alice@example.com", "secret")
    token = _admin_token(user, config)

    response = client.get(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_oauth_list_clients_returns_clients(client, db_session, config):
    """Test that admins can list OAuth clients."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()
    token = _admin_token(admin, config)

    response = client.get(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["client_id"] == client_obj.client_id
    assert data[0]["name"] == "Test Client"
    assert data[0]["redirect_uris"] == ["https://example.com/callback"]
    assert data[0]["grant_types"] == ["authorization_code"]
    assert data[0]["owner_id"] == admin.id
    assert data[0]["is_confidential"] is True
    assert "client_secret" not in data[0]
    assert "client_secret_hash" not in data[0]
    assert "X-Total-Count" in response.headers
    assert response.headers["X-Total-Count"] == "1"


@pytest.mark.asyncio
async def test_oauth_list_clients_paginates(client, db_session, config):
    """Test that the OAuth client list supports limit and offset."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    for i in range(3):
        await oauth_client_service.create_oauth_client(
            db_session,
            created_by=admin.id,
            name=f"Client {i}",
            redirect_uris=[f"https://example{i}.com/callback"],
        )
    await db_session.flush()
    token = _admin_token(admin, config)

    response = client.get(
        "/api/v1/admin/oauth/clients?limit=2&offset=0",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 2

    response = client.get(
        "/api/v1/admin/oauth/clients?limit=2&offset=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_oauth_create_client(client, db_session, config):
    """Test that an admin can create an OAuth2 client."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = _admin_token(admin, config)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Test Client",
            "redirect_uris": ["https://example.com/callback"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["name"] == "Test Client"
    assert data["redirect_uris"] == ["https://example.com/callback"]
    assert data["grant_types"] == ["authorization_code"]
    assert data["owner_id"] == admin.id
    assert data["is_confidential"] is True
    assert data["client_secret"]
    assert data["client_id"]


@pytest.mark.asyncio
async def test_oauth_create_client_secret_hashed(client, db_session, config):
    """Test that client secrets are stored as a hash, not plaintext."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = _admin_token(admin, config)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Test Client",
            "redirect_uris": ["https://example.com/callback"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    raw_secret = data["client_secret"]
    client_id = data["client_id"]

    client_obj = await oauth_client_service.get_oauth_client_by_client_id(db_session, client_id)
    assert client_obj is not None
    assert client_obj.client_secret_hash is not None
    assert client_obj.client_secret_hash != raw_secret
    assert client_obj.client_secret_hash.startswith("$2b$")
    assert oauth_client_service.check_client_secret(client_obj, raw_secret) is True
    assert oauth_client_service.check_client_secret(client_obj, "wrong-secret") is False


@pytest.mark.asyncio
async def test_oauth_create_public_client(client, db_session, config):
    """Test that a public client does not receive a client secret."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = _admin_token(admin, config)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Public Client",
            "redirect_uris": ["https://example.com/callback"],
            "is_confidential": False,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["is_confidential"] is False
    assert data["client_secret"] is None

    client_obj = await oauth_client_service.get_oauth_client_by_client_id(db_session, data["client_id"])
    assert client_obj is not None
    assert client_obj.client_secret_hash is None


@pytest.mark.asyncio
async def test_oauth_create_client_rejects_invalid_redirect_uri(client, db_session, config):
    """Test that invalid redirect URIs are rejected."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = _admin_token(admin, config)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Bad Client",
            "redirect_uris": ["not-a-valid-uri"],
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_create_client_rejects_invalid_grant_type(client, db_session, config):
    """Test that unknown grant types are rejected."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = _admin_token(admin, config)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Bad Client",
            "redirect_uris": ["https://example.com/callback"],
            "grant_types": ["implicit"],
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_create_client_forbids_non_admin(client, db_session, config):
    """Test that non-admin users cannot create OAuth clients."""
    user = await _make_user(db_session, "alice", "alice@example.com", "secret")
    token = _admin_token(user, config)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Test Client",
            "redirect_uris": ["https://example.com/callback"],
        },
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_oauth_delete_client(client, db_session, config):
    """Test that an admin can delete an OAuth client."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="To Delete",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()
    token = _admin_token(admin, config)

    response = client.delete(
        f"/api/v1/admin/oauth/clients/{client_obj.client_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    list_response = client.get(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_response.json() == []


@pytest.mark.asyncio
async def test_oauth_delete_client_missing(client, db_session, config):
    """Test that deleting a missing OAuth client returns 404."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = _admin_token(admin, config)

    response = client.delete(
        "/api/v1/admin/oauth/clients/not-a-real-client-id",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_oauth_delete_client_forbids_non_admin(client, db_session, config):
    """Test that non-admin users cannot delete OAuth clients."""
    user = await _make_user(db_session, "alice", "alice@example.com", "secret")
    token = _admin_token(user, config)

    response = client.delete(
        "/api/v1/admin/oauth/clients/not-a-real-client-id",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_oauth_create_client_validates_owner(client, db_session, config):
    """Test that an invalid owner_id is rejected."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = _admin_token(admin, config)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Test Client",
            "redirect_uris": ["https://example.com/callback"],
            "owner_id": "missing-id",
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_client_secret_not_in_list(client, db_session, config):
    """Test that created client secrets are not exposed when listing clients."""
    admin = await _make_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    token = _admin_token(admin, config)

    create_response = client.post(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Test Client",
            "redirect_uris": ["https://example.com/callback"],
        },
    )
    assert create_response.status_code == status.HTTP_201_CREATED

    list_response = client.get(
        "/api/v1/admin/oauth/clients",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_response.status_code == status.HTTP_200_OK
    data = list_response.json()
    assert len(data) == 1
    assert "client_secret" not in data[0]
    assert "client_secret_hash" not in data[0]

    result = await db_session.execute(select(OAuth2Client).where(OAuth2Client.client_id == data[0]["client_id"]))
    client_obj = result.scalar_one()
    assert client_obj.client_secret_hash is not None
