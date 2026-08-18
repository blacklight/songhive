"""
User model and lifecycle management tests.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from songhive.api.middleware.auth import create_access_token
from songhive.api.routes.users import UserLinkInput, UserProfileUpdate, UserResponse
from songhive.config.schema import SonghiveConfig
from songhive.models.user import User
from songhive.models.user_link import UserLink
from songhive.services.auth import create_user, verify_password
from songhive.users.manager import (
    RegistrationError,
    change_password,
    deactivate_user,
    register_user,
    update_profile,
)


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
    assert user.email_verification_token_raw is None
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
async def test_register_user(db_session, config):
    """Test registering a new user."""
    user = await register_user(
        db_session,
        config,
        username="alice",
        email="alice@example.com",
        password="secret",
        display_name="Alice",
    )
    assert user.username == "alice"
    assert user.email == "alice@example.com"
    assert user.display_name == "Alice"
    assert user.is_active is True
    assert user.role == "user"
    assert user.email_verified is True
    assert user.email_verification_token is None
    assert user.email_verification_token_raw is None
    assert verify_password("secret", user.password_hash)


@pytest.mark.asyncio
async def test_register_user_without_display_name(db_session, config):
    """Test registering a user without a display name."""
    user = await register_user(db_session, config, "bob", "bob@example.com", "secret")
    assert user.username == "bob"
    assert user.display_name is None
    assert user.is_active is True


@pytest.mark.asyncio
async def test_register_user_duplicate_username(db_session, config):
    """Test that duplicate usernames are rejected with 409."""
    await register_user(db_session, config, "alice", "alice@example.com", "secret")
    with pytest.raises(RegistrationError, match="already taken") as exc_info:
        await register_user(db_session, config, "alice", "other@example.com", "secret")
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_register_user_duplicate_email(db_session, config):
    """Test that duplicate emails are rejected with 409."""
    await register_user(db_session, config, "alice", "alice@example.com", "secret")
    with pytest.raises(RegistrationError, match="already taken") as exc_info:
        await register_user(db_session, config, "bob", "alice@example.com", "secret")
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_register_user_closed_rejects(db_session, config):
    """Test that closed registration mode rejects new users."""
    closed_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={"registration_mode": "closed", "secret_key": config.auth.secret_key},
    )
    with pytest.raises(RegistrationError, match="Registration is closed"):
        await register_user(db_session, closed_config, "alice", "alice@example.com", "secret")


@pytest.mark.asyncio
async def test_register_user_approval_required_creates_inactive(db_session, config):
    """Test that approval-required mode creates an inactive user."""
    approval_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={"registration_mode": "approval-required", "secret_key": config.auth.secret_key},
    )
    user = await register_user(db_session, approval_config, "alice", "alice@example.com", "secret")
    assert user.is_active is False
    assert user.email_verified is True
    assert user.role == "user"


@pytest.mark.asyncio
async def test_register_user_invite_only_rejects(db_session, config):
    """Test that invite-only mode rejects registration without a valid invite."""
    invite_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={"registration_mode": "invite-only", "secret_key": config.auth.secret_key},
    )
    with pytest.raises(RegistrationError, match="Invalid or missing invite code"):
        await register_user(db_session, invite_config, "alice", "alice@example.com", "secret")

    with pytest.raises(RegistrationError, match="Invalid or missing invite code"):
        await register_user(
            db_session,
            invite_config,
            "bob",
            "bob@example.com",
            "secret",
            invite_code="not-a-real-code",
        )


@pytest.mark.asyncio
async def test_register_user_email_verification_token(db_session, config):
    """Test that email verification creates an inactive user with a token."""
    verify_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={
            "registration_mode": "open",
            "require_email_verification": True,
            "secret_key": config.auth.secret_key,
        },
    )
    user = await register_user(db_session, verify_config, "alice", "alice@example.com", "secret")
    assert user.is_active is True
    assert user.email_verified is False
    assert user.email_verification_token is not None
    assert len(user.email_verification_token) == 64
    assert user.email_verification_token_raw is not None
    assert len(user.email_verification_token_raw) >= 32
    assert user.email_verification_token != user.email_verification_token_raw


@pytest.mark.asyncio
async def test_register_user_no_email_verification_when_disabled(db_session, config):
    """Test that email is marked verified when verification is not required."""
    no_verify_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={
            "registration_mode": "open",
            "require_email_verification": False,
            "secret_key": config.auth.secret_key,
        },
    )
    user = await register_user(db_session, no_verify_config, "alice", "alice@example.com", "secret")
    assert user.email_verified is True
    assert user.email_verification_token is None
    assert user.email_verification_token_raw is None


@pytest.mark.asyncio
async def test_register_user_approval_and_verification(db_session, config):
    """Test approval-required with email verification still sets a token."""
    approval_verify_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={
            "registration_mode": "approval-required",
            "require_email_verification": True,
            "secret_key": config.auth.secret_key,
        },
    )
    user = await register_user(db_session, approval_verify_config, "alice", "alice@example.com", "secret")
    assert user.is_active is False
    assert user.email_verified is False
    assert user.email_verification_token is not None
    assert len(user.email_verification_token) == 64
    assert user.email_verification_token_raw is not None
    assert len(user.email_verification_token_raw) >= 32
    assert user.email_verification_token != user.email_verification_token_raw


@pytest.mark.asyncio
async def test_register_user_normalizes_username_and_email(db_session, config):
    """Test that usernames and emails are normalized before storage."""
    user = await register_user(
        db_session,
        config,
        username="  Alice  ",
        email="  Alice@Example.COM  ",
        password="secret",
    )
    assert user.username == "alice"
    assert user.email == "alice@example.com"


@pytest.mark.asyncio
async def test_register_user_rejects_invalid_username(db_session, config):
    """Test that invalid usernames are rejected."""
    with pytest.raises(RegistrationError, match="invalid characters"):
        await register_user(db_session, config, "alice@bad", "alice@example.com", "secret")


@pytest.mark.asyncio
async def test_register_user_rejects_invalid_email(db_session, config):
    """Test that obviously invalid emails are rejected."""
    with pytest.raises(RegistrationError, match="Invalid email address"):
        await register_user(db_session, config, "alice", "not-an-email", "secret")


@pytest.mark.asyncio
async def test_register_user_rejects_too_long_password(db_session, config):
    """Test that passwords longer than bcrypt's 72-byte limit are rejected."""
    long_password = "a" * 80
    with pytest.raises(RegistrationError, match="too long"):
        await register_user(db_session, config, "alice", "alice@example.com", long_password)


@pytest.mark.asyncio
async def test_register_user_rejects_email_with_trailing_dot(db_session, config):
    """Test that Pydantic-style email validation rejects a trailing dot."""
    with pytest.raises(RegistrationError, match="Invalid email address"):
        await register_user(db_session, config, "alice", "a@b.", "secret")


@pytest.mark.asyncio
async def test_register_user_whitespace_display_name_becomes_none(db_session, config):
    """Test that whitespace-only display names are stored as None."""
    user = await register_user(
        db_session,
        config,
        "alice",
        "alice@example.com",
        "secret",
        display_name="   ",
    )
    assert user.display_name is None


@pytest.mark.asyncio
async def test_register_user_accepts_72_byte_password(db_session, config):
    """Test that a password of exactly 72 bytes is accepted."""
    password = "🎵" * 18  # 72 bytes in UTF-8
    user = await register_user(db_session, config, "alice", "alice@example.com", password)
    assert user is not None
    assert verify_password(password, user.password_hash)


@pytest.mark.asyncio
async def test_register_user_rejects_non_ascii_password_over_bcrypt_limit(db_session, config):
    """Test that byte length, not character length, enforces the 72-byte limit."""
    password = "🎵" * 19  # 76 bytes in UTF-8
    with pytest.raises(RegistrationError, match="too long"):
        await register_user(db_session, config, "alice", "alice@example.com", password)


@pytest.mark.asyncio
async def test_register_user_invite_only_checks_invite_before_duplicates(db_session, config):
    """Test that invite-only mode rejects an invalid invite before duplicate checks."""
    await register_user(db_session, config, "alice", "alice@example.com", "secret")
    invite_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={"registration_mode": "invite-only", "secret_key": config.auth.secret_key},
    )
    with pytest.raises(RegistrationError, match="Invalid or missing invite code"):
        await register_user(
            db_session,
            invite_config,
            "alice",
            "other@example.com",
            "secret",
            invite_code="not-a-real-code",
        )


@pytest.mark.asyncio
async def test_register_user_duplicate_race_returns_409(db_session, config, monkeypatch):
    """Test that database-level unique constraint violations are translated to 409."""
    await register_user(db_session, config, "alice", "alice@example.com", "secret")
    await db_session.commit()

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("songhive.users.manager._check_for_duplicates", _noop)

    with pytest.raises(RegistrationError, match="already taken") as exc_info:
        await register_user(db_session, config, "alice", "other@example.com", "secret")
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_register_user_unverified_account_is_public(db_session, config, client):
    """Test that open-mode unverified users are active and visible in public profiles."""
    verify_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={
            "registration_mode": "open",
            "require_email_verification": True,
            "secret_key": config.auth.secret_key,
        },
    )
    user = await register_user(db_session, verify_config, "alice", "alice@example.com", "secret")
    assert user.is_active is True
    await db_session.commit()

    response = client.get("/api/v1/users/alice")
    assert response.status_code == 200


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


def test_user_link_model_rejects_unsafe_url_scheme():
    """Test that UserLink rejects URLs with unsafe schemes."""
    with pytest.raises(ValueError, match="Link URL must start with http:// or https://"):
        UserLink(name="Example", url="javascript:alert(1)")
    with pytest.raises(ValueError, match="Link URL must start with http:// or https://"):
        UserLink(name="Example", url="data:text/html,<script>alert(1)</script>")


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


def test_user_link_input_rejects_unsafe_url_scheme():
    """Test that UserLinkInput rejects URLs with unsafe schemes after stripping."""
    with pytest.raises(ValidationError):
        UserLinkInput(name="Example", url="   javascript:alert(1)   ")
    with pytest.raises(ValidationError):
        UserProfileUpdate(links=[UserLinkInput(name="Example", url="data:text/html,<script>alert(1)</script>")])


def test_user_link_input_accepts_http_and_https():
    """Test that UserLinkInput accepts http:// and https:// URLs."""
    https_link = UserLinkInput(name="Website", url="https://example.com")
    assert https_link.url == "https://example.com"
    http_link = UserLinkInput(name="Website", url="http://example.com")
    assert http_link.url == "http://example.com"


@pytest.fixture
async def profile_user(db_session):
    """Create a user with profile links for public profile tests."""
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
    return user


@pytest.fixture
async def inactive_user(db_session):
    """Create an inactive user for public profile tests."""
    user = User(
        username="inactive",
        email="inactive@example.com",
        password_hash="hashed",
        is_active=False,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    return user


def test_me_route_not_shadowed_by_username(client):
    """Test that /api/v1/users/me resolves to the /me route, not /{username}."""
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


def test_get_user_profile_endpoint_returns_links(client, profile_user):
    """Test that the public profile endpoint serializes a user with links."""
    response = client.get("/api/v1/users/alice")
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "alice"
    assert data["display_name"] == "Alice"
    assert data["bio"] == "Hello"
    assert len(data["links"]) == 2
    assert data["links"][0] == {"name": "Website", "url": "https://example.com"}
    assert data["links"][1] == {
        "name": "Mastodon",
        "url": "https://mastodon.example.com/@alice",
    }

    expected_fields = {"username", "display_name", "bio", "avatar_url", "links"}
    assert set(data.keys()) == expected_fields

    sensitive_fields = {
        "id",
        "email",
        "password_hash",
        "email_verification_token",
        "password_reset_token",
        "password_reset_expires_at",
        "private_key_pem",
        "public_key_pem",
        "actor_url",
    }
    for field in sensitive_fields:
        assert field not in data, f"Sensitive field {field!r} leaked into public response"


def test_get_user_profile_endpoint_missing_user(client):
    """Test that the public profile endpoint returns 404 for an unknown user."""
    response = client.get("/api/v1/users/nobody")
    assert response.status_code == 404


def test_get_user_profile_endpoint_inactive_user(client, inactive_user):
    """Test that the public profile endpoint does not expose inactive users."""
    response = client.get("/api/v1/users/inactive")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_profile_updates_scalar_fields(db_session):
    """Test that update_profile updates display_name, bio and avatar_url."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await update_profile(
        db_session,
        user,
        {
            "display_name": "  Alice  ",
            "bio": "  Hello world  ",
            "avatar_url": "  https://example.com/avatar.png  ",
        },
    )
    assert user.display_name == "Alice"
    assert user.bio == "Hello world"
    assert user.avatar_url == "https://example.com/avatar.png"


@pytest.mark.asyncio
async def test_update_profile_clears_fields(db_session):
    """Test that update_profile clears fields sent as None or whitespace."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    user.display_name = "Alice"
    user.bio = "Hello"
    user.avatar_url = "https://example.com/avatar.png"
    await db_session.flush()

    await update_profile(
        db_session,
        user,
        {
            "display_name": "   ",
            "bio": None,
            "avatar_url": "",
        },
    )
    assert user.display_name is None
    assert user.bio is None
    assert user.avatar_url is None


@pytest.mark.asyncio
async def test_update_profile_replaces_links(db_session):
    """Test that update_profile replaces the user's link set."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        links=[UserLink(name="Old", url="https://old.example.com")],
    )
    db_session.add(user)
    await db_session.flush()

    await update_profile(
        db_session,
        user,
        {
            "links": [
                UserLink(name="Website", url="https://example.com"),
                UserLink(name="Mastodon", url="https://mastodon.example.com/@alice"),
            ],
        },
    )

    result = await db_session.execute(select(UserLink).where(UserLink.user_id == user.id))
    links = result.scalars().all()
    assert len(links) == 2
    assert {link.name for link in links} == {"Website", "Mastodon"}


@pytest.mark.asyncio
async def test_update_profile_clears_links(db_session):
    """Test that update_profile clears all links when given an empty list."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        links=[UserLink(name="Website", url="https://example.com")],
    )
    db_session.add(user)
    await db_session.flush()

    await update_profile(db_session, user, {"links": []})

    result = await db_session.execute(select(UserLink).where(UserLink.user_id == user.id))
    assert result.scalars().first() is None


@pytest.mark.asyncio
async def test_update_profile_partial(db_session):
    """Test that update_profile only changes explicitly provided fields."""
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="hashed",
        display_name="Old",
        bio="Old bio",
        avatar_url="https://old.example.com/avatar.png",
        links=[UserLink(name="Website", url="https://example.com")],
    )
    db_session.add(user)
    await db_session.flush()

    await update_profile(db_session, user, {"display_name": "Alice"})
    assert user.display_name == "Alice"
    assert user.bio == "Old bio"
    assert user.avatar_url == "https://old.example.com/avatar.png"
    assert len(user.links) == 1


@pytest.mark.asyncio
async def test_patch_me_endpoint(client, db_session, config):
    """Test that PATCH /me updates the current user's profile and links."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    token = create_access_token(user.id, config.auth.secret_key)
    response = client.patch(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "display_name": "  Alice  ",
            "bio": "Hello",
            "avatar_url": "https://example.com/avatar.png",
            "links": [
                {"name": "Website", "url": "https://example.com"},
                {"name": "Mastodon", "url": "https://mastodon.example.com/@alice"},
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "alice"
    assert data["display_name"] == "Alice"
    assert data["bio"] == "Hello"
    assert data["avatar_url"] == "https://example.com/avatar.png"
    assert len(data["links"]) == 2
    assert data["links"][0] == {"name": "Website", "url": "https://example.com"}

    sensitive_fields = {
        "email",
        "password_hash",
        "email_verification_token",
        "password_reset_token",
        "password_reset_expires_at",
        "private_key_pem",
        "public_key_pem",
    }
    for field in sensitive_fields:
        assert field not in data, f"Sensitive field {field!r} leaked into /me response"


def test_patch_me_endpoint_unauthenticated(client):
    """Test that PATCH /me requires authentication."""
    response = client.patch(
        "/api/v1/users/me",
        json={"display_name": "Alice"},
    )
    assert response.status_code == 401


def test_patch_me_endpoint_invalid_token(client):
    """Test that PATCH /me rejects an invalid token."""
    response = client.patch(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer not-a-real-token"},
        json={"display_name": "Alice"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_patch_me_endpoint_partial(client, db_session, config):
    """Test that a partial PATCH /me leaves unspecified fields unchanged."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    user.display_name = "Old"
    user.bio = "Old bio"
    await db_session.flush()

    token = create_access_token(user.id, config.auth.secret_key)
    response = client.patch(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": "Alice"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Alice"
    assert data["bio"] == "Old bio"


@pytest.mark.asyncio
async def test_patch_me_endpoint_clears_links(client, db_session, config):
    """Test that PATCH /me can clear all links with an empty list."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    db_session.add(UserLink(user_id=user.id, name="Website", url="https://example.com"))
    await db_session.flush()

    token = create_access_token(user.id, config.auth.secret_key)
    response = client.patch(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"links": []},
    )
    assert response.status_code == 200
    assert response.json()["links"] == []


@pytest.mark.asyncio
async def test_patch_me_endpoint_rejects_invalid_link(client, db_session, config):
    """Test that PATCH /me rejects links with unsafe URL schemes."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    token = create_access_token(user.id, config.auth.secret_key)
    response = client.patch(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "links": [
                {"name": "Bad", "url": "javascript:alert(1)"},
            ],
        },
    )
    assert response.status_code == 422
