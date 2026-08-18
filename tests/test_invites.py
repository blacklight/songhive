"""
Invite code model and service tests.
"""

from datetime import datetime, timedelta, timezone

import pytest

from songhive.config.schema import RegistrationMode, SonghiveConfig
from songhive.models.invite import Invite
from songhive.services.auth import create_user
from songhive.users.invites import (
    InviteError,
    consume_invite,
    create_invite,
    get_invite,
    is_invite_valid,
    revoke_invite,
    validate_invite,
)
from songhive.users.manager import RegistrationError, register_user


@pytest.fixture
async def invite_admin(db_session):
    """Create an admin user to act as the creator of invite codes."""
    user = await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_create_invite(db_session, invite_admin):
    """Test that an invite is created with a unique code and default uses."""
    invite = await create_invite(db_session, created_by=invite_admin.id)
    assert invite.id is not None
    assert invite.code
    assert invite.created_by == invite_admin.id
    assert invite.uses == 0
    assert invite.max_uses is None
    assert invite.expires_at is None


@pytest.mark.asyncio
async def test_create_invite_with_max_uses_and_expires_at(db_session, invite_admin):
    """Test creating an invite with limited uses and an expiration."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    invite = await create_invite(db_session, created_by=invite_admin.id, max_uses=5, expires_at=expires_at)
    assert invite.max_uses == 5
    assert invite.expires_at == expires_at


@pytest.mark.asyncio
async def test_create_invite_rejects_non_positive_max_uses(db_session, invite_admin):
    """Test that max_uses must be positive when provided."""
    with pytest.raises(InviteError, match="max_uses must be a positive integer"):
        await create_invite(db_session, created_by=invite_admin.id, max_uses=0)

    with pytest.raises(InviteError, match="max_uses must be a positive integer"):
        await create_invite(db_session, created_by=invite_admin.id, max_uses=-1)


@pytest.mark.asyncio
async def test_create_invite_rejects_past_expiration(db_session, invite_admin):
    """Test that expires_at must be in the future."""
    expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    with pytest.raises(InviteError, match="expires_at must be in the future"):
        await create_invite(db_session, created_by=invite_admin.id, expires_at=expires_at)


@pytest.mark.asyncio
async def test_get_invite(db_session, invite_admin):
    """Test fetching an invite by code."""
    invite = await create_invite(db_session, created_by=invite_admin.id)
    fetched = await get_invite(db_session, invite.code)
    assert fetched is not None
    assert fetched.id == invite.id

    missing = await get_invite(db_session, "not-a-real-code")
    assert missing is None


@pytest.mark.asyncio
async def test_validate_invite_valid(db_session, invite_admin):
    """Test that a fresh invite is valid."""
    invite = await create_invite(db_session, created_by=invite_admin.id)
    assert await validate_invite(db_session, invite.code) is True


@pytest.mark.asyncio
async def test_validate_invite_missing(db_session):
    """Test that a non-existent invite code is invalid."""
    assert await validate_invite(db_session, "not-a-real-code") is False


def test_is_invite_valid():
    """Test the core validity check for invite objects."""
    assert is_invite_valid(None) is False

    valid = Invite(code="valid", created_by="user-1")
    assert is_invite_valid(valid) is True

    exhausted = Invite(code="exhausted", created_by="user-1", max_uses=1, uses=1)
    assert is_invite_valid(exhausted) is False

    expired = Invite(
        code="expired",
        created_by="user-1",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    assert is_invite_valid(expired) is False


@pytest.mark.asyncio
async def test_validate_invite_expired(db_session, invite_admin):
    """Test that an expired invite is invalid."""
    invite = Invite(
        code="expired-code",
        created_by=invite_admin.id,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(invite)
    await db_session.flush()
    assert await validate_invite(db_session, invite.code) is False


@pytest.mark.asyncio
async def test_validate_invite_exhausted(db_session, invite_admin):
    """Test that an invite with max uses is invalid once exhausted."""
    invite = await create_invite(db_session, created_by=invite_admin.id, max_uses=1)
    consumed = await consume_invite(db_session, invite.code)
    assert consumed is not None
    assert consumed.uses == 1

    assert await validate_invite(db_session, invite.code) is False


@pytest.mark.asyncio
async def test_consume_invite_invalid_code(db_session):
    """Test that consuming a non-existent code returns None."""
    assert await consume_invite(db_session, "not-a-real-code") is None


@pytest.mark.asyncio
async def test_consume_invite_increments_uses(db_session, invite_admin):
    """Test that consuming a valid invite increments uses."""
    invite = await create_invite(db_session, created_by=invite_admin.id, max_uses=2)
    for expected in (1, 2):
        consumed = await consume_invite(db_session, invite.code)
        assert consumed is not None
        assert consumed.uses == expected

    assert await consume_invite(db_session, invite.code) is None


@pytest.mark.asyncio
async def test_revoke_invite(db_session, invite_admin):
    """Test that revoking an invite deletes it."""
    invite = await create_invite(db_session, created_by=invite_admin.id)
    assert await revoke_invite(db_session, invite.code) is True
    assert await get_invite(db_session, invite.code) is None
    assert await revoke_invite(db_session, invite.code) is False


@pytest.mark.asyncio
async def test_invite_cascade_on_user_delete(db_session, invite_admin):
    """Test that deleting a user also deletes their invites."""
    invite = await create_invite(db_session, created_by=invite_admin.id)
    await db_session.delete(invite_admin)
    await db_session.flush()

    assert await get_invite(db_session, invite.code) is None


@pytest.mark.asyncio
async def test_register_user_invite_only_succeeds(db_session, config):
    """Test that a valid invite allows registration in invite-only mode."""
    admin = await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()

    invite = await create_invite(db_session, created_by=admin.id)
    await db_session.flush()

    invite_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={"registration_mode": "invite-only", "secret_key": config.auth.secret_key},
    )
    user = await register_user(
        db_session,
        invite_config,
        username="alice",
        email="alice@example.com",
        password="secret",
        invite_code=invite.code,
    )
    await db_session.flush()

    assert user.username == "alice"
    assert user.role == "user"
    assert invite.uses == 1


@pytest.mark.asyncio
async def test_register_user_invite_only_invalid_rejected(db_session, config):
    """Test that an invalid invite is rejected in invite-only mode."""
    invite_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={"registration_mode": "invite-only", "secret_key": config.auth.secret_key},
    )
    with pytest.raises(RegistrationError, match="Invalid or missing invite code"):
        await register_user(
            db_session,
            invite_config,
            username="alice",
            email="alice@example.com",
            password="secret",
            invite_code="not-a-real-code",
        )


@pytest.mark.asyncio
async def test_register_user_invite_only_expired_rejected(db_session, config, invite_admin):
    """Test that an expired invite is rejected and not consumed."""
    invite = Invite(
        code="expired-code",
        created_by=invite_admin.id,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(invite)
    await db_session.flush()

    invite_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={"registration_mode": "invite-only", "secret_key": config.auth.secret_key},
    )
    with pytest.raises(RegistrationError, match="Invalid or missing invite code"):
        await register_user(
            db_session,
            invite_config,
            username="alice",
            email="alice@example.com",
            password="secret",
            invite_code=invite.code,
        )
    assert invite.uses == 0


@pytest.mark.asyncio
async def test_register_user_invite_only_exhausted_rejected(db_session, config, invite_admin):
    """Test that an exhausted invite is rejected and not double-consumed."""
    invite = await create_invite(db_session, created_by=invite_admin.id, max_uses=1)
    await consume_invite(db_session, invite.code)
    await db_session.flush()

    invite_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={"registration_mode": "invite-only", "secret_key": config.auth.secret_key},
    )
    with pytest.raises(RegistrationError, match="Invalid or missing invite code"):
        await register_user(
            db_session,
            invite_config,
            username="alice",
            email="alice@example.com",
            password="secret",
            invite_code=invite.code,
        )
    assert invite.uses == 1


@pytest.mark.asyncio
async def test_register_user_invite_only_does_not_consume_on_duplicate(db_session, config, invite_admin):
    """Test that a valid invite is not consumed when registration hits a duplicate."""
    await register_user(db_session, config, "alice", "alice@example.com", "secret")
    await db_session.commit()

    invite = await create_invite(db_session, created_by=invite_admin.id)
    await db_session.flush()

    invite_config = SonghiveConfig(
        database={"url": config.database.url},
        federation={"enabled": False},
        auth={"registration_mode": "invite-only", "secret_key": config.auth.secret_key},
    )
    with pytest.raises(RegistrationError, match="already taken"):
        await register_user(
            db_session,
            invite_config,
            "alice",
            "other@example.com",
            "secret",
            invite_code=invite.code,
        )
    assert invite.uses == 0


def test_invite_model_instantiation():
    """Test that an invite model can be instantiated in memory."""
    invite = Invite(code="abc123", created_by="user-1")
    assert invite.code == "abc123"
    assert invite.created_by == "user-1"
    assert invite.uses is None


@pytest.mark.asyncio
async def test_register_endpoint_invite_only(client, db_session, config):
    """Test that the auth endpoint requires and consumes a valid invite code."""
    admin = await create_user(db_session, "admin", "admin@example.com", "secret", role="admin")
    await db_session.flush()

    invite = await create_invite(db_session, created_by=admin.id)
    await db_session.flush()

    client.app.state.config.auth.registration_mode = RegistrationMode.INVITE_ONLY

    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
            "invite_code": invite.code,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "alice"
    assert invite.uses == 1

    invalid = client.post(
        "/api/v1/auth/register",
        json={
            "username": "bob",
            "email": "bob@example.com",
            "password": "secret",
            "invite_code": "not-a-real-code",
        },
    )
    assert invalid.status_code == 400
