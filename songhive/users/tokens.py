"""
Refresh token service: JWT access + opaque refresh token issuance,
validation, rotation, and revocation backed by Redis.
"""

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Optional, cast

from redis.asyncio import Redis
from redis.exceptions import WatchError

from ..api.middleware.auth import create_access_token
from ..config.schema import SonghiveConfig
from ..models.user import User

__all__ = [
    "TokenPair",
    "RefreshTokenPayload",
    "UserSession",
    "issue_token_pair",
    "validate_refresh_token",
    "rotate_refresh_token",
    "revoke_refresh_token",
    "revoke_all_user_refresh_tokens",
    "list_user_sessions",
    "revoke_session",
]


@dataclass
class TokenPair:
    """A pair of access and refresh tokens returned on login or refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 0


@dataclass
class RefreshTokenPayload:
    """Metadata stored with a refresh token in Redis."""

    user_id: str
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


@dataclass
class UserSession:
    """A user-facing view of an active refresh token session."""

    id: str
    user_id: str
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


def _hash_token(token: str) -> str:
    """Return a SHA-256 hash of the raw token used for Redis keys."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _refresh_key(token_hash: str) -> str:
    """Build the Redis key for a refresh token."""
    return f"auth:refresh:{token_hash}"


def _user_refresh_set_key(user_id: str) -> str:
    """Build the Redis key for a user's refresh token index set."""
    return f"auth:refresh-user:{user_id}"


async def _set_members(redis: Redis, key: str) -> set[str]:
    """Return the members of a Redis set as a set of strings."""
    members = await cast(Awaitable[set[str]], redis.smembers(key))
    return members


def _token_ttl(config: SonghiveConfig) -> int:
    """Return refresh token TTL in seconds."""
    return config.auth.refresh_token_expiry_days * 86400


def _encode_value(
    user_id: str,
    ttl: int,
    *,
    created_at: Optional[datetime] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> str:
    """Encode the refresh token payload as JSON."""
    now = datetime.now(timezone.utc)
    if created_at is None:
        created_at = now
    return json.dumps(
        {
            "user_id": user_id,
            "created_at": created_at.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
            "ip_address": ip_address,
            "user_agent": user_agent,
        },
        default=str,
    )


def _decode_value(value: Optional[str | bytes]) -> Optional[RefreshTokenPayload]:
    """Decode a JSON refresh token payload."""
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None
    user_id = data.get("user_id")
    if not user_id:
        return None

    expires_at: Optional[datetime] = None
    if data.get("expires_at"):
        try:
            expires_at = datetime.fromisoformat(data["expires_at"])
        except ValueError:
            expires_at = None

    created_at: Optional[datetime] = None
    if data.get("created_at"):
        try:
            created_at = datetime.fromisoformat(data["created_at"])
        except ValueError:
            created_at = None

    return RefreshTokenPayload(
        user_id=user_id,
        expires_at=expires_at,
        created_at=created_at,
        ip_address=data.get("ip_address") or None,
        user_agent=data.get("user_agent") or None,
    )


def _issue_token_pair_for_user_id(user_id: str, config: SonghiveConfig) -> TokenPair:
    """Create a new access/refresh token pair for the given user id."""
    # A value of 0 means the access token does not expire (OAuth2 convention).
    expires_minutes = config.auth.access_token_expiry_minutes or None
    access_token = create_access_token(
        user_id,
        config.auth.secret_key,
        expires_minutes=expires_minutes,
    )
    refresh_token = secrets.token_urlsafe(32)
    expires_in = (expires_minutes or 0) * 60
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=expires_in,
    )


async def issue_token_pair(
    user: User,
    config: SonghiveConfig,
    redis: Redis,
    *,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> TokenPair:
    """
    Issue a new access token and refresh token for a user.

    The refresh token is stored in Redis keyed by its SHA-256 hash, with the
    user's id as the payload and a TTL set by ``config.auth.refresh_token_expiry_days``.
    A hash is also added to the per-user refresh token set for bulk revocation.
    """
    token_pair = _issue_token_pair_for_user_id(user.id, config)
    token_hash = _hash_token(token_pair.refresh_token)
    key = _refresh_key(token_hash)
    user_set_key = _user_refresh_set_key(user.id)
    ttl = _token_ttl(config)
    value = _encode_value(
        user.id,
        ttl,
        created_at=created_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(key, value, ex=ttl)
        pipe.sadd(user_set_key, token_hash)
        pipe.expire(user_set_key, ttl)
        await pipe.execute()

    return token_pair


async def validate_refresh_token(token: str, redis: Redis) -> Optional[RefreshTokenPayload]:
    """
    Validate an opaque refresh token.

    Returns the stored payload if the token exists in Redis and is well-formed,
    otherwise ``None``.
    """
    token_hash = _hash_token(token)
    key = _refresh_key(token_hash)
    value = await redis.get(key)
    return _decode_value(value)


async def rotate_refresh_token(
    token: str,
    config: SonghiveConfig,
    redis: Redis,
    *,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Optional[TokenPair]:
    """
    Validate a refresh token and, if valid, issue a new pair and revoke the old one.

    The entire read-validate-claim-delete-set cycle runs inside a watched Redis
    transaction so concurrent callers can never rotate the same refresh token
    twice: exactly one caller's ``EXEC`` succeeds; the others receive a
    ``WatchError`` and ``None`` is returned.

    The returned pair contains the new raw refresh token the client should use.
    """
    old_hash = _hash_token(token)
    old_key = _refresh_key(old_hash)
    ttl = _token_ttl(config)

    async with redis.pipeline(transaction=True) as pipe:
        await pipe.watch(old_key)
        value = await pipe.get(old_key)
        payload = _decode_value(value)
        if payload is None:
            await pipe.reset()
            return None

        token_pair = _issue_token_pair_for_user_id(payload.user_id, config)
        new_hash = _hash_token(token_pair.refresh_token)
        new_key = _refresh_key(new_hash)
        new_value = _encode_value(
            payload.user_id,
            ttl,
            created_at=payload.created_at,
            ip_address=ip_address or payload.ip_address,
            user_agent=user_agent or payload.user_agent,
        )
        user_set_key = _user_refresh_set_key(payload.user_id)

        pipe.multi()
        pipe.delete(old_key)
        pipe.set(new_key, new_value, ex=ttl)
        pipe.srem(user_set_key, old_hash)
        pipe.sadd(user_set_key, new_hash)
        pipe.expire(user_set_key, ttl)
        try:
            await pipe.execute()
        except WatchError:
            return None

    return token_pair


async def revoke_refresh_token(token: str, redis: Redis) -> bool:
    """
    Revoke a refresh token by deleting it from Redis and removing it from the
    per-user refresh token index.

    Returns ``True`` if a token was deleted, ``False`` if it was not present.
    """
    token_hash = _hash_token(token)
    key = _refresh_key(token_hash)
    value = await redis.get(key)
    payload = _decode_value(value)

    if payload is None:
        # The token is absent or malformed. Try to delete the key anyway to
        # clean up stale data, but we cannot remove it from a user set.
        stale_deleted = await redis.delete(key)
        return stale_deleted > 0

    user_set_key = _user_refresh_set_key(payload.user_id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(key)
        pipe.srem(user_set_key, token_hash)
        results = await pipe.execute()
        deleted: int = results[0]

    return deleted > 0


async def revoke_all_user_refresh_tokens(redis: Redis, user_id: str) -> int:
    """
    Revoke every refresh token issued for a user by deleting the per-user set
    and all token keys it indexes.

    Returns the number of Redis keys deleted.
    """
    set_key = _user_refresh_set_key(user_id)
    token_hashes = await _set_members(redis, set_key)
    if not token_hashes:
        return 0

    keys = [_refresh_key(h) for h in token_hashes]
    keys.append(set_key)
    deleted: int = await redis.delete(*keys)
    return deleted


async def list_user_sessions(redis: Redis, user_id: str) -> list[UserSession]:
    """Return all active refresh token sessions for a user, newest first."""
    set_key = _user_refresh_set_key(user_id)
    token_hashes = await _set_members(redis, set_key)
    sessions: list[UserSession] = []

    for token_hash in token_hashes:
        key = _refresh_key(token_hash)
        value = await redis.get(key)
        payload = _decode_value(value)
        if payload is None or payload.user_id != user_id:
            # Clean up stale or malformed set entries.
            await cast(Awaitable[int], redis.srem(set_key, token_hash))
            continue

        sessions.append(
            UserSession(
                id=token_hash,
                user_id=payload.user_id,
                created_at=payload.created_at,
                expires_at=payload.expires_at,
                ip_address=payload.ip_address,
                user_agent=payload.user_agent,
            )
        )

    def _sort_key(session: UserSession) -> float:
        if session.created_at is not None:
            return session.created_at.timestamp()
        return 0.0

    sessions.sort(key=_sort_key, reverse=True)
    return sessions


async def revoke_session(redis: Redis, session_id: str, user_id: str) -> bool:
    """Revoke a single session by its id (token hash) for a user."""
    key = _refresh_key(session_id)
    value = await redis.get(key)
    payload = _decode_value(value)

    if payload is None or payload.user_id != user_id:
        return False

    user_set_key = _user_refresh_set_key(user_id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(key)
        pipe.srem(user_set_key, session_id)
        results = await pipe.execute()

    return bool(results[0])
