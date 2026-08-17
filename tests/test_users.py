"""
User model and lifecycle management tests.
"""

import pytest

from songhive.models.user import User
from songhive.services.auth import create_user, verify_password
from songhive.users.manager import change_password, deactivate_user, register_user


def test_user_model_defaults():
    """Test User model defaults and optional fields before persistence."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
    )
    assert user.username == "alice"
    assert user.email == "alice@example.com"
    assert user.id is None
    assert user.is_active is None
    assert user.is_admin is None
    assert user.display_name is None
    assert user.bio is None
    assert user.avatar_url is None
    assert user.last_login is None
    assert user.actor_url is None
    assert user.private_key_pem is None
    assert user.public_key_pem is None


@pytest.mark.asyncio
async def test_user_db_defaults(db_session):
    """Test User model defaults are applied on flush."""
    user = User(username="alice", email="alice@example.com", password_hash="hashed")
    db_session.add(user)
    await db_session.flush()
    assert user.id is not None
    assert user.is_active is True
    assert user.is_admin is False


def test_user_model_with_optional_fields():
    """Test User model with optional fields populated."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        display_name="Alice",
        bio="Hello",
        avatar_url="https://example.com/avatar.png",
        is_admin=True,
    )
    assert user.display_name == "Alice"
    assert user.bio == "Hello"
    assert user.avatar_url == "https://example.com/avatar.png"
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_register_user(db_session):
    """Test registering a new user."""
    user = await register_user(
        db_session,
        username="alice",
        email="alice@example.com",
        password="secret",
        display_name="Alice",
    )
    assert user.username == "alice"
    assert user.email == "alice@example.com"
    assert user.display_name == "Alice"
    assert user.is_active is True
    assert verify_password("secret", user.password_hash)


@pytest.mark.asyncio
async def test_register_user_without_display_name(db_session):
    """Test registering a user without a display name."""
    user = await register_user(db_session, "bob", "bob@example.com", "secret")
    assert user.username == "bob"
    assert user.display_name is None


@pytest.mark.asyncio
async def test_register_user_duplicate_username(db_session):
    """Test that duplicate usernames are rejected."""
    await register_user(db_session, "alice", "alice@example.com", "secret")
    with pytest.raises(ValueError, match="already taken"):
        await register_user(db_session, "alice", "other@example.com", "secret")


@pytest.mark.asyncio
async def test_change_password(db_session):
    """Test changing a user's password."""
    user = await create_user(db_session, "alice", "alice@example.com", "old-password")
    await change_password(db_session, user, "new-password")
    assert verify_password("new-password", user.password_hash)
    assert verify_password("old-password", user.password_hash) is False


@pytest.mark.asyncio
async def test_deactivate_user(db_session):
    """Test deactivating a user account."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    assert user.is_active is True
    await deactivate_user(db_session, user)
    assert user.is_active is False
