"""
OAuth2 client registration, admin API, and provider flow tests.
"""

import base64
import hashlib
import secrets
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import status
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
    assert refresh_response.json() == {"detail": "unsupported_grant_type"}


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
