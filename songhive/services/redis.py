"""
Shared async Redis client helper.

Provides a single, lazily-initialized Redis client that can be used by refresh
token storage, OAuth2 token storage, rate limiting, and other auth/state needs.
The client is created from `config.redis.url` and reused via a connection pool.
"""

import logging
from typing import Optional

from redis.asyncio import Redis

from ..config.schema import SonghiveConfig

logger = logging.getLogger(__name__)

_redis_client: Optional[Redis] = None


def get_redis_client(config: SonghiveConfig) -> Redis:
    """
    Return a shared async Redis client for the application.

    The client is created lazily from ``config.redis.url`` and cached for the
    process lifetime. Callers that need a different client in tests can patch
    ``redis.asyncio.Redis.from_url`` or monkeypatch
    ``songhive.services.redis._redis_client`` directly.
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(
            config.redis.url,
            decode_responses=True,
        )
        logger.info("Initialized shared Redis client")
    return _redis_client


async def close_redis_client() -> None:
    """Close the shared async Redis client, if it has been initialized."""
    global _redis_client
    client = _redis_client
    _redis_client = None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            logger.exception("Error closing shared Redis client")
        else:
            logger.info("Closed shared Redis client")
