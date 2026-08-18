"""
Tests for the refresh token service.
"""

import pytest

from songhive.api.middleware.auth import decode_access_token
from songhive.config.schema import AuthConfig
from songhive.services.auth import create_user
from songhive.users.tokens import (
    issue_token_pair,
    revoke_refresh_token,
    rotate_refresh_token,
    validate_refresh_token,
)

SECRET_KEY = "a" * 32


@pytest.fixture
def token_config(config):
    """Return a config with deterministic token expiry values."""
    return config.model_copy(
        update={
            "auth": AuthConfig(
                secret_key=SECRET_KEY,
                access_token_expiry_minutes=5,
                refresh_token_expiry_days=1,
            )
        }
    )


@pytest.mark.asyncio
async def test_issue_token_pair(db_session, token_config, fake_redis):
    """Test that issuing a token pair stores a refresh token and returns a JWT."""
    user = await create_user(db_session, "alice", "alice@example.com", "secret")
    await db_session.flush()

    token_pair = await issue_token_pair(user, token_config, fake_redis)

    assert token_pair.token_type == "bearer"
    assert token_pair.expires_in == 300
    assert decode_access_token(token_pair.access_token, SECRET_KEY) == user.id
    assert len(token_pair.refresh_token) >= 32

    payload = await validate_refresh_token(token_pair.refresh_token, token_config, fake_redis)
    assert payload is not None
    assert payload.user_id == user.id
    assert payload.expires_at is not None


@pytest.mark.asyncio
async def test_validate_refresh_token_returns_none_for_unknown_token(token_config, fake_redis):
    """Test that validating an unknown refresh token returns None."""
    payload = await validate_refresh_token("not-a-real-token", token_config, fake_redis)
    assert payload is None


@pytest.mark.asyncio
async def test_validate_refresh_token_returns_none_for_malformed_value(db_session, token_config, fake_redis):
    """Test that a malformed Redis value is treated as invalid."""
    user = await create_user(db_session, "malformed", "malformed@example.com", "secret")
    await db_session.flush()

    token_pair = await issue_token_pair(user, token_config, fake_redis)
    keys = [k async for k in fake_redis.scan_iter("auth:refresh:*")]
    await fake_redis.set(keys[0], "not-json")

    payload = await validate_refresh_token(token_pair.refresh_token, token_config, fake_redis)
    assert payload is None


@pytest.mark.asyncio
async def test_validate_refresh_token_returns_none_for_non_object_value(db_session, token_config, fake_redis):
    """Test that a non-object JSON Redis value is treated as invalid."""
    user = await create_user(db_session, "non-object", "non-object@example.com", "secret")
    await db_session.flush()

    token_pair = await issue_token_pair(user, token_config, fake_redis)
    keys = [k async for k in fake_redis.scan_iter("auth:refresh:*")]
    await fake_redis.set(keys[0], "[1, 2, 3]")

    payload = await validate_refresh_token(token_pair.refresh_token, token_config, fake_redis)
    assert payload is None


@pytest.mark.asyncio
async def test_validate_refresh_token_returns_none_for_missing_user_id(db_session, token_config, fake_redis):
    """Test that a Redis value missing the user_id is treated as invalid."""
    user = await create_user(db_session, "missing-user-id", "missing-user-id@example.com", "secret")
    await db_session.flush()

    token_pair = await issue_token_pair(user, token_config, fake_redis)
    keys = [k async for k in fake_redis.scan_iter("auth:refresh:*")]
    await fake_redis.set(keys[0], '{"created_at": "2024-01-01T00:00:00+00:00"}')

    payload = await validate_refresh_token(token_pair.refresh_token, token_config, fake_redis)
    assert payload is None


@pytest.mark.asyncio
async def test_validate_refresh_token_ignores_invalid_expires_at(db_session, token_config, fake_redis):
    """Test that an invalid expires_at format does not invalidate the token."""
    user = await create_user(db_session, "invalid-expires", "invalid-expires@example.com", "secret")
    await db_session.flush()

    token_pair = await issue_token_pair(user, token_config, fake_redis)
    keys = [k async for k in fake_redis.scan_iter("auth:refresh:*")]
    await fake_redis.set(keys[0], f'{{"user_id": "{user.id}", "expires_at": "not-a-date"}}')

    payload = await validate_refresh_token(token_pair.refresh_token, token_config, fake_redis)
    assert payload is not None
    assert payload.user_id == user.id
    assert payload.expires_at is None


@pytest.mark.asyncio
async def test_rotate_refresh_token_issues_new_pair_and_invalidates_old(db_session, token_config, fake_redis):
    """Test that rotating a refresh token replaces it with a new one."""
    user = await create_user(db_session, "bob", "bob@example.com", "secret")
    await db_session.flush()

    original = await issue_token_pair(user, token_config, fake_redis)
    rotated = await rotate_refresh_token(original.refresh_token, token_config, fake_redis)

    assert rotated is not None
    assert rotated.refresh_token != original.refresh_token
    assert decode_access_token(rotated.access_token, SECRET_KEY) == user.id

    old_payload = await validate_refresh_token(original.refresh_token, token_config, fake_redis)
    assert old_payload is None

    new_payload = await validate_refresh_token(rotated.refresh_token, token_config, fake_redis)
    assert new_payload is not None
    assert new_payload.user_id == user.id


@pytest.mark.asyncio
async def test_rotate_refresh_token_returns_none_for_invalid_token(token_config, fake_redis):
    """Test that rotating an invalid refresh token returns None."""
    result = await rotate_refresh_token("not-a-real-token", token_config, fake_redis)
    assert result is None


@pytest.mark.asyncio
async def test_revoke_refresh_token_removes_token(db_session, token_config, fake_redis):
    """Test that revoking a refresh token deletes it from Redis."""
    user = await create_user(db_session, "carol", "carol@example.com", "secret")
    await db_session.flush()

    token_pair = await issue_token_pair(user, token_config, fake_redis)

    revoked = await revoke_refresh_token(token_pair.refresh_token, token_config, fake_redis)
    assert revoked is True

    payload = await validate_refresh_token(token_pair.refresh_token, token_config, fake_redis)
    assert payload is None


@pytest.mark.asyncio
async def test_revoke_refresh_token_returns_false_for_missing_token(token_config, fake_redis):
    """Test that revoking a missing token returns False."""
    revoked = await revoke_refresh_token("not-a-real-token", token_config, fake_redis)
    assert revoked is False


@pytest.mark.asyncio
async def test_refresh_token_not_stored_raw(db_session, token_config, fake_redis):
    """Test that the raw refresh token cannot be found in Redis."""
    user = await create_user(db_session, "dave", "dave@example.com", "secret")
    await db_session.flush()

    token_pair = await issue_token_pair(user, token_config, fake_redis)

    keys = [key async for key in fake_redis.scan_iter(match="*")]
    for key in keys:
        assert token_pair.refresh_token not in key
        value = await fake_redis.get(key)
        assert token_pair.refresh_token not in value


@pytest.mark.asyncio
async def test_refresh_token_ttl_matches_config(db_session, token_config, fake_redis):
    """Test that the refresh token is stored with the configured TTL."""
    user = await create_user(db_session, "eve", "eve@example.com", "secret")
    await db_session.flush()

    await issue_token_pair(user, token_config, fake_redis)

    keys = [k async for k in fake_redis.scan_iter("auth:refresh:*")]
    ttl = await fake_redis.ttl(keys[0])
    # 1-day TTL set in token_config -> 86400 s; allow a small margin.
    assert 86390 <= ttl <= 86400


@pytest.mark.asyncio
async def test_rotate_refresh_token_replay_returns_none(db_session, token_config, fake_redis):
    """Test that a refresh token cannot be rotated twice."""
    user = await create_user(db_session, "frank", "frank@example.com", "secret")
    await db_session.flush()

    original = await issue_token_pair(user, token_config, fake_redis)
    rotated = await rotate_refresh_token(original.refresh_token, token_config, fake_redis)
    assert rotated is not None

    replay = await rotate_refresh_token(original.refresh_token, token_config, fake_redis)
    assert replay is None
