"""
Authentication service and middleware tests.
"""

import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException, Request, status

from songhive.api.deps import get_config, get_current_user, get_db, require_admin
from songhive.api.middleware.auth import (
    create_access_token,
    decode_access_token,
    extract_token,
)
from songhive.config.schema import RegistrationMode, SonghiveConfig
from songhive.models.user import User
from songhive.services.auth import (
    create_user,
    get_user_by_email,
    get_user_by_username,
    get_user_by_username_or_email,
    hash_password,
    verify_password,
)
from songhive.users import oauth as oauth_service

SECRET_KEY = "a" * 32


def test_hash_password_and_verify():
    """Test that password hashing and verification work together."""
    password = "super-secret"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed) is True


def test_verify_password_with_wrong_password():
    """Test that an incorrect password does not verify."""
    hashed = hash_password("correct-password")
    assert verify_password("wrong-password", hashed) is False


def test_hash_password_uses_bcrypt():
    """Test that hashed passwords are bcrypt hashes."""
    hashed = hash_password("password")
    assert hashed.startswith(("$2a$", "$2b$", "$2y$"))


def test_hash_password_different_salts():
    """Test that hashing the same password twice yields different hashes."""
    password = "password"
    assert hash_password(password) != hash_password(password)


@pytest.mark.asyncio
async def test_create_user(db_session):
    """Test creating a user through the auth service."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    assert user.username == "alice"
    assert user.email == "alice@example.com"
    assert user.id is not None
    assert user.is_admin is False
    assert verify_password("secret", user.password_hash)


@pytest.mark.asyncio
async def test_create_user_admin_role(db_session):
    """Test that create_user respects the role argument."""
    user = await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_create_user_moderator_role(db_session):
    """Test that create_user accepts the moderator role."""
    user = await create_user(db_session, "mod", "mod@example.com", "secret", role="moderator")
    assert user.role == "moderator"
    assert user.is_admin is False


@pytest.mark.asyncio
async def test_create_user_rejects_invalid_role(db_session):
    """Test that create_user rejects an unknown role."""
    with pytest.raises(ValueError, match="Invalid role"):
        await create_user(db_session, "hacker", "hacker@example.com", "secret", role="superuser")


@pytest.mark.asyncio
async def test_get_user_by_username(db_session):
    """Test fetching a user by username."""
    await create_user(db_session, "alice", "alice@example.com", "secret")
    found = await get_user_by_username(db_session, "alice")
    assert found is not None
    assert found.username == "alice"


@pytest.mark.asyncio
async def test_get_user_by_username_missing(db_session):
    """Test that a missing username returns None."""
    assert await get_user_by_username(db_session, "nobody") is None


@pytest.mark.asyncio
async def test_get_user_by_email(db_session):
    """Test fetching a user by email."""
    await create_user(db_session, "bob", "bob@example.com", "secret")
    found = await get_user_by_email(db_session, "bob@example.com")
    assert found is not None
    assert found.username == "bob"


@pytest.mark.asyncio
async def test_get_user_by_email_missing(db_session):
    """Test that a missing email returns None."""
    assert await get_user_by_email(db_session, "missing@example.com") is None


def test_create_access_token_decodes():
    """Test that a created token decodes back to the user id."""
    token = create_access_token("user-123", SECRET_KEY)
    assert decode_access_token(token, SECRET_KEY) == "user-123"


def test_decode_expired_token_returns_none():
    """Test that an expired token is rejected."""
    token = create_access_token("user-123", SECRET_KEY, expires_minutes=-10)
    assert decode_access_token(token, SECRET_KEY) is None


def test_decode_invalid_token_returns_none():
    """Test that a malformed token is rejected."""
    assert decode_access_token("not.a.valid.token", SECRET_KEY) is None


def test_decode_wrong_secret_returns_none():
    """Test that a token decoded with the wrong secret is rejected."""
    token = create_access_token("user-123", SECRET_KEY)
    assert decode_access_token(token, "b" * 32) is None


def test_create_access_token_without_expiry():
    """Test creating a token with no expiry."""
    token = create_access_token("user-123", SECRET_KEY, expires_minutes=None)
    assert decode_access_token(token, SECRET_KEY) == "user-123"


def test_extract_token_from_header():
    """Test extracting a Bearer token from the Authorization header."""
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer abc123")],
            "query_string": b"",
        }
    )
    assert extract_token(request) == "abc123"


def test_extract_token_missing():
    """Test that extract_token returns None when no token is present."""
    request = Request({"type": "http", "headers": [], "query_string": b""})
    assert extract_token(request) is None


def test_get_config_returns_app_config():
    """Test that get_config returns the config stored in app state."""
    config = SonghiveConfig()
    app = FastAPI()
    app.state.config = config
    request = Request({"type": "http", "app": app})
    assert get_config(request) is config


def _make_request_with_token(
    token: str | None = None,
    config: SonghiveConfig | None = None,
) -> Request:
    """Build a request with the given app config and optional bearer token."""
    if config is None:
        config = SonghiveConfig(auth={"secret_key": SECRET_KEY})
    app = FastAPI()
    app.state.config = config
    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return Request({"type": "http", "app": app, "headers": headers, "query_string": b""})


@pytest.mark.asyncio
async def test_get_current_user_raises_401():
    """Test that get_current_user raises 401 for an unauthenticated request."""
    request = Request({"type": "http", "headers": [], "query_string": b""})
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_with_valid_token(db_session):
    """Test that get_current_user returns the user for a valid token."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    config = SonghiveConfig(auth={"secret_key": SECRET_KEY})
    token = create_access_token(user.id, config.auth.secret_key)
    request = _make_request_with_token(token, config=config)
    result = await get_current_user(request, db=db_session)

    assert result.id == user.id
    assert result.username == "alice"


@pytest.mark.asyncio
async def test_get_current_user_invalid_token(db_session):
    """Test that get_current_user rejects an invalid token."""
    request = _make_request_with_token("not.a.valid.token")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request, db=db_session)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_expired_token(db_session):
    """Test that get_current_user rejects an expired token."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    config = SonghiveConfig(auth={"secret_key": SECRET_KEY})
    token = create_access_token(user.id, config.auth.secret_key, expires_minutes=-10)
    request = _make_request_with_token(token, config=config)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request, db=db_session)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_inactive_user(db_session):
    """Test that get_current_user rejects an inactive user."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    user.is_active = False
    await db_session.flush()

    config = SonghiveConfig(auth={"secret_key": SECRET_KEY})
    token = create_access_token(user.id, config.auth.secret_key)
    request = _make_request_with_token(token, config=config)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request, db=db_session)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_missing_user(db_session):
    """Test that get_current_user rejects a token for a non-existent user."""
    config = SonghiveConfig(auth={"secret_key": SECRET_KEY})
    token = create_access_token("missing-user-id", config.auth.secret_key)
    request = _make_request_with_token(token, config=config)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request, db=db_session)
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_require_admin_allows_admin():
    """Test that require_admin returns an admin user."""
    user = User(username="admin", email="admin@example.com", password_hash="x", role="admin")
    result = await require_admin(user)
    assert result is user


@pytest.mark.asyncio
async def test_require_admin_rejects_non_admin():
    """Test that require_admin rejects non-admin users with 403."""
    user = User(username="user", email="user@example.com", password_hash="x", role="user")
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(user)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_me_endpoint_with_valid_token(client, db_session, config):
    """Test that /api/v1/users/me returns the current authenticated user."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    token = create_access_token(user.id, config.auth.secret_key)
    response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["username"] == "alice"
    assert "email" not in data
    assert "password_hash" not in data


@pytest.mark.asyncio
async def test_admin_users_endpoint_returns_403_for_non_admin(client, db_session, config):
    """Test that admin-only routes return 403 for authenticated non-admin users."""
    user = await create_user(db_session, "bob", "bob@example.com", "secret")
    await db_session.flush()

    token = create_access_token(user.id, config.auth.secret_key)
    response = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_get_db(db_session, monkeypatch):
    """Test that get_db yields a database session."""

    @asynccontextmanager
    async def _fake_session():
        yield db_session

    monkeypatch.setattr("songhive.api.deps.get_session", _fake_session)
    sessions = [s async for s in get_db()]
    assert sessions == [db_session]


@pytest.mark.asyncio
async def test_get_user_by_username_or_email_matches_username(db_session):
    """Test that a username lookup is case-insensitive."""
    await create_user(db_session, "alice", "alice@example.com", "secret")
    found = await get_user_by_username_or_email(db_session, "ALICE")
    assert found is not None
    assert found.username == "alice"


@pytest.mark.asyncio
async def test_get_user_by_username_or_email_matches_email(db_session):
    """Test that an email lookup is case-insensitive."""
    await create_user(db_session, "bob", "bob@example.com", "secret")
    found = await get_user_by_username_or_email(db_session, "BOB@EXAMPLE.COM")
    assert found is not None
    assert found.username == "bob"


@pytest.mark.asyncio
async def test_get_user_by_username_or_email_missing(db_session):
    """Test that a missing value returns None."""
    assert await get_user_by_username_or_email(db_session, "nobody") is None
    assert await get_user_by_username_or_email(db_session, "nobody@example.com") is None


@pytest.mark.asyncio
async def test_register_endpoint(client):
    """Test that the register endpoint creates a user in open mode."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
            "display_name": "Alice",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["username"] == "alice"
    assert data["email"] == "alice@example.com"
    assert data["display_name"] == "Alice"
    assert data["is_active"] is True
    assert data["email_verified"] is True
    assert data["role"] == "user"
    assert "id" in data


@pytest.mark.asyncio
async def test_register_endpoint_closed_mode(client):
    """Test that registration is rejected when the registration mode is closed."""
    client.app.state.config.auth.registration_mode = RegistrationMode.CLOSED
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": "secret",
        },
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_register_endpoint_duplicate_username(client, db_session):
    """Test that duplicate usernames are rejected with 409."""
    await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "other@example.com",
            "password": "secret",
        },
    )
    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_login_endpoint(client):
    """Test that login returns a token pair for valid credentials."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 900
    assert decode_access_token(data["access_token"], SECRET_KEY) is not None


@pytest.mark.asyncio
async def test_login_endpoint_with_email(client):
    """Test that login accepts an email address as the username."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "ALICE@EXAMPLE.COM", "password": "secret"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_login_endpoint_invalid_password(client):
    """Test that login rejects an incorrect password."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wrong"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_login_endpoint_inactive_user(client, db_session, config):
    """Test that inactive users cannot log in."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    user.is_active = False
    user.email_verified = True
    await db_session.flush()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_login_endpoint_unverified_email(client, db_session, config):
    """Test that unverified users cannot log in when verification is required."""
    client.app.state.config.auth.require_email_verification = True
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    user.is_active = True
    user.email_verified = False
    await db_session.flush()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_login_updates_last_login(client, db_session):
    """Test that a successful login updates the user's last_login timestamp."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    assert response.status_code == status.HTTP_200_OK

    user = await get_user_by_username(db_session, "alice")
    assert user is not None
    assert user.last_login is not None


@pytest.mark.asyncio
async def test_refresh_endpoint(client, config):
    """Test that the refresh endpoint returns a new token pair."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    original = login_response.json()

    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["refresh_token"] != original["refresh_token"]
    assert decode_access_token(data["access_token"], config.auth.secret_key) is not None
    assert decode_access_token(data["access_token"], config.auth.secret_key) == decode_access_token(
        original["access_token"], config.auth.secret_key
    )

    replay = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )
    assert replay.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_refresh_endpoint_invalid_token(client):
    """Test that an invalid refresh token is rejected."""
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "not-a-real-token"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_refresh_endpoint_inactive_user(client, db_session):
    """Test that an inactive user cannot refresh tokens."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    original = login_response.json()

    user = await get_user_by_username(db_session, "alice")
    assert user is not None
    user.is_active = False
    await db_session.flush()

    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["detail"] == "Account is inactive or deleted"


@pytest.mark.asyncio
async def test_logout_endpoint(client, config, fake_redis):
    """Test that logout revokes the refresh token and prevents refresh."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    tokens = login_response.json()

    logout_response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert logout_response.status_code == status.HTTP_200_OK
    assert logout_response.json()["success"] is True

    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refresh_response.status_code == status.HTTP_401_UNAUTHORIZED


class _MockCeleryTask:
    """Stand-in for a Celery task that records ``.delay()`` calls."""

    def __init__(self):
        self.calls: list[tuple] = []

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.mark.asyncio
async def test_register_sends_verification_email_when_required(client, db_session, monkeypatch):
    """Test that registration queues a verification email when required."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_verification_email", mock_task)
    client.app.state.config.auth.require_email_verification = True

    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "verify-alice",
            "email": "verify-alice@example.com",
            "password": "secret",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["is_active"] is True
    assert data["email_verified"] is False

    assert len(mock_task.calls) == 1
    to_address, username, token = mock_task.calls[0][0]
    assert to_address == "verify-alice@example.com"
    assert username == "verify-alice"
    assert len(token) >= 32

    user = await get_user_by_username(db_session, "verify-alice")
    assert user is not None
    assert user.email_verification_token != token
    assert len(user.email_verification_token) == 64


@pytest.mark.asyncio
async def test_register_does_not_send_verification_email_when_disabled(client, monkeypatch):
    """Test that registration does not queue an email when verification is disabled."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_verification_email", mock_task)
    client.app.state.config.auth.require_email_verification = False

    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "no-verify-alice",
            "email": "no-verify-alice@example.com",
            "password": "secret",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["email_verified"] is True
    assert len(mock_task.calls) == 0


@pytest.mark.asyncio
async def test_verify_email_with_valid_token(client, db_session, monkeypatch):
    """Test that a valid verification token marks the email as verified."""
    client.app.state.config.auth.require_email_verification = True
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_verification_email", mock_task)

    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "verify-me",
            "email": "verify-me@example.com",
            "password": "secret",
        },
    )
    assert register_response.status_code == status.HTTP_201_CREATED

    assert len(mock_task.calls) == 1
    token = mock_task.calls[0][0][2]

    response = client.post(
        "/api/v1/auth/verify-email",
        json={"token": token},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True

    user = await get_user_by_username(db_session, "verify-me")
    assert user is not None
    await db_session.refresh(user)
    assert user.email_verified is True
    assert user.email_verification_token is None


@pytest.mark.asyncio
async def test_verify_email_with_invalid_token(client):
    """Test that an unknown verification token is rejected."""
    response = client.post(
        "/api/v1/auth/verify-email",
        json={"token": "not-a-real-token"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_verify_email_cannot_reuse_token(client, monkeypatch):
    """Test that a verification token can only be used once."""
    client.app.state.config.auth.require_email_verification = True
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_verification_email", mock_task)

    client.post(
        "/api/v1/auth/register",
        json={
            "username": "reuse-me",
            "email": "reuse-me@example.com",
            "password": "secret",
        },
    )

    assert len(mock_task.calls) == 1
    token = mock_task.calls[0][0][2]

    first = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert first.status_code == status.HTTP_200_OK

    second = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert second.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_login_blocks_unverified_user_until_verified(client, monkeypatch):
    """Test that unverified open-mode users cannot log in until verified."""
    client.app.state.config.auth.require_email_verification = True
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_verification_email", mock_task)

    client.post(
        "/api/v1/auth/register",
        json={
            "username": "blocked-alice",
            "email": "blocked-alice@example.com",
            "password": "secret",
        },
    )

    assert len(mock_task.calls) == 1
    token = mock_task.calls[0][0][2]

    login_before = client.post(
        "/api/v1/auth/login",
        json={"username": "blocked-alice", "password": "secret"},
    )
    assert login_before.status_code == status.HTTP_403_FORBIDDEN

    client.post("/api/v1/auth/verify-email", json={"token": token})

    login_after = client.post(
        "/api/v1/auth/login",
        json={"username": "blocked-alice", "password": "secret"},
    )
    assert login_after.status_code == status.HTTP_200_OK
    assert "access_token" in login_after.json()


@pytest.mark.asyncio
async def test_resend_verification_email_for_unverified_user(client, db_session, monkeypatch):
    """Resending a verification email queues a new token for an unverified user."""
    client.app.state.config.auth.require_email_verification = True
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_verification_email", mock_task)

    client.post(
        "/api/v1/auth/register",
        json={
            "username": "resend-alice",
            "email": "resend-alice@example.com",
            "password": "secret",
        },
    )

    original_token = mock_task.calls[0][0][2]

    response = client.post(
        "/api/v1/auth/verify-email/resend",
        json={"username_or_email": "resend-alice"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True

    assert len(mock_task.calls) == 2
    to_address, username, token = mock_task.calls[1][0]
    assert to_address == "resend-alice@example.com"
    assert username == "resend-alice"
    assert len(token) >= 32
    assert token != original_token

    user = await get_user_by_username(db_session, "resend-alice")
    assert user is not None
    assert user.email_verification_token == hashlib.sha256(token.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_resend_verification_email_returns_generic_success_for_verified_user(client, db_session, monkeypatch):
    """Resending for an already-verified user returns success but sends nothing."""
    client.app.state.config.auth.require_email_verification = True
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_verification_email", mock_task)

    client.post(
        "/api/v1/auth/register",
        json={
            "username": "resend-verified",
            "email": "resend-verified@example.com",
            "password": "secret",
        },
    )

    token = mock_task.calls[0][0][2]
    client.post("/api/v1/auth/verify-email", json={"token": token})

    response = client.post(
        "/api/v1/auth/verify-email/resend",
        json={"username_or_email": "resend-verified"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True
    assert len(mock_task.calls) == 1

    user = await get_user_by_username(db_session, "resend-verified")
    assert user is not None
    assert user.email_verified is True


@pytest.mark.asyncio
async def test_resend_verification_email_returns_generic_success_for_missing_user(client, monkeypatch):
    """Resending for a non-existent user returns a generic success response."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_verification_email", mock_task)

    response = client.post(
        "/api/v1/auth/verify-email/resend",
        json={"username_or_email": "nobody@example.com"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True
    assert len(mock_task.calls) == 0


@pytest.mark.asyncio
async def test_resend_verification_email_uses_email_lookup(client, db_session, monkeypatch):
    """Resending by email address works the same as by username."""
    client.app.state.config.auth.require_email_verification = True
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_verification_email", mock_task)

    client.post(
        "/api/v1/auth/register",
        json={
            "username": "resend-by-email",
            "email": "resend-by-email@example.com",
            "password": "secret",
        },
    )

    response = client.post(
        "/api/v1/auth/verify-email/resend",
        json={"username_or_email": "resend-by-email@example.com"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True

    assert len(mock_task.calls) == 2
    user = await get_user_by_username(db_session, "resend-by-email")
    assert user is not None
    assert user.email_verification_token is not None


@pytest.mark.asyncio
async def test_password_reset_request_sends_email_for_existing_user(client, db_session, monkeypatch):
    """Test that a reset request queues an email for a real user."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "reset-alice",
            "email": "reset-alice@example.com",
            "password": "secret",
        },
    )

    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_password_reset_email", mock_task)

    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "reset-alice"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True

    assert len(mock_task.calls) == 1
    to_address, username, token = mock_task.calls[0][0]
    assert to_address == "reset-alice@example.com"
    assert username == "reset-alice"
    assert len(token) >= 32

    user = await get_user_by_username(db_session, "reset-alice")
    assert user is not None
    assert user.password_reset_token is not None
    assert user.password_reset_token != token
    assert user.password_reset_expires_at is not None


@pytest.mark.asyncio
async def test_password_reset_request_returns_generic_success_for_missing_user(client, monkeypatch):
    """Test that reset requests for unknown users still return success."""
    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_password_reset_email", mock_task)

    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "nobody@example.com"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True
    assert len(mock_task.calls) == 0


@pytest.mark.asyncio
async def test_password_reset_confirm_with_valid_token(client, db_session, monkeypatch):
    """Test that a valid reset token changes the password and revokes refresh tokens."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "reset-bob",
            "email": "reset-bob@example.com",
            "password": "old-secret",
        },
    )

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "reset-bob", "password": "old-secret"},
    )
    assert login.status_code == status.HTTP_200_OK
    old_refresh_token = login.json()["refresh_token"]

    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_password_reset_email", mock_task)
    client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "reset-bob"},
    )
    _, _, token = mock_task.calls[0][0]

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "new-secret"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True

    user = await get_user_by_username(db_session, "reset-bob")
    assert user is not None
    assert user.password_reset_token is None
    assert user.password_reset_expires_at is None

    old_login = client.post(
        "/api/v1/auth/login",
        json={"username": "reset-bob", "password": "old-secret"},
    )
    assert old_login.status_code == status.HTTP_401_UNAUTHORIZED

    old_refresh = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": old_refresh_token},
    )
    assert old_refresh.status_code == status.HTTP_401_UNAUTHORIZED

    new_login = client.post(
        "/api/v1/auth/login",
        json={"username": "reset-bob", "password": "new-secret"},
    )
    assert new_login.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_password_reset_confirm_with_invalid_token(client):
    """Test that an unknown reset token is rejected."""
    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "not-a-real-token", "new_password": "new-secret"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_password_reset_confirm_with_expired_token(client, db_session):
    """Test that an expired reset token is rejected."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "reset-expired",
            "email": "reset-expired@example.com",
            "password": "secret",
        },
    )

    user = await get_user_by_username(db_session, "reset-expired")
    assert user is not None
    raw_token = "expired-token"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    user.password_reset_token = token_hash
    user.password_reset_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.flush()

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "new-secret"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_password_reset_confirm_cannot_reuse_token(client, db_session, monkeypatch):
    """Test that a password reset token can only be used once."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "reset-reuse",
            "email": "reset-reuse@example.com",
            "password": "old-secret",
        },
    )

    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_password_reset_email", mock_task)
    client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "reset-reuse"},
    )
    _, _, token = mock_task.calls[0][0]

    first = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "new-secret"},
    )
    assert first.status_code == status.HTTP_200_OK

    second = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "another-secret"},
    )
    assert second.status_code == status.HTTP_400_BAD_REQUEST

    user = await get_user_by_username(db_session, "reset-reuse")
    assert user is not None
    assert user.password_reset_token is None
    assert user.password_reset_expires_at is None


@pytest.mark.asyncio
async def test_password_reset_request_sends_email_for_inactive_user(client, db_session, monkeypatch):
    """Test that inactive users can request a password reset."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "reset-inactive",
            "email": "reset-inactive@example.com",
            "password": "secret",
        },
    )
    user = await get_user_by_username(db_session, "reset-inactive")
    assert user is not None
    user.is_active = False
    await db_session.flush()

    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_password_reset_email", mock_task)
    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "reset-inactive"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True
    assert len(mock_task.calls) == 1


@pytest.mark.asyncio
async def test_password_reset_confirm_for_inactive_user_sets_password_but_blocks_login(client, db_session, monkeypatch):
    """Test that resetting an inactive user's password does not activate the account."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "reset-still-inactive",
            "email": "reset-still-inactive@example.com",
            "password": "old-secret",
        },
    )
    user = await get_user_by_username(db_session, "reset-still-inactive")
    assert user is not None
    user.is_active = False
    await db_session.flush()

    mock_task = _MockCeleryTask()
    monkeypatch.setattr("songhive.api.routes.auth.send_password_reset_email", mock_task)
    client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "reset-still-inactive"},
    )
    _, _, token = mock_task.calls[0][0]

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": token, "new_password": "new-secret"},
    )
    assert response.status_code == status.HTTP_200_OK

    user = await get_user_by_username(db_session, "reset-still-inactive")
    assert user is not None
    assert verify_password("new-secret", user.password_hash)
    assert user.is_active is False

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "reset-still-inactive", "password": "new-secret"},
    )
    assert login.status_code == status.HTTP_401_UNAUTHORIZED
    assert login.json()["detail"] == "Account is inactive"


def _pkce_pair():
    """Return a PKCE verifier and the matching S256 challenge."""
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest()).decode().rstrip("=")
    return verifier, challenge


@pytest.mark.asyncio
async def test_oauth_authorize_post(client, db_session, config, monkeypatch):
    """POST /auth/oauth/authorize issues an authorization code."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    client_obj, _ = await oauth_service.create_oauth_client(
        db_session,
        created_by=str(user.id),
        name="Test Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()

    token = create_access_token(str(user.id), config.auth.secret_key)
    verifier, challenge = _pkce_pair()

    response = client.post(
        "/api/v1/auth/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_obj.client_id,
            "redirect_uri": "https://example.com/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_302_FOUND
    assert "code=" in response.headers["location"]


@pytest.mark.asyncio
async def test_oauth_token_endpoint_accepts_http_basic_auth(client, db_session, config):
    """The token endpoint accepts client credentials via HTTP Basic auth."""
    import base64

    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    client_obj, client_secret = await oauth_service.create_oauth_client(
        db_session,
        created_by=str(user.id),
        name="Basic Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()

    token_user = create_access_token(str(user.id), config.auth.secret_key)
    verifier, challenge = _pkce_pair()

    authorize = client.get(
        "/api/v1/auth/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_obj.client_id,
            "redirect_uri": "https://example.com/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        headers={"Authorization": f"Bearer {token_user}"},
        follow_redirects=False,
    )
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(authorize.headers["location"])
    code = parse_qs(parsed.query)["code"][0]

    credentials = base64.b64encode(f"{client_obj.client_id}:{client_secret}".encode()).decode()
    token_response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://example.com/callback",
            "code_verifier": verifier,
        },
        headers={"Authorization": f"Basic {credentials}"},
    )
    assert token_response.status_code == status.HTTP_200_OK
    assert "access_token" in token_response.json()


@pytest.mark.asyncio
async def test_oauth_token_endpoint_rejects_invalid_basic_auth(client, db_session, config):
    """The token endpoint ignores malformed HTTP Basic auth headers."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    client_obj, client_secret = await oauth_service.create_oauth_client(
        db_session,
        created_by=str(user.id),
        name="Basic Client",
        redirect_uris=["https://example.com/callback"],
    )
    await db_session.flush()

    # Send an un-decodable basic header; the route falls back to the form values.
    response = client.post(
        "/api/v1/auth/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_obj.client_id,
            "client_secret": client_secret,
        },
        headers={"Authorization": "Basic not-valid-base64"},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oauth_revoke_endpoint_returns_invalid_client_error(client, db_session, monkeypatch):
    """The revoke endpoint converts OAuth2ProviderError into an HTTPException."""

    async def _raise(*args, **kwargs):
        from songhive.users.oauth import OAuth2ProviderError

        raise OAuth2ProviderError("Invalid client", status_code=401, error="invalid_client")

    monkeypatch.setattr("songhive.api.routes.auth.revoke_token", _raise)

    response = client.post(
        "/api/v1/auth/oauth/revoke",
        data={"token": "some-token", "client_id": "client-id"},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_refresh_endpoint_rotate_returns_none(client, config, monkeypatch):
    """Test that the refresh endpoint handles a concurrent rotation failure."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "rotate-fail",
            "email": "rotate-fail@example.com",
            "password": "secret",
        },
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "rotate-fail", "password": "secret"},
    )
    token = login.json()["refresh_token"]

    async def _return_none(*args, **kwargs):
        return None

    monkeypatch.setattr("songhive.api.routes.auth.rotate_refresh_token", _return_none)

    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": token},
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
