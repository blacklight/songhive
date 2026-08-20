"""
Rate limiting middleware: Redis-backed fixed-window per-endpoint rate limiting.

Provides a FastAPI dependency and a helper for checking rate limits. The limiter
is conservative: if Redis is unavailable, it fails open and allows the request.
"""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis

from ...config.schema import SonghiveConfig
from ...models.user import User
from ..deps import get_config, get_current_user, get_redis

logger = logging.getLogger(__name__)


def _client_ip(request: Request, trusted_hops: int = 0) -> str:
    """Return the client IP address, honoring trusted X-Forwarded-For hops."""
    forwarded: str | None = request.headers.get("X-Forwarded-For")
    if forwarded and trusted_hops > 0:
        parts = [ip.strip() for ip in forwarded.split(",")]
        if len(parts) > trusted_hops:
            return parts[-(trusted_hops + 1)]

    real_ip: str | None = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def _rate_limit_key(ip: str, path: str, identifier: Optional[str] = None) -> str:
    """Build a Redis key for a client IP and endpoint (plus optional identifier)."""
    key = f"rate:{ip}:{path}"
    if identifier:
        key = f"{key}:{identifier}"
    return key


async def check_rate_limit(
    request: Request,
    config: SonghiveConfig,
    redis: Redis,
    identifier: Optional[str] = None,
) -> None:
    """
    Increment and check a fixed-window rate limit for the request.

    The window is anchored at the first request in the window and is keyed by
    client IP and endpoint path. An optional ``identifier`` (e.g. the submitted
    username for ``/login``) can be added to the key for per-account limiting.

    If Redis is unavailable, the limiter logs a warning without secrets and
    allows the request to proceed.
    """
    if not config.auth.rate_limit_enabled:
        return

    ip = _client_ip(request, trusted_hops=config.auth.trusted_proxy_hops)
    path = request.url.path
    key = _rate_limit_key(ip, path, identifier)
    window = config.auth.rate_limit_window_seconds
    limit = config.auth.rate_limit_requests

    try:
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, window)
    except Exception:
        logger.warning("Rate limiting unavailable; allowing request")
        return

    if current > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later.",
        )


async def rate_limit(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
) -> None:
    """FastAPI dependency for IP + endpoint path based rate limiting."""
    await check_rate_limit(request, config, redis)


async def rate_limit_account(
    request: Request,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> None:
    """FastAPI dependency for per-account rate limiting by the current user."""
    await check_rate_limit(request, config, redis, identifier=current_user.username)
