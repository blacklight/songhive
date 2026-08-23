"""
API token service: issuance, validation, listing, counting, and revocation.
"""

import secrets
from datetime import datetime, timezone
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.middleware.auth import create_api_token_jwt
from ..config.schema import SonghiveConfig
from ..models.api_token import ApiToken
from ..models.user import User
from ..services.api_token_tracker import flush_api_token_usage, track_api_token_usage

__all__ = [
    "ApiTokenError",
    "issue_api_token",
    "get_api_token_by_jti",
    "validate_api_token",
    "list_user_api_tokens",
    "count_user_api_tokens",
    "revoke_api_token",
]


class ApiTokenError(ValueError):
    """Raised when an API-token operation fails."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def issue_api_token(
    db: AsyncSession,
    user: User,
    config: SonghiveConfig,
    name: str,
    expires_at: Optional[datetime],
) -> tuple[ApiToken, str]:
    """Create and persist a new API token, returning the row and raw JWT."""
    jti = secrets.token_urlsafe(32)
    raw_jwt = create_api_token_jwt(user.id, config.auth.secret_key, jti, expires_at)
    api_token = ApiToken(
        user_id=user.id,
        jti=jti,
        name=name,
        expires_at=expires_at,
    )
    db.add(api_token)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ApiTokenError("Name already in use", status_code=409) from None

    return api_token, raw_jwt


async def get_api_token_by_jti(db: AsyncSession, jti: str) -> Optional[ApiToken]:
    """Return the API token row with the given ``jti``, or None."""
    result = await db.execute(select(ApiToken).where(ApiToken.jti == jti))
    return result.scalar_one_or_none()


async def validate_api_token(
    db: AsyncSession,
    jti: str,
    redis: Optional[Redis] = None,
) -> Optional[ApiToken]:
    """
    Return the active API token for ``jti`` and track its usage.

    If ``redis`` is provided, the usage timestamp is buffered in Redis and
    flushed to the database periodically. Otherwise, the timestamp is written
    directly to the database (legacy behavior for tests or when Redis is unavailable).
    """
    api_token = await get_api_token_by_jti(db, jti)
    if api_token is None:
        return None
    if api_token.revoked_at is not None:
        return None
    if api_token.expires_at is not None and api_token.expires_at <= datetime.now(timezone.utc):
        return None

    # Buffer usage in Redis if available, otherwise write directly to DB
    if redis is not None:
        await track_api_token_usage(redis, jti)
    else:
        api_token.last_used_at = datetime.now(timezone.utc)
        try:
            await db.flush()
        except Exception:
            # Best-effort update: a failure here must not fail the request.
            pass

    return api_token


async def list_user_api_tokens(
    db: AsyncSession,
    user_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[ApiToken]:
    """Return a paginated list of API tokens for a user, newest first."""
    stmt = (
        select(ApiToken)
        .where(ApiToken.user_id == user_id)
        .order_by(ApiToken.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def count_user_api_tokens(db: AsyncSession, user_id: str) -> int:
    """Return the total number of API tokens for a user."""
    stmt = select(func.count()).select_from(ApiToken).where(ApiToken.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one()


async def revoke_api_token(
    db: AsyncSession,
    token_id: str,
    user_id: str,
    redis: Optional[Redis] = None,
) -> bool:
    """
    Revoke an API token, returning True if it belonged to ``user_id``.

    If ``redis`` is provided, any buffered usage timestamp is flushed to the
    database before revocation to ensure the final usage time is persisted.
    """
    stmt = select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user_id)
    result = await db.execute(stmt)
    api_token = result.scalar_one_or_none()

    if api_token is None:
        return False

    # Flush buffered usage before revoking
    if redis is not None:
        await flush_api_token_usage(db, redis, api_token.jti)

    api_token.revoked_at = datetime.now(timezone.utc)
    await db.flush()
    return True
