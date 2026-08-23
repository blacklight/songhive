"""
API token service, middleware, and route tests.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from songhive.api.middleware.auth import create_api_token_jwt, decode_token_payload
from songhive.models.api_token import ApiToken
from songhive.models.audit_log import AuditLog
from songhive.users.api_tokens import (
    ApiTokenError,
    count_user_api_tokens,
    get_api_token_by_jti,
    issue_api_token,
    list_user_api_tokens,
    revoke_api_token,
    validate_api_token,
)


@pytest.fixture
async def api_token_pair(db_session, config, regular_user):
    """Issue an API token for ``regular_user`` and return the row and raw JWT."""
    token, raw_jwt = await issue_api_token(
        db_session,
        regular_user,
        config,
        name="test-token",
        expires_at=None,
    )
    return token, raw_jwt


@pytest.fixture
def api_token_headers(api_token_pair):
    """Return an Authorization header with a valid API-token JWT."""
    _, raw_jwt = api_token_pair
    return {"Authorization": f"Bearer {raw_jwt}"}


# Service tests


@pytest.mark.asyncio
async def test_issue_api_token_returns_jwt_and_orm(db_session, config, regular_user):
    """Issuing a token returns a JWT whose jti matches the DB row."""
    token, raw_jwt = await issue_api_token(db_session, regular_user, config, "cli", None)

    payload = decode_token_payload(raw_jwt, config.auth.secret_key)
    assert payload is not None
    assert payload.get("token_type") == "api_token"
    assert payload.get("jti") == token.jti
    assert payload.get("sub") == regular_user.id


@pytest.mark.asyncio
async def test_issue_api_token_duplicate_name_raises(db_session, config, regular_user):
    """Two tokens with the same name for the same user raise ApiTokenError 409."""
    await issue_api_token(db_session, regular_user, config, "cli", None)

    with pytest.raises(ApiTokenError) as exc_info:
        await issue_api_token(db_session, regular_user, config, "cli", None)
    assert exc_info.value.status_code == 409
    assert "already in use" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_issue_api_token_duplicate_name_different_users_ok(db_session, config, regular_user, other_user):
    """Different users can share the same token name."""
    token_a, _ = await issue_api_token(db_session, regular_user, config, "cli", None)
    token_b, _ = await issue_api_token(db_session, other_user, config, "cli", None)
    assert token_a.name == token_b.name == "cli"
    assert token_a.user_id != token_b.user_id


@pytest.mark.asyncio
async def test_validate_api_token_returns_none_when_revoked(db_session, config, regular_user):
    """A revoked token is not valid."""
    token, _ = await issue_api_token(db_session, regular_user, config, "revoked", None)
    await revoke_api_token(db_session, token.id, regular_user.id)
    assert await validate_api_token(db_session, token.jti) is None


@pytest.mark.asyncio
async def test_validate_api_token_returns_none_when_expired(db_session, config, regular_user):
    """A token whose expires_at is in the past is not valid."""
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    token, _ = await issue_api_token(db_session, regular_user, config, "expired", past)
    assert await validate_api_token(db_session, token.jti) is None


@pytest.mark.asyncio
async def test_validate_api_token_returns_none_when_unknown_jti(db_session):
    """A random jti does not resolve to a token."""
    assert await validate_api_token(db_session, "not-a-real-jti") is None


@pytest.mark.asyncio
async def test_validate_api_token_updates_last_used_at(db_session, config, regular_user):
    """A successful validation sets last_used_at."""
    token, _ = await issue_api_token(db_session, regular_user, config, "used", None)
    assert token.last_used_at is None

    validated = await validate_api_token(db_session, token.jti)
    assert validated is not None
    assert validated.last_used_at is not None

    refreshed = await db_session.execute(select(ApiToken).where(ApiToken.id == token.id))
    db_token = refreshed.scalar_one()
    assert db_token.last_used_at is not None


@pytest.mark.asyncio
async def test_validate_api_token_never_expires(db_session, config, regular_user):
    """A token with expires_at=None stays valid."""
    token, _ = await issue_api_token(db_session, regular_user, config, "never", None)
    validated = await validate_api_token(db_session, token.jti)
    assert validated is not None
    assert validated.id == token.id


@pytest.mark.asyncio
async def test_revoke_api_token_wrong_user_returns_false(db_session, config, regular_user, other_user):
    """One user cannot revoke another user's token."""
    token, _ = await issue_api_token(db_session, regular_user, config, "protected", None)
    assert await revoke_api_token(db_session, token.id, other_user.id) is False

    validated = await validate_api_token(db_session, token.jti)
    assert validated is not None


@pytest.mark.asyncio
async def test_revoke_api_token_idempotent_after_revoked(db_session, config, regular_user):
    """Revoking an already-revoked token still returns True but is_active is False."""
    token, _ = await issue_api_token(db_session, regular_user, config, "double", None)
    assert await revoke_api_token(db_session, token.id, regular_user.id) is True
    assert await validate_api_token(db_session, token.jti) is None

    assert await revoke_api_token(db_session, token.id, regular_user.id) is True
    refreshed = await db_session.execute(select(ApiToken).where(ApiToken.id == token.id))
    db_token = refreshed.scalar_one()
    assert db_token.is_active is False


@pytest.mark.asyncio
async def test_get_api_token_by_jti(db_session, config, regular_user):
    """get_api_token_by_jti returns the token or None."""
    token, _ = await issue_api_token(db_session, regular_user, config, "lookup", None)
    found = await get_api_token_by_jti(db_session, token.jti)
    assert found is not None
    assert found.id == token.id
    assert await get_api_token_by_jti(db_session, "nope") is None


@pytest.mark.asyncio
async def test_list_user_api_tokens(db_session, config, regular_user, other_user):
    """list_user_api_tokens only returns the requested user's tokens."""
    a, _ = await issue_api_token(db_session, regular_user, config, "a", None)
    b, _ = await issue_api_token(db_session, regular_user, config, "b", None)
    await issue_api_token(db_session, other_user, config, "c", None)

    items = await list_user_api_tokens(db_session, regular_user.id)
    assert [t.id for t in items] == [b.id, a.id]


@pytest.mark.asyncio
async def test_count_user_api_tokens(db_session, config, regular_user, other_user):
    """count_user_api_tokens only counts the requested user's tokens."""
    await issue_api_token(db_session, regular_user, config, "a", None)
    await issue_api_token(db_session, regular_user, config, "b", None)
    await issue_api_token(db_session, other_user, config, "c", None)
    assert await count_user_api_tokens(db_session, regular_user.id) == 2
    assert await count_user_api_tokens(db_session, other_user.id) == 1


# Middleware / deps tests


def test_api_token_authenticates_request(client, regular_user, api_token_headers):
    """An API-token JWT authenticates a protected request."""
    response = client.get("/api/v1/users/me", headers=api_token_headers)
    assert response.status_code == 200
    assert response.json()["id"] == regular_user.id
    assert response.json()["username"] == regular_user.username


def test_api_token_revoked_returns_401(client, auth_headers, api_token_pair, api_token_headers, regular_user):
    """A revoked API token returns 401."""
    token, _ = api_token_pair
    revoke_response = client.delete(
        f"/api/v1/auth/api-tokens/{token.id}",
        headers=auth_headers(regular_user),
    )
    assert revoke_response.status_code == 200

    response = client.get("/api/v1/users/me", headers=api_token_headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_api_token_expired_returns_401(client, db_session, config, regular_user):
    """An expired API-token JWT returns 401."""
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    jti = "expired-jti"
    db_token = ApiToken(
        user_id=regular_user.id,
        jti=jti,
        name="expired",
        expires_at=past,
    )
    db_session.add(db_token)
    await db_session.flush()

    raw_jwt = create_api_token_jwt(regular_user.id, config.auth.secret_key, jti, past)
    response = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {raw_jwt}"})
    assert response.status_code == 401


def test_login_access_token_still_works(client, regular_user, auth_headers):
    """A short-lived access token still authenticates."""
    response = client.get("/api/v1/users/me", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["id"] == regular_user.id


def test_invalid_token_returns_401(client):
    """A random Bearer token returns 401."""
    response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 401


# Route tests


def test_create_api_token_returns_jwt(client, config, auth_headers, regular_user):
    """POST creates an API token and returns the raw JWT once."""
    response = client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(regular_user),
        json={"name": "cli"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"]
    assert body["name"] == "cli"
    assert body["token"]
    assert "expires_at" in body
    assert "created_at" in body

    token = body["token"]
    payload = decode_token_payload(token, config.auth.secret_key)
    assert payload is not None
    assert payload.get("token_type") == "api_token"


def test_create_api_token_no_expiry(client, auth_headers, regular_user):
    """POST with expires_at: null creates a token that never expires."""
    response = client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(regular_user),
        json={"name": "forever", "expires_at": None},
    )
    assert response.status_code == 201
    assert response.json()["expires_at"] is None


def test_create_api_token_past_expiry_rejected(client, auth_headers, regular_user):
    """POST with expires_at in the past returns 422."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    response = client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(regular_user),
        json={"name": "stale", "expires_at": past},
    )
    assert response.status_code == 422


def test_create_api_token_duplicate_name_409(client, auth_headers, regular_user):
    """POST with a duplicate name returns 409."""
    client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(regular_user),
        json={"name": "dup"},
    )
    response = client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(regular_user),
        json={"name": "dup"},
    )
    assert response.status_code == 409


def test_list_api_tokens_no_token_in_response(client, auth_headers, regular_user):
    """GET lists tokens without exposing the raw JWT."""
    create_response = client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(regular_user),
        json={"name": "listable"},
    )
    assert create_response.status_code == 201

    response = client.get("/api/v1/auth/api-tokens", headers=auth_headers(regular_user))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert "token" not in body["items"][0]
    assert body["items"][0]["name"] == "listable"
    assert body["items"][0]["is_active"] is True


def test_list_api_tokens_only_own_tokens(client, auth_headers, regular_user, other_user):
    """GET only returns tokens belonging to the caller."""
    client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(regular_user),
        json={"name": "mine"},
    )
    client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(other_user),
        json={"name": "yours"},
    )

    response = client.get("/api/v1/auth/api-tokens", headers=auth_headers(regular_user))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "mine"


def test_delete_api_token_revokes_it(client, auth_headers, api_token_pair, api_token_headers, regular_user):
    """DELETE revokes the token, which then returns 401."""
    token, _ = api_token_pair

    # Sanity: token works before deletion.
    before = client.get("/api/v1/users/me", headers=api_token_headers)
    assert before.status_code == 200

    response = client.delete(
        f"/api/v1/auth/api-tokens/{token.id}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    after = client.get("/api/v1/users/me", headers=api_token_headers)
    assert after.status_code == 401


def test_delete_api_token_other_user_404(client, auth_headers, api_token_pair, api_token_headers, other_user):
    """A user cannot delete another user token."""
    token, _ = api_token_pair

    response = client.delete(
        f"/api/v1/auth/api-tokens/{token.id}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 404

    after = client.get("/api/v1/users/me", headers=api_token_headers)
    assert after.status_code == 200


def test_unauthenticated_create_returns_401(client):
    """POST without authentication returns 401."""
    response = client.post("/api/v1/auth/api-tokens", json={"name": "nope"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_api_token_writes_audit_log(client, auth_headers, db_session, regular_user):
    """POST writes an api_token.create AuditLog row."""
    response = client.post(
        "/api/v1/auth/api-tokens",
        headers=auth_headers(regular_user),
        json={"name": "audited"},
    )
    assert response.status_code == 201
    token_id = response.json()["id"]

    logs = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "api_token.create",
            AuditLog.actor_id == regular_user.id,
            AuditLog.target_id == token_id,
        )
    )
    assert logs.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_revoke_api_token_writes_audit_log(client, auth_headers, db_session, api_token_pair, regular_user):
    """DELETE writes an api_token.revoke AuditLog row."""
    token, _ = api_token_pair

    response = client.delete(
        f"/api/v1/auth/api-tokens/{token.id}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200

    logs = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "api_token.revoke",
            AuditLog.actor_id == regular_user.id,
            AuditLog.target_id == token.id,
        )
    )
    assert logs.scalar_one_or_none() is not None
