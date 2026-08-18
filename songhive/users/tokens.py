"""
Refresh token service: JWT access + opaque refresh token issuance,
validation, rotation, and revocation backed by Redis.
"""

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from redis.asyncio import Redis
from redis.exceptions import WatchError

from ..api.middleware.auth import create_access_token
from ..config.schema import SonghiveConfig
from ..models.user import User

__all__ = [
    "TokenPair",
    "RefreshTokenPayload",
    "issue_token_pair",
    "validate_refresh_token",
    "rotate_refresh_token",
    "revoke_refresh_token",
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


def _hash_token(token: str) -> str:
    """Return a SHA-256 hash of the raw token used for Redis keys."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _refresh_key(token_hash: str) -> str:
    """Build the Redis key for a refresh token."""
    return f"auth:refresh:{token_hash}"


def _token_ttl(config: SonghiveConfig) -> int:
    """Return refresh token TTL in seconds."""
    return config.auth.refresh_token_expiry_days * 86400


def _encode_value(user_id: str, ttl: int) -> str:
    """Encode the refresh token payload as JSON."""
    now = datetime.now(timezone.utc)
    return json.dumps(
        {
            "user_id": user_id,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        }
    )


def _decode_value(value: Optional[str]) -> Optional[RefreshTokenPayload]:
    """Decode a JSON refresh token payload."""
    if not value:
        return None
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
    return RefreshTokenPayload(user_id=user_id, expires_at=expires_at)


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


async def issue_token_pair(user: User, config: SonghiveConfig, redis: Redis) -> TokenPair:
    """
    Issue a new access token and refresh token for a user.

    The refresh token is stored in Redis keyed by its SHA-256 hash, with the
    user's id as the payload and a TTL set by ``config.auth.refresh_token_expiry_days``.
    """
    token_pair = _issue_token_pair_for_user_id(user.id, config)
    token_hash = _hash_token(token_pair.refresh_token)
    key = _refresh_key(token_hash)
    ttl = _token_ttl(config)
    value = _encode_value(user.id, ttl)
    await redis.set(key, value, ex=ttl)
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


async def rotate_refresh_token(token: str, config: SonghiveConfig, redis: Redis) -> Optional[TokenPair]:
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
        new_value = _encode_value(payload.user_id, ttl)

        pipe.multi()
        pipe.delete(old_key)
        pipe.set(new_key, new_value, ex=ttl)
        try:
            await pipe.execute()
        except WatchError:
            return None

    return token_pair


async def revoke_refresh_token(token: str, redis: Redis) -> bool:
    """
    Revoke a refresh token by deleting it from Redis.

    Returns ``True`` if a token was deleted, ``False`` if it was not present.
    """
    token_hash = _hash_token(token)
    key = _refresh_key(token_hash)
    deleted: int = await redis.delete(key)
    return deleted > 0
