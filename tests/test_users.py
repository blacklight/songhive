"""
User model and lifecycle management tests.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from songhive.api.deps import get_db
from songhive.api.routes.users import UserLinkInput, UserProfileUpdate, UserResponse
from songhive.models.user import User
from songhive.models.user_link import UserLink
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
    assert user.role is None
    assert user.is_admin is False
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
    assert user.email_verified is False
    assert user.email_verification_token is None
    assert user.password_reset_token is None
    assert user.password_reset_expires_at is None


def test_user_model_with_optional_fields():
    """Test User model with optional fields populated."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        display_name="Alice",
        bio="Hello",
        avatar_url="https://example.com/avatar.png",
        role="admin",
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


def test_user_link_model_rejects_empty_name():
    """Test that UserLink rejects an empty name."""
    with pytest.raises(ValueError, match="Link name cannot be empty"):
        UserLink(name="", url="https://example.com")


def test_user_link_model_rejects_empty_url():
    """Test that UserLink rejects an empty URL."""
    with pytest.raises(ValueError, match="Link URL cannot be empty"):
        UserLink(name="Example", url="")


def test_user_link_model_rejects_whitespace_only():
    """Test that UserLink strips and rejects whitespace-only values."""
    with pytest.raises(ValueError, match="Link name cannot be empty"):
        UserLink(name="   ", url="https://example.com")
    with pytest.raises(ValueError, match="Link URL cannot be empty"):
        UserLink(name="Example", url="   ")


@pytest.mark.asyncio
async def test_user_links_cascade_on_delete(db_session):
    """Test that deleting a user also deletes their profile links."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        links=[UserLink(name="Example", url="https://example.com")],
    )
    db_session.add(user)
    await db_session.flush()

    await db_session.delete(user)
    await db_session.flush()

    remaining = await db_session.execute(select(UserLink).where(UserLink.user_id == user.id))
    assert remaining.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_user_profile_serialization_includes_links(db_session):
    """Test that a user profile can be serialized with links."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        display_name="Alice",
        bio="Hello",
        links=[
            UserLink(name="Website", url="https://example.com"),
            UserLink(name="Mastodon", url="https://mastodon.example.com/@alice"),
        ],
    )
    db_session.add(user)
    await db_session.flush()

    profile = UserResponse.model_validate(user)
    assert profile.username == "alice"
    assert profile.display_name == "Alice"
    assert profile.bio == "Hello"
    assert len(profile.links) == 2
    assert profile.links[0].name == "Website"
    assert profile.links[0].url == "https://example.com"
    assert profile.links[1].name == "Mastodon"


def test_user_profile_update_schema_rejects_empty_link_name():
    """Test that UserProfileUpdate rejects a link with an empty name."""
    with pytest.raises(ValidationError):
        UserProfileUpdate(links=[UserLinkInput(name="", url="https://example.com")])


def test_user_profile_update_schema_rejects_empty_link_url():
    """Test that UserProfileUpdate rejects a link with an empty URL."""
    with pytest.raises(ValidationError):
        UserProfileUpdate(links=[UserLinkInput(name="Example", url="")])


def test_user_profile_update_schema_accepts_valid_links():
    """Test that UserProfileUpdate accepts a list of valid links."""
    update = UserProfileUpdate(
        display_name="Alice",
        bio="Hello",
        avatar_url="https://example.com/avatar.png",
        links=[
            UserLinkInput(name="Website", url="https://example.com"),
            UserLinkInput(name="Mastodon", url="https://mastodon.example.com/@alice"),
        ],
    )
    assert update.display_name == "Alice"
    assert update.bio == "Hello"
    assert update.avatar_url == "https://example.com/avatar.png"
    assert len(update.links) == 2


def test_user_link_input_strips_whitespace():
    """Test that UserLinkInput strips whitespace before validation."""
    with pytest.raises(ValidationError):
        UserLinkInput(name="   ", url="https://example.com")
    with pytest.raises(ValidationError):
        UserLinkInput(name="Example", url="   ")

    link = UserLinkInput(name="  Website  ", url="  https://example.com  ")
    assert link.name == "Website"
    assert link.url == "https://example.com"


def _override_db(session):
    """Return a FastAPI dependency that yields the provided session."""

    async def _db():
        yield session

    return _db


@pytest.mark.asyncio
async def test_me_route_not_shadowed_by_username(client):
    """Test that /api/v1/users/me resolves to the /me route, not /{username}."""
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_user_profile_endpoint_returns_links(client, db_session):
    """Test that the public profile endpoint serializes a user with links."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        display_name="Alice",
        bio="Hello",
        links=[
            UserLink(name="Website", url="https://example.com"),
            UserLink(name="Mastodon", url="https://mastodon.example.com/@alice"),
        ],
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    client.app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        response = client.get("/api/v1/users/alice")
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "alice"
        assert data["display_name"] == "Alice"
        assert data["bio"] == "Hello"
        assert "email" not in data
        assert "password_hash" not in data
        assert len(data["links"]) == 2
        assert data["links"][0] == {"name": "Website", "url": "https://example.com"}
        assert data["links"][1] == {
            "name": "Mastodon",
            "url": "https://mastodon.example.com/@alice",
        }
    finally:
        client.app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_get_user_profile_endpoint_missing_user(client, db_session):
    """Test that the public profile endpoint returns 404 for an unknown user."""
    client.app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        response = client.get("/api/v1/users/nobody")
        assert response.status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_get_user_profile_endpoint_inactive_user(client, db_session):
    """Test that the public profile endpoint does not expose inactive users."""
    user = User(
        username="inactive",
        email="inactive@example.com",
        password_hash="hashed",
        is_active=False,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    client.app.dependency_overrides[get_db] = _override_db(db_session)
    try:
        response = client.get("/api/v1/users/inactive")
        assert response.status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_db, None)
