"""
Authentication service and middleware tests.
"""

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, HTTPException, Request, status

from songhive.api.deps import get_config, get_current_user, require_admin
from songhive.api.middleware.auth import (
    create_access_token,
    decode_access_token,
    extract_token,
)
from songhive.config.schema import SonghiveConfig
from songhive.models.user import User
from songhive.services.auth import (
    create_user,
    get_user_by_email,
    get_user_by_username,
    hash_password,
    verify_password,
)

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


def test_extract_token_from_query():
    """Test extracting a token from the query string."""
    request = Request(
        {
            "type": "http",
            "headers": [],
            "query_string": b"token=xyz789",
        }
    )
    assert extract_token(request) == "xyz789"


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


@pytest.mark.asyncio
async def test_get_current_user_raises_401():
    """Test that get_current_user raises a 401 before it is implemented."""
    request = Request({"type": "http"})
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(request)
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
async def test_get_db(db_session, monkeypatch):
    """Test that get_db yields a database session."""
    from songhive.api.deps import get_db

    @asynccontextmanager
    async def _fake_session():
        yield db_session

    monkeypatch.setattr("songhive.api.deps.get_session", _fake_session)
    sessions = [s async for s in get_db()]
    assert sessions == [db_session]
