"""
Rate limiting middleware: Redis-backed sliding-window per-endpoint rate limiting.

Provides FastAPI dependencies and a helper for checking rate limits. The limiter
is conservative: if Redis is unavailable, it fails open and allows the request.
"""

import logging
import math
import time
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.stored_file import StoredFile
from ...models.user import User
from .._common import client_ip
from ..deps import get_config, get_current_user, get_current_user_optional, get_db, get_redis

logger = logging.getLogger(__name__)


def _client_ip(request: Request, trusted_hops: int = 0) -> str:
    """Return the client IP address, honoring trusted X-Forwarded-For hops."""
    return client_ip(request, trusted_hops=trusted_hops) or "unknown"


def _rate_limit_key(scope: str, path: str, identifier: Optional[str] = None) -> str:
    """Build a Redis key for a scope and endpoint (plus optional identifier)."""
    key = f"rl:{scope}:{path}"
    if identifier:
        key = f"{key}:{identifier}"
    return key


def _retry_after(oldest_score: float, window: int, now: float) -> int:
    """Compute the number of seconds a client should wait before retrying."""
    retry_after = math.ceil(oldest_score + window - now)
    return max(retry_after, 1)


async def _check_rate_limit(
    request: Request,
    config: SonghiveConfig,
    redis: Redis,
    scope: str,
    identifier: Optional[str] = None,
    *,
    limit: Optional[int] = None,
    window: Optional[int] = None,
) -> None:
    """
    Enforce a sliding-window rate limit for the request.

    The window is stored as a Redis sorted set keyed by ``scope`` (a client IP
    or user id) and endpoint path. An optional ``identifier`` (e.g. the
    submitted username for ``/login``) can be added to the key for additional
    per-account limiting. ``limit``/``window`` override the
    ``auth.rate_limit_*`` defaults; a limit of 0 or less disables the check.

    If Redis is unavailable, the limiter logs a warning without secrets and
    allows the request to proceed.
    """
    if not config.auth.rate_limit_enabled:
        return

    path = request.url.path
    key = _rate_limit_key(scope, path, identifier)
    if window is None:
        window = config.auth.rate_limit_window_seconds
    if limit is None:
        limit = config.auth.rate_limit_requests
    if limit <= 0:
        return

    now = time.time()
    member = f"{now}:{uuid.uuid4().hex}"
    min_score = now - window

    try:
        pipeline = redis.pipeline()
        pipeline.zremrangebyscore(key, 0, min_score)
        pipeline.zadd(key, {member: now})
        pipeline.zrange(key, 0, 0, withscores=True)
        pipeline.zcard(key)
        pipeline.expire(key, window * 2)
        _, _, oldest, count, _ = await pipeline.execute()
    except Exception:
        logger.warning("Rate limiting unavailable; allowing request")
        return

    if count > limit:
        if not oldest:
            return
        oldest_score = float(oldest[0][1])
        retry_after = _retry_after(oldest_score, window, now)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


async def check_rate_limit(
    request: Request,
    config: SonghiveConfig,
    redis: Redis,
    identifier: Optional[str] = None,
) -> None:
    """
    Enforce a sliding-window rate limit scoped by client IP.

    This helper is used directly by route handlers (e.g. ``/login``) that need
    to rate limit by an additional identifier before the user is authenticated.
    """
    scope = _client_ip(request, trusted_hops=config.auth.trusted_proxy_hops)
    await _check_rate_limit(request, config, redis, scope, identifier=identifier)


async def rate_limit(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
) -> None:
    """FastAPI dependency for IP-based sliding-window rate limiting."""
    await check_rate_limit(request, config, redis)


def _user_or_ip_scope(request: Request, config: SonghiveConfig, user: Optional[User]) -> str:
    """Return the rate-limit scope: user id when authenticated, else client IP."""
    if user is not None:
        return str(user.id)
    return _client_ip(request, trusted_hops=config.auth.trusted_proxy_hops)


async def rate_limit_user_or_ip(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
    user: Optional[User] = Depends(get_current_user_optional),
) -> None:
    """FastAPI dependency that keys the limit by user id when authenticated, else IP."""
    scope = _user_or_ip_scope(request, config, user)
    await _check_rate_limit(request, config, redis, scope=scope)


async def rate_limit_media(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
    user: Optional[User] = Depends(get_current_user_optional),
) -> None:
    """FastAPI dependency for byte-serving endpoints under the looser media budget.

    Keys the window by user id when authenticated (else client IP) and applies
    ``auth.rate_limit_media_requests`` instead of ``rate_limit_requests``, so
    media downloads are not choked by the budget meant for sensitive
    endpoints. ``rate_limit_media_requests = 0`` disables the check.
    """
    scope = _user_or_ip_scope(request, config, user)
    await _check_rate_limit(
        request,
        config,
        redis,
        scope=scope,
        limit=config.auth.rate_limit_media_requests,
    )


async def rate_limit_file_download(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> None:
    """FastAPI dependency for ``files/{file_id}/download``.

    ``image/*`` stored files (avatars, covers, post attachments) are exempt:
    they are small, immutable, and fetched in bursts by remote Fediverse
    instances whose fetches all share one egress IP. Other content types fall
    back to the media budget via :func:`rate_limit_media`. The file row is
    already loaded by the route's ``require_access`` dependency, so the
    ``db.get`` here is an identity-map lookup.
    """
    file_id = request.path_params.get("file_id")
    if file_id:
        stored = await db.get(StoredFile, file_id)
        if stored is not None and (stored.content_type or "").startswith("image/"):
            return
    await rate_limit_media(request, config, redis, user)


async def rate_limit_account(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> None:
    """FastAPI dependency for per-user sliding-window rate limiting."""
    await _check_rate_limit(request, config, redis, scope=str(current_user.id))
