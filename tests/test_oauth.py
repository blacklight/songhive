"""
OAuth2 client registration, admin API, and provider flow tests.
"""

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import status
from redis.exceptions import WatchError
from sqlalchemy import select

from songhive.models.oauth_client import OAuth2Client
from songhive.users import oauth as oauth_client_service


def _pkce_pair():
    """Return a PKCE verifier and the matching S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode().rstrip("=")
    return verifier, challenge


@pytest.mark.asyncio
async def test_oauth_list_clients_requires_authentication(client):
    """Test that listing OAuth clients requires authentication."""
    response = client.get("/api/v1/admin/oauth/clients")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_oauth_list_clients_forbids_non_admin(client, db_session, config, make_user, auth_headers):
    """Test that non-admin users cannot list OAuth clients."""
    user = await make_user("alice")
    headers = auth_headers(user)

    response = client.get(
        "/api/v1/admin/oauth/clients",
        headers=headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_oauth_list_clients_returns_clients(client, db_session, config, make_user, auth_headers):
    """Test that admins can list OAuth clients."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()
    headers = auth_headers(admin)

    response = client.get(
        "/api/v1/admin/oauth/clients",
        headers=headers,
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
async def test_oauth_list_clients_paginates(client, db_session, config, make_user, auth_headers):
    """Test that the OAuth client list supports limit and offset."""
    admin = await make_user("admin", role="admin")
    for i in range(3):
        await oauth_client_service.create_oauth_client(
            db_session,
            created_by=admin.id,
            name=f"Client {i}",
            redirect_uris=[f"https://example{i}.com/callback"],
        )
    await db_session.flush()
    headers = auth_headers(admin)

    response = client.get(
        "/api/v1/admin/oauth/clients?limit=2&offset=0",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 2

    response = client.get(
        "/api/v1/admin/oauth/clients?limit=2&offset=2",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_oauth_create_client(client, db_session, config, make_user, auth_headers):
    """Test that an admin can create an OAuth2 client."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
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

    created_at = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
    updated_at = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
    assert updated_at >= created_at
    assert (datetime.now(timezone.utc) - updated_at).total_seconds() < 60


@pytest.mark.asyncio
async def test_oauth_create_client_secret_hashed(client, db_session, config, make_user, auth_headers):
    """Test that client secrets are stored as a hash, not plaintext."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
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
async def test_oauth_create_client_custom_grant_types(client, db_session, config, make_user, auth_headers):
    """Test that admins can create a client with multiple valid grant types."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
        json={
            "name": "Custom Client",
            "redirect_uris": ["https://example.com/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["name"] == "Custom Client"
    assert data["grant_types"] == ["authorization_code", "refresh_token"]
    assert data["is_confidential"] is True
    assert data["client_secret"]
    assert data["client_id"]


@pytest.mark.asyncio
async def test_oauth_create_public_client(client, db_session, config, make_user, auth_headers):
    """Test that a public client does not receive a client secret."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
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
async def test_oauth_create_client_rejects_invalid_redirect_uri(client, db_session, config, make_user, auth_headers):
    """Test that invalid redirect URIs are rejected."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
        json={
            "name": "Bad Client",
            "redirect_uris": ["not-a-valid-uri"],
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_create_client_rejects_http_non_loopback(client, db_session, config, make_user, auth_headers):
    """Test that plain-HTTP redirect URIs are only allowed for localhost/loopback."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
        json={
            "name": "Bad Client",
            "redirect_uris": ["http://example.com/callback"],
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_create_client_allows_http_localhost(client, db_session, config, make_user, auth_headers):
    """Test that HTTP redirect URIs are allowed for localhost."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
        json={
            "name": "Local Client",
            "redirect_uris": [
                "http://localhost:8080/callback",
                "http://127.0.0.1:8080/callback",
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["redirect_uris"] == [
        "http://localhost:8080/callback",
        "http://127.0.0.1:8080/callback",
    ]


@pytest.mark.asyncio
async def test_oauth_create_client_rejects_invalid_grant_type(client, db_session, config, make_user, auth_headers):
    """Test that unknown grant types are rejected."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
        json={
            "name": "Bad Client",
            "redirect_uris": ["https://example.com/callback"],
            "grant_types": ["implicit"],
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_create_client_forbids_non_admin(client, db_session, config, make_user, auth_headers):
    """Test that non-admin users cannot create OAuth clients."""
    user = await make_user("alice")
    headers = auth_headers(user)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
        json={
            "name": "Test Client",
            "redirect_uris": ["https://example.com/callback"],
        },
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_oauth_delete_client(client, db_session, config, make_user, auth_headers):
    """Test that an admin can delete an OAuth client."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="To Delete",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()
    headers = auth_headers(admin)

    response = client.delete(
        f"/api/v1/admin/oauth/clients/{client_obj.client_id}",
        headers=headers,
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT

    list_response = client.get(
        "/api/v1/admin/oauth/clients",
        headers=headers,
    )
    assert list_response.json() == []


@pytest.mark.asyncio
async def test_oauth_delete_client_missing(client, db_session, config, make_user, auth_headers):
    """Test that deleting a missing OAuth client returns 404."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.delete(
        "/api/v1/admin/oauth/clients/not-a-real-client-id",
        headers=headers,
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_oauth_delete_client_forbids_non_admin(client, db_session, config, make_user, auth_headers):
    """Test that non-admin users cannot delete OAuth clients."""
    user = await make_user("alice")
    headers = auth_headers(user)

    response = client.delete(
        "/api/v1/admin/oauth/clients/not-a-real-client-id",
        headers=headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_oauth_get_client(client, db_session, config, make_user, auth_headers):
    """Test that an admin can retrieve a single OAuth2 client."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Single Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()
    headers = auth_headers(admin)

    response = client.get(
        f"/api/v1/admin/oauth/clients/{client_obj.client_id}",
        headers=headers,
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["client_id"] == client_obj.client_id
    assert data["name"] == "Single Client"
    assert "client_secret" not in data
    assert "client_secret_hash" not in data


@pytest.mark.asyncio
async def test_oauth_get_client_requires_authentication(client):
    """Test that retrieving a single OAuth2 client requires authentication."""
    response = client.get("/api/v1/admin/oauth/clients/not-a-real-client-id")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_oauth_get_client_forbids_non_admin(client, db_session, config, make_user, auth_headers):
    """Test that non-admin users cannot retrieve OAuth2 client details."""
    user = await make_user("alice")
    headers = auth_headers(user)

    response = client.get(
        "/api/v1/admin/oauth/clients/not-a-real-client-id",
        headers=headers,
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_oauth_get_client_missing(client, db_session, config, make_user, auth_headers):
    """Test that retrieving a missing OAuth2 client returns 404."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.get(
        "/api/v1/admin/oauth/clients/not-a-real-client-id",
        headers=headers,
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_oauth_create_client_validates_owner(client, db_session, config, make_user, auth_headers):
    """Test that an invalid owner_id is rejected."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
        json={
            "name": "Test Client",
            "redirect_uris": ["https://example.com/callback"],
            "owner_id": "missing-id",
        },
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_client_secret_not_in_list(client, db_session, config, make_user, auth_headers):
    """Test that created client secrets are not exposed when listing clients."""
    admin = await make_user("admin", role="admin")
    headers = auth_headers(admin)

    create_response = client.post(
        "/api/v1/admin/oauth/clients",
        headers=headers,
        json={
            "name": "Test Client",
            "redirect_uris": ["https://example.com/callback"],
        },
    )
    assert create_response.status_code == status.HTTP_201_CREATED

    list_response = client.get(
        "/api/v1/admin/oauth/clients",
        headers=headers,
    )
    assert list_response.status_code == status.HTTP_200_OK
    data = list_response.json()
    assert len(data) == 1
    assert "client_secret" not in data[0]
    assert "client_secret_hash" not in data[0]

    result = await db_session.execute(select(OAuth2Client).where(OAuth2Client.client_id == data[0]["client_id"]))
    client_obj = result.scalar_one()
    assert client_obj.client_secret_hash is not None


@pytest.mark.asyncio
async def test_oauth_authorize_and_token_flow(client, db_session, config, make_user, auth_headers):
    """Test the full authorization-code + PKCE flow, introspection and revocation."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
        grant_types=["authorization_code", "refresh_token"],
    )
    await db_session.flush()

    user = await make_user("alice")
    user_headers = auth_headers(user)
    verifier, challenge = _pkce_pair()

    authorize_response = client.get(
        f"/api/v1/auth/oauth/authorize?response_type=code&client_id={client_obj.client_id}"
        f"&redirect_uri=https://example.com/callback&state=xyz"
        f"&code_challenge={challenge}&code_challenge_method=S256",
        headers=user_headers,
        follow_redirects=False,
    )
    assert authorize_response.status_code == status.HTTP_302_FOUND
    location = authorize_response.headers["Location"]
    parsed = urlparse(location)
    assert parsed.netloc == "example.com"
    assert parsed.path == "/callback"
    params = parse_qs(parsed.query)
    assert "code" in params
    assert params.get("state") == ["xyz"]
    auth_code = params["code"][0]

    token_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://example.com/callback",
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == status.HTTP_200_OK
    token_data = token_response.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "Bearer"
    assert token_data["expires_in"] == 900
    assert "refresh_token" in token_data

    introspect_response = client.post(
        "/api/v1/auth/oauth/introspect",
        data={
            "token": token_data["access_token"],
            "token_type_hint": "access_token",
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
        },
    )
    assert introspect_response.status_code == status.HTTP_200_OK
    introspect_data = introspect_response.json()
    assert introspect_data["active"] is True
    assert introspect_data["client_id"] == client_obj.client_id
    assert introspect_data["username"] == "alice"
    assert introspect_data["token_type"] == "access_token"

    revoke_response = client.post(
        "/api/v1/auth/oauth/revoke",
        data={
            "token": token_data["access_token"],
            "token_type_hint": "access_token",
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
        },
    )
    assert revoke_response.status_code == status.HTTP_200_OK

    introspect_after_revoke = client.post(
        "/api/v1/auth/oauth/introspect",
        data={
            "token": token_data["access_token"],
            "token_type_hint": "access_token",
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
        },
    )
    assert introspect_after_revoke.json()["active"] is False

    refresh_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
        },
    )
    assert refresh_response.status_code == status.HTTP_200_OK
    refresh_data = refresh_response.json()
    assert "access_token" in refresh_data
    assert refresh_data["refresh_token"] != token_data["refresh_token"]

    old_refresh_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
        },
    )
    assert old_refresh_response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_public_client_flow(client, db_session, config, make_user, auth_headers):
    """Test the OAuth2 flow for a public client without a client secret."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Public Client",
        redirect_uris=["https://example.com/callback"],
        is_confidential=False,
    )
    await db_session.flush()

    user = await make_user("bob")
    user_headers = auth_headers(user)
    verifier, challenge = _pkce_pair()

    authorize_response = client.get(
        f"/api/v1/auth/oauth/authorize?response_type=code&client_id={client_obj.client_id}"
        f"&redirect_uri=https://example.com/callback&code_challenge={challenge}"
        f"&code_challenge_method=S256",
        headers=user_headers,
        follow_redirects=False,
    )
    assert authorize_response.status_code == status.HTTP_302_FOUND
    auth_code = parse_qs(urlparse(authorize_response.headers["Location"]).query)["code"][0]

    token_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://example.com/callback",
            "client_id": client_obj.client_id,
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == status.HTTP_200_OK
    assert "access_token" in token_response.json()


@pytest.mark.asyncio
async def test_oauth_authorize_requires_authentication(client, db_session, config, make_user, auth_headers):
    """Test that the authorization endpoint requires a logged-in resource owner."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()
    verifier, challenge = _pkce_pair()

    response = client.get(
        f"/api/v1/auth/oauth/authorize?response_type=code&client_id={client_obj.client_id}"
        f"&redirect_uri=https://example.com/callback&code_challenge={challenge}"
        f"&code_challenge_method=S256",
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_oauth_authorize_rejects_invalid_redirect_uri(client, db_session, config, make_user, auth_headers):
    """Test that an unauthorized redirect URI is rejected at the authorize endpoint."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()

    user = await make_user("alice")
    user_headers = auth_headers(user)
    verifier, challenge = _pkce_pair()

    response = client.get(
        f"/api/v1/auth/oauth/authorize?response_type=code&client_id={client_obj.client_id}"
        f"&redirect_uri=https://other.com/callback&code_challenge={challenge}"
        f"&code_challenge_method=S256",
        headers=user_headers,
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_authorize_rejects_missing_code_challenge(client, db_session, config, make_user, auth_headers):
    """Test that PKCE is required at the authorization endpoint."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()

    user = await make_user("alice")
    user_headers = auth_headers(user)

    response = client.get(
        f"/api/v1/auth/oauth/authorize?response_type=code&client_id={client_obj.client_id}"
        f"&redirect_uri=https://example.com/callback",
        headers=user_headers,
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_token_rejects_wrong_pkce_verifier(client, db_session, config, make_user, auth_headers):
    """Test that a mismatched PKCE verifier is rejected at the token endpoint."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()

    user = await make_user("alice")
    user_headers = auth_headers(user)
    _, challenge = _pkce_pair()

    authorize_response = client.get(
        f"/api/v1/auth/oauth/authorize?response_type=code&client_id={client_obj.client_id}"
        f"&redirect_uri=https://example.com/callback&code_challenge={challenge}"
        f"&code_challenge_method=S256",
        headers=user_headers,
        follow_redirects=False,
    )
    auth_code = parse_qs(urlparse(authorize_response.headers["Location"]).query)["code"][0]

    wrong_verifier = secrets.token_urlsafe(64)
    token_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://example.com/callback",
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
            "code_verifier": wrong_verifier,
        },
    )
    assert token_response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_authorization_code_single_use(client, db_session, config, make_user, auth_headers):
    """Test that an authorization code can only be exchanged once."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()

    user = await make_user("alice")
    user_headers = auth_headers(user)
    verifier, challenge = _pkce_pair()

    authorize_response = client.get(
        f"/api/v1/auth/oauth/authorize?response_type=code&client_id={client_obj.client_id}"
        f"&redirect_uri=https://example.com/callback&code_challenge={challenge}"
        f"&code_challenge_method=S256",
        headers=user_headers,
        follow_redirects=False,
    )
    auth_code = parse_qs(urlparse(authorize_response.headers["Location"]).query)["code"][0]

    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": "https://example.com/callback",
        "client_id": client_obj.client_id,
        "client_secret": client_secret,
        "code_verifier": verifier,
    }
    first = client.post("/api/v1/auth/oauth/token", data=data)
    assert first.status_code == status.HTTP_200_OK

    second = client.post("/api/v1/auth/oauth/token", data=data)
    assert second.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_token_rejects_invalid_client(client, db_session, config, make_user, auth_headers):
    """Test that the token endpoint rejects an unknown client."""
    token_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "fake-code",
            "redirect_uri": "https://example.com/callback",
            "client_id": "not-a-real-client",
            "client_secret": "secret",
            "code_verifier": secrets.token_urlsafe(64),
        },
    )
    assert token_response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_oauth_token_rejects_unsupported_grant_type(client, db_session, config, make_user, auth_headers):
    """Test that unsupported grant types are rejected at the token endpoint."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()

    token_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "implicit",
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
        },
    )
    assert token_response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_token_rejects_refresh_without_refresh_grant(client, db_session, config, make_user, auth_headers):
    """Test that a client with only authorization_code cannot use the refresh_token grant."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="No Refresh Client",
        redirect_uris=["https://example.com/callback"],
        grant_types=["authorization_code"],
    )
    await db_session.flush()

    user = await make_user("alice")
    user_headers = auth_headers(user)
    verifier, challenge = _pkce_pair()

    authorize_response = client.get(
        f"/api/v1/auth/oauth/authorize?response_type=code&client_id={client_obj.client_id}"
        f"&redirect_uri=https://example.com/callback&code_challenge={challenge}"
        f"&code_challenge_method=S256",
        headers=user_headers,
        follow_redirects=False,
    )
    assert authorize_response.status_code == status.HTTP_302_FOUND
    auth_code = parse_qs(urlparse(authorize_response.headers["Location"]).query)["code"][0]

    token_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://example.com/callback",
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    assert token_response.status_code == status.HTTP_200_OK
    token_data = token_response.json()
    assert "refresh_token" in token_data

    refresh_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
        },
    )
    assert refresh_response.status_code == status.HTTP_400_BAD_REQUEST
    assert refresh_response.json()["detail"] == "unsupported_grant_type"


@pytest.mark.asyncio
async def test_oauth_introspect_requires_client_authentication(client, db_session, config, make_user, auth_headers):
    """Test that the introspection endpoint rejects unauthenticated requests."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
        grant_types=["authorization_code", "refresh_token"],
    )
    await db_session.flush()

    user = await make_user("alice")
    user_headers = auth_headers(user)
    verifier, challenge = _pkce_pair()

    authorize_response = client.get(
        f"/api/v1/auth/oauth/authorize?response_type=code&client_id={client_obj.client_id}"
        f"&redirect_uri=https://example.com/callback&code_challenge={challenge}"
        f"&code_challenge_method=S256",
        headers=user_headers,
        follow_redirects=False,
    )
    auth_code = parse_qs(urlparse(authorize_response.headers["Location"]).query)["code"][0]

    token_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://example.com/callback",
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
            "code_verifier": verifier,
        },
    )
    token_data = token_response.json()

    introspect_response = client.post(
        "/api/v1/auth/oauth/introspect",
        data={
            "token": token_data["access_token"],
            "token_type_hint": "access_token",
        },
    )
    assert introspect_response.status_code == status.HTTP_401_UNAUTHORIZED


# --------------------------------------------------------------------------- #
# Targeted coverage tests for songhive/users/oauth.py
# --------------------------------------------------------------------------- #


def test_is_loopback_host():
    """Exercise the _is_loopback_host helper, including the None/empty branch."""
    assert oauth_client_service._is_loopback_host(None) is False
    assert oauth_client_service._is_loopback_host("") is False
    assert oauth_client_service._is_loopback_host("localhost") is True
    assert oauth_client_service._is_loopback_host("127.0.0.1") is True
    assert oauth_client_service._is_loopback_host("[::1]") is True
    assert oauth_client_service._is_loopback_host("example.com") is False


def test_validate_name_and_redirect_uri_errors():
    """Cover validation error branches in _validate_name and _validate_redirect_uris."""
    with pytest.raises(oauth_client_service.OAuthClientError, match="required"):
        oauth_client_service._validate_name("")

    with pytest.raises(oauth_client_service.OAuthClientError, match="too long"):
        oauth_client_service._validate_name("x" * 129)

    with pytest.raises(oauth_client_service.OAuthClientError, match="At least one"):
        oauth_client_service._validate_redirect_uris([])

    with pytest.raises(oauth_client_service.OAuthClientError, match="must be strings"):
        oauth_client_service._validate_redirect_uris([123])

    with pytest.raises(oauth_client_service.OAuthClientError, match="empty values"):
        oauth_client_service._validate_redirect_uris(["https://example.com/cb", ""])

    long_uri = "https://example.com/" + "x" * 500
    with pytest.raises(oauth_client_service.OAuthClientError, match="cannot exceed"):
        oauth_client_service._validate_redirect_uris([long_uri])

    with pytest.raises(oauth_client_service.OAuthClientError, match="Invalid redirect_uri scheme"):
        oauth_client_service._validate_redirect_uris(["ftp://example.com/cb"])

    with pytest.raises(oauth_client_service.OAuthClientError, match="Invalid redirect_uri:"):
        oauth_client_service._validate_redirect_uris(["https:"])

    with pytest.raises(oauth_client_service.OAuthClientError, match="fragments"):
        oauth_client_service._validate_redirect_uris(["https://example.com/cb#frag"])


def test_validate_grant_types_defaults_and_errors():
    """Cover _validate_grant_types defaults, empty iterators, and type errors."""
    assert oauth_client_service._validate_grant_types(None) == ["authorization_code"]
    assert oauth_client_service._validate_grant_types([]) == ["authorization_code"]
    assert oauth_client_service._validate_grant_types(iter([])) == ["authorization_code"]
    assert oauth_client_service._validate_grant_types([" refresh_token "]) == ["refresh_token"]

    with pytest.raises(oauth_client_service.OAuthClientError, match="must be strings"):
        oauth_client_service._validate_grant_types([123])

    with pytest.raises(oauth_client_service.OAuthClientError, match="Unsupported grant_type"):
        oauth_client_service._validate_grant_types(["implicit"])


@pytest.mark.asyncio
async def test_generate_unique_client_id_collision_fallback(db_session, monkeypatch):
    """Cover the collision fallback in _generate_unique_client_id."""
    monkeypatch.setattr(
        oauth_client_service,
        "get_oauth_client_by_client_id",
        AsyncMock(return_value=MagicMock()),
    )
    with pytest.raises(oauth_client_service.OAuthClientError, match="Could not generate"):
        await oauth_client_service._generate_unique_client_id(db_session)


def test_encode_decode_json_and_parse_expires_at():
    """Cover _encode_json, _decode_json, and _parse_expires_at edge cases."""
    now = datetime.now(timezone.utc)
    encoded = oauth_client_service._encode_json({"dt": now})
    assert now.isoformat() in encoded

    with pytest.raises(TypeError):
        oauth_client_service._encode_json({"value": object()})

    assert oauth_client_service._decode_json("{invalid") is None
    assert oauth_client_service._decode_json(None) is None

    assert oauth_client_service._parse_expires_at(None) is None
    assert oauth_client_service._parse_expires_at("not-a-date") is None
    parsed = oauth_client_service._parse_expires_at("2020-01-01T00:00:00")
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_check_client_secret_empty_and_public_client(db_session, make_user):
    """Cover check_client_secret with missing hash or empty secret."""
    admin = await make_user("admin", role="admin")
    confidential, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Confidential",
        redirect_uris=["https://example.com/cb"],
    )
    assert not oauth_client_service.check_client_secret(confidential, "")

    public, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Public",
        redirect_uris=["https://example.com/cb"],
        is_confidential=False,
    )
    assert not oauth_client_service.check_client_secret(public, "secret")


@pytest.mark.asyncio
async def test_delete_oauth_client_missing(db_session):
    """Cover delete_oauth_client when the client does not exist."""
    result = await oauth_client_service.delete_oauth_client(db_session, "missing-client-id")
    assert result is False


def test_verify_pkce_branches():
    """Cover all _verify_pkce error branches, including plain method and S256."""
    verifier, challenge = _pkce_pair()
    oauth_client_service._verify_pkce(challenge, "S256", verifier)

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Unsupported code challenge method"):
        oauth_client_service._verify_pkce(challenge, "invalid", verifier)

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid code challenge"):
        oauth_client_service._verify_pkce("short", "S256", verifier)

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid code verifier"):
        oauth_client_service._verify_pkce(challenge, "S256", "short")

    wrong_verifier = secrets.token_urlsafe(64)
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Code challenge failed"):
        oauth_client_service._verify_pkce(challenge, "S256", wrong_verifier)

    plain_verifier = secrets.token_urlsafe(64)
    plain_challenge = secrets.token_urlsafe(64)
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Code challenge failed"):
        oauth_client_service._verify_pkce(plain_challenge, "plain", plain_verifier)


@pytest.mark.asyncio
async def test_create_authorization_code_branches(db_session, fake_redis, make_user):
    """Cover authorization-code creation validation branches."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Auth Code Client",
        redirect_uris=["https://example.com/cb"],
    )
    user = await make_user("alice")
    verifier, challenge = _pkce_pair()
    redirect_uri = client_obj.redirect_uris[0]

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Unsupported response type"):
        await oauth_client_service.create_authorization_code(
            db_session,
            fake_redis,
            user,
            "token",
            client_obj.client_id,
            redirect_uri,
            code_challenge=challenge,
            code_challenge_method="S256",
        )

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid client"):
        await oauth_client_service.create_authorization_code(
            db_session,
            fake_redis,
            user,
            "code",
            "missing-client-id",
            redirect_uri,
            code_challenge=challenge,
            code_challenge_method="S256",
        )

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Missing code_challenge"):
        await oauth_client_service.create_authorization_code(
            db_session,
            fake_redis,
            user,
            "code",
            client_obj.client_id,
            redirect_uri,
            code_challenge=None,
            code_challenge_method="S256",
        )

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Unsupported code challenge method"):
        await oauth_client_service.create_authorization_code(
            db_session,
            fake_redis,
            user,
            "code",
            client_obj.client_id,
            redirect_uri,
            code_challenge=challenge,
            code_challenge_method="invalid",
        )

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid code challenge"):
        await oauth_client_service.create_authorization_code(
            db_session,
            fake_redis,
            user,
            "code",
            client_obj.client_id,
            redirect_uri,
            code_challenge="short",
            code_challenge_method="S256",
        )

    code, _ = await oauth_client_service.create_authorization_code(
        db_session,
        fake_redis,
        user,
        "code",
        client_obj.client_id,
        redirect_uri,
        code_challenge=challenge,
        code_challenge_method="",
    )
    assert code


@pytest.mark.asyncio
async def test_get_oauth_token_hints_and_expiry(fake_redis, config):
    """Cover _get_oauth_token token_type_hint branches and expiry cleanup."""
    client = OAuth2Client(client_id="hint-client", name="Hint Client")
    access_data = oauth_client_service._token_payload("access_token", client, "user-1", None, config)
    refresh_data = oauth_client_service._token_payload("refresh_token", client, "user-1", None, config)

    await fake_redis.set(
        oauth_client_service._access_token_key("access-hint"),
        oauth_client_service._encode_json(access_data),
    )
    await fake_redis.set(
        oauth_client_service._refresh_token_key("refresh-hint"),
        oauth_client_service._encode_json(refresh_data),
    )

    got = await oauth_client_service._get_oauth_token(fake_redis, "access-hint", "access_token")
    assert got["token_type"] == "access_token"

    got = await oauth_client_service._get_oauth_token(fake_redis, "refresh-hint", "refresh_token")
    assert got["token_type"] == "refresh_token"

    got = await oauth_client_service._get_oauth_token(fake_redis, "access-hint")
    assert got["token_type"] == "access_token"

    got = await oauth_client_service._get_oauth_token(fake_redis, "refresh-hint")
    assert got["token_type"] == "refresh_token"

    expired = dict(access_data)
    expired["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    await fake_redis.set(
        oauth_client_service._access_token_key("expired-hint"),
        oauth_client_service._encode_json(expired),
    )
    assert await oauth_client_service._get_oauth_token(fake_redis, "expired-hint") is None
    assert await fake_redis.get(oauth_client_service._access_token_key("expired-hint")) is None


@pytest.mark.asyncio
async def test_consume_authorization_code_error_branches(db_session, fake_redis, make_user):
    """Cover _consume_authorization_code validation and expiry branches."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Consume Client",
        redirect_uris=["https://example.com/cb"],
    )
    user = await make_user("alice")
    verifier, challenge = _pkce_pair()
    redirect_uri = client_obj.redirect_uris[0]
    code = "consume-code"

    async def _store(payload):
        await fake_redis.set(
            oauth_client_service._authz_code_key(code),
            oauth_client_service._encode_json(payload),
        )

    payload = {
        "client_id": client_obj.client_id,
        "redirect_uri": redirect_uri,
        "user_id": user.id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": None,
        "state": None,
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
    }
    await _store(payload)
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid authorization code"):
        await oauth_client_service._consume_authorization_code(fake_redis, client_obj, code, redirect_uri, verifier)

    payload["client_id"] = "other-client"
    payload["expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    await _store(payload)
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid authorization code"):
        await oauth_client_service._consume_authorization_code(fake_redis, client_obj, code, redirect_uri, verifier)

    payload["client_id"] = client_obj.client_id
    payload["redirect_uri"] = "https://other.com/cb"
    await _store(payload)
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid redirect URI"):
        await oauth_client_service._consume_authorization_code(fake_redis, client_obj, code, redirect_uri, verifier)


@pytest.mark.asyncio
async def test_consume_authorization_code_watch_error(db_session, fake_redis, make_user, monkeypatch):
    """Cover the WatchError handling in _consume_authorization_code."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Watch Client",
        redirect_uris=["https://example.com/cb"],
    )
    user = await make_user("alice")
    verifier, challenge = _pkce_pair()
    redirect_uri = client_obj.redirect_uris[0]
    code = "watch-authz"

    payload = {
        "client_id": client_obj.client_id,
        "redirect_uri": redirect_uri,
        "user_id": user.id,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": None,
        "state": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    await fake_redis.set(
        oauth_client_service._authz_code_key(code),
        oauth_client_service._encode_json(payload),
    )

    class FakePipe:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def watch(self, key):
            pass

        async def get(self, key):
            return await fake_redis.get(key)

        async def reset(self):
            pass

        def multi(self):
            pass

        def delete(self, key):
            pass

        async def execute(self):
            raise WatchError()

    monkeypatch.setattr(fake_redis, "pipeline", lambda transaction=True: FakePipe())
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid authorization code"):
        await oauth_client_service._consume_authorization_code(fake_redis, client_obj, code, redirect_uri, verifier)


@pytest.mark.asyncio
async def test_consume_refresh_token_error_branches(db_session, fake_redis, make_user):
    """Cover _consume_refresh_token validation and expiry branches."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Refresh Client",
        redirect_uris=["https://example.com/cb"],
        grant_types=["authorization_code", "refresh_token"],
    )
    user = await make_user("alice")
    token = "consume-refresh"

    async def _store(payload):
        await fake_redis.set(
            oauth_client_service._refresh_token_key(token),
            oauth_client_service._encode_json(payload),
        )

    payload = {
        "client_id": client_obj.client_id,
        "token_type": "refresh_token",
        "user_id": user.id,
        "scope": None,
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
    }
    await _store(payload)
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid refresh token"):
        await oauth_client_service._consume_refresh_token(fake_redis, client_obj, token)

    payload["client_id"] = "other-client"
    payload["expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    await _store(payload)
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid refresh token"):
        await oauth_client_service._consume_refresh_token(fake_redis, client_obj, token)

    payload["client_id"] = client_obj.client_id
    payload["token_type"] = "access_token"
    await _store(payload)
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid refresh token"):
        await oauth_client_service._consume_refresh_token(fake_redis, client_obj, token)


@pytest.mark.asyncio
async def test_consume_refresh_token_watch_error(db_session, fake_redis, make_user, monkeypatch):
    """Cover the WatchError handling in _consume_refresh_token."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Watch Refresh Client",
        redirect_uris=["https://example.com/cb"],
        grant_types=["authorization_code", "refresh_token"],
    )
    user = await make_user("alice")
    token = "watch-refresh"

    payload = {
        "client_id": client_obj.client_id,
        "token_type": "refresh_token",
        "user_id": user.id,
        "scope": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    await fake_redis.set(
        oauth_client_service._refresh_token_key(token),
        oauth_client_service._encode_json(payload),
    )

    class FakePipe:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def watch(self, key):
            pass

        async def get(self, key):
            return await fake_redis.get(key)

        async def reset(self):
            pass

        def multi(self):
            pass

        def delete(self, key):
            pass

        async def execute(self):
            raise WatchError()

    monkeypatch.setattr(fake_redis, "pipeline", lambda transaction=True: FakePipe())
    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Invalid refresh token"):
        await oauth_client_service._consume_refresh_token(fake_redis, client_obj, token)


@pytest.mark.asyncio
async def test_authenticate_client_confidential_and_public(db_session, make_user):
    """Cover _authenticate_client branches for wrong/empty secrets and public clients."""
    admin = await make_user("admin", role="admin")
    confidential, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Confidential Auth",
        redirect_uris=["https://example.com/cb"],
    )
    public, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Public Auth",
        redirect_uris=["https://example.com/cb"],
        is_confidential=False,
    )

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Client authentication failed"):
        await oauth_client_service._authenticate_client(db_session, confidential.client_id, "wrong-secret")

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Client authentication failed"):
        await oauth_client_service._authenticate_client(db_session, confidential.client_id, "")

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Client authentication failed"):
        await oauth_client_service._authenticate_client(db_session, public.client_id, "any-secret")


@pytest.mark.asyncio
async def test_create_token_authorization_code_missing_params(db_session, fake_redis, config, make_user):
    """Cover _create_token_from_authorization_code missing-parameter branches."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Missing Params Client",
        redirect_uris=["https://example.com/cb"],
    )
    await db_session.flush()

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Missing authorization code"):
        await oauth_client_service.create_token(
            db_session,
            fake_redis,
            config,
            "authorization_code",
            client_obj.client_id,
            client_secret,
        )

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Missing redirect URI"):
        await oauth_client_service.create_token(
            db_session,
            fake_redis,
            config,
            "authorization_code",
            client_obj.client_id,
            client_secret,
            code="fake-code",
        )

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Missing code verifier"):
        await oauth_client_service.create_token(
            db_session,
            fake_redis,
            config,
            "authorization_code",
            client_obj.client_id,
            client_secret,
            code="fake-code",
            redirect_uri="https://example.com/cb",
        )


@pytest.mark.asyncio
async def test_create_token_client_credentials_unsupported(db_session, fake_redis, config, make_user):
    """Cover the unsupported grant-type branch for client_credentials."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Client Credentials Client",
        redirect_uris=["https://example.com/cb"],
        grant_types=["client_credentials"],
    )
    await db_session.flush()

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Unsupported grant type"):
        await oauth_client_service.create_token(
            db_session,
            fake_redis,
            config,
            "client_credentials",
            client_obj.client_id,
            client_secret,
        )


@pytest.mark.asyncio
async def test_create_token_user_inactive_after_authorization(db_session, fake_redis, config, make_user):
    """Cover the user-inactive branch in _create_token_from_authorization_code."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Inactive Authz Client",
        redirect_uris=["https://example.com/cb"],
    )
    user = await make_user("alice")
    verifier, challenge = _pkce_pair()
    redirect_uri = client_obj.redirect_uris[0]

    code, _ = await oauth_client_service.create_authorization_code(
        db_session,
        fake_redis,
        user,
        "code",
        client_obj.client_id,
        redirect_uri,
        code_challenge=challenge,
        code_challenge_method="S256",
    )

    user.is_active = False
    await db_session.flush()

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="User not active"):
        await oauth_client_service.create_token(
            db_session,
            fake_redis,
            config,
            "authorization_code",
            client_obj.client_id,
            client_secret,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=verifier,
        )


@pytest.mark.asyncio
async def test_create_token_missing_refresh_token(db_session, fake_redis, config, make_user):
    """Cover the missing refresh token branch in _create_token_from_refresh_token."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Refresh Only Client",
        redirect_uris=["https://example.com/cb"],
        grant_types=["refresh_token"],
    )
    await db_session.flush()

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="Missing refresh token"):
        await oauth_client_service.create_token(
            db_session,
            fake_redis,
            config,
            "refresh_token",
            client_obj.client_id,
            client_secret,
        )


@pytest.mark.asyncio
async def test_create_token_refresh_user_inactive(db_session, fake_redis, config, make_user):
    """Cover the user-inactive branch in _create_token_from_refresh_token."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Refresh Inactive Client",
        redirect_uris=["https://example.com/cb"],
        grant_types=["authorization_code", "refresh_token"],
    )
    user = await make_user("alice")
    await db_session.flush()

    token_pair = await oauth_client_service._issue_oauth_token_pair(fake_redis, config, client_obj, user.id, None)

    user.is_active = False
    await db_session.flush()

    with pytest.raises(oauth_client_service.OAuth2ProviderError, match="User not active"):
        await oauth_client_service.create_token(
            db_session,
            fake_redis,
            config,
            "refresh_token",
            client_obj.client_id,
            client_secret,
            refresh_token=token_pair["refresh_token"],
        )


@pytest.mark.asyncio
async def test_revoke_token_branches(db_session, fake_redis, config, make_user):
    """Cover _revoke_token_by_client_id and empty-token branches."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Revoke Client",
        redirect_uris=["https://example.com/cb"],
        grant_types=["authorization_code", "refresh_token"],
    )
    user = await make_user("alice")
    await db_session.flush()

    token_pair = await oauth_client_service._issue_oauth_token_pair(fake_redis, config, client_obj, user.id, None)

    await oauth_client_service.revoke_token(
        db_session,
        fake_redis,
        token_pair["access_token"],
        "access_token",
        client_obj.client_id,
        client_secret,
    )
    assert await fake_redis.get(oauth_client_service._access_token_key(token_pair["access_token"])) is None

    await oauth_client_service.revoke_token(
        db_session,
        fake_redis,
        token_pair["refresh_token"],
        "refresh_token",
        client_obj.client_id,
        client_secret,
    )
    assert await fake_redis.get(oauth_client_service._refresh_token_key(token_pair["refresh_token"])) is None

    await oauth_client_service.revoke_token(
        db_session,
        fake_redis,
        "any-token",
        "access_token",
        client_obj.client_id,
        "wrong-secret",
    )

    await oauth_client_service.revoke_token(
        db_session,
        fake_redis,
        "",
        "access_token",
        client_obj.client_id,
        client_secret,
    )


@pytest.mark.asyncio
async def test_revoke_token_without_client_id(db_session, fake_redis, config, make_user):
    """Cover revoke_token paths when no client_id is provided."""
    admin = await make_user("admin", role="admin")
    client_obj, _ = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Revoke Public Client",
        redirect_uris=["https://example.com/cb"],
        grant_types=["authorization_code", "refresh_token"],
    )
    user = await make_user("alice")
    await db_session.flush()

    token_pair = await oauth_client_service._issue_oauth_token_pair(fake_redis, config, client_obj, user.id, None)

    await oauth_client_service.revoke_token(db_session, fake_redis, token_pair["access_token"], "access_token")
    assert await fake_redis.get(oauth_client_service._access_token_key(token_pair["access_token"])) is None

    token_pair = await oauth_client_service._issue_oauth_token_pair(fake_redis, config, client_obj, user.id, None)
    await oauth_client_service.revoke_token(db_session, fake_redis, token_pair["refresh_token"], "refresh_token")
    assert await fake_redis.get(oauth_client_service._refresh_token_key(token_pair["refresh_token"])) is None

    await oauth_client_service.revoke_token(db_session, fake_redis, "unknown-access", "access_token")
    await oauth_client_service.revoke_token(db_session, fake_redis, "unknown-refresh", "refresh_token")
    await oauth_client_service.revoke_token(db_session, fake_redis, "unknown-none", None)


@pytest.mark.asyncio
async def test_introspect_token_branches(db_session, fake_redis, config, make_user):
    """Cover introspect_token branches for client mismatch, user, and expiry."""
    admin = await make_user("admin", role="admin")
    client_a, secret_a = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Introspect Client A",
        redirect_uris=["https://a.com/cb"],
        grant_types=["authorization_code", "refresh_token"],
    )
    client_b, secret_b = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Introspect Client B",
        redirect_uris=["https://b.com/cb"],
        grant_types=["authorization_code", "refresh_token"],
    )
    user = await make_user("alice")
    await db_session.flush()

    token_pair = await oauth_client_service._issue_oauth_token_pair(fake_redis, config, client_a, user.id, None)
    access_token = token_pair["access_token"]

    result = await oauth_client_service.introspect_token(
        db_session, fake_redis, access_token, "access_token", client_a.client_id, secret_a
    )
    assert result["active"] is True

    result = await oauth_client_service.introspect_token(
        db_session, fake_redis, access_token, "access_token", client_b.client_id, secret_b
    )
    assert result == {"active": False}

    payload_no_user = {
        "token_type": "access_token",
        "client_id": client_a.client_id,
        "scope": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    await fake_redis.set(
        oauth_client_service._access_token_key("no-user-token"),
        oauth_client_service._encode_json(payload_no_user),
    )
    result = await oauth_client_service.introspect_token(
        db_session, fake_redis, "no-user-token", "access_token", client_a.client_id, secret_a
    )
    assert result == {"active": False}

    user.is_active = False
    await db_session.flush()
    result = await oauth_client_service.introspect_token(
        db_session, fake_redis, access_token, "access_token", client_a.client_id, secret_a
    )
    assert result == {"active": False}

    user.is_active = True
    await db_session.flush()

    expired_payload = oauth_client_service._token_payload("access_token", client_a, user.id, None, config)
    expired_payload["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    await fake_redis.set(
        oauth_client_service._access_token_key("expired-token"),
        oauth_client_service._encode_json(expired_payload),
    )
    result = await oauth_client_service.introspect_token(
        db_session, fake_redis, "expired-token", "access_token", client_a.client_id, secret_a
    )
    assert result == {"active": False}

    no_exp_payload = oauth_client_service._token_payload("access_token", client_a, user.id, None, config)
    no_exp_payload.pop("expires_at")
    await fake_redis.set(
        oauth_client_service._access_token_key("no-exp-token"),
        oauth_client_service._encode_json(no_exp_payload),
    )
    result = await oauth_client_service.introspect_token(
        db_session, fake_redis, "no-exp-token", "access_token", client_a.client_id, secret_a
    )
    assert result == {"active": False}


@pytest.mark.asyncio
async def test_introspect_token_expired_after_get(db_session, fake_redis, config, make_user, monkeypatch):
    """Cover the defensive expiry branch in introspect_token after _get_oauth_token."""
    admin = await make_user("admin", role="admin")
    client_obj, client_secret = await oauth_client_service.create_oauth_client(
        db_session,
        created_by=admin.id,
        name="Introspect Expired Client",
        redirect_uris=["https://expired.com/cb"],
        grant_types=["authorization_code", "refresh_token"],
    )
    user = await make_user("alice")
    await db_session.flush()

    payload = {
        "token_type": "access_token",
        "client_id": client_obj.client_id,
        "user_id": user.id,
        "scope": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }
    monkeypatch.setattr(
        oauth_client_service,
        "_get_oauth_token",
        AsyncMock(return_value=payload),
    )

    result = await oauth_client_service.introspect_token(
        db_session,
        fake_redis,
        "expired-after-get",
        "access_token",
        client_obj.client_id,
        client_secret,
    )
    assert result == {"active": False}
