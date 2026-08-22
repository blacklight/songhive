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

from ...config.schema import SonghiveConfig
from ...models.user import User
from .._common import client_ip
from ..deps import get_config, get_current_user, get_current_user_optional, get_redis

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
) -> None:
    """
    Enforce a sliding-window rate limit for the request.

    The window is stored as a Redis sorted set keyed by ``scope`` (a client IP
    or user id) and endpoint path. An optional ``identifier`` (e.g. the
    submitted username for ``/login``) can be added to the key for additional
    per-account limiting.

    If Redis is unavailable, the limiter logs a warning without secrets and
    allows the request to proceed.
    """
    if not config.auth.rate_limit_enabled:
        return

    path = request.url.path
    key = _rate_limit_key(scope, path, identifier)
    window = config.auth.rate_limit_window_seconds
    limit = config.auth.rate_limit_requests

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


async def rate_limit_user_or_ip(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
    user: Optional[User] = Depends(get_current_user_optional),
) -> None:
    """FastAPI dependency that keys the limit by user id when authenticated, else IP."""
    scope = str(user.id) if user is not None else _client_ip(request, trusted_hops=config.auth.trusted_proxy_hops)
    await _check_rate_limit(request, config, redis, scope=scope)


async def rate_limit_account(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> None:
    """FastAPI dependency for per-user sliding-window rate limiting."""
    await _check_rate_limit(request, config, redis, scope=str(current_user.id))
