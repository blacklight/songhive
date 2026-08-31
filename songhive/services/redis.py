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

    .. note::
        The returned client's connection pool binds to the event loop of the
        first call site. When Tornado and FastAPI run on different event loops
        (as they do when bridged via ``a2wsgi``), each side must use its own
        client. Use :func:`create_redis_client` for a non-shared instance.
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = create_redis_client(config)
        logger.info("Initialized shared Redis client")
    return _redis_client


def create_redis_client(config: SonghiveConfig) -> Redis:
    """
    Create a fresh, non-shared async Redis client.

    Unlike :func:`get_redis_client`, this always returns a new instance rather
    than reusing the process-wide singleton. This is required when a Redis
    client must bind to a specific event loop that differs from the one the
    shared client is bound to — for example, the Tornado request handlers run
    on the main Tornado loop while the shared client is bound to the
    ``a2wsgi`` loop used by FastAPI.
    """
    return Redis.from_url(
        config.redis.url,
        decode_responses=True,
    )


async def close_redis_client(client: Optional[Redis] = None) -> None:
    """
    Close an async Redis client.

    By default this closes the shared singleton client (and clears the
    cache). Pass an explicit ``client`` to close a non-shared instance
    returned by :func:`create_redis_client` without touching the singleton.
    """
    global _redis_client
    if client is None:
        client = _redis_client
        _redis_client = None
    if client is not None:
        try:
            await client.aclose()
        except Exception:
            logger.exception("Error closing shared Redis client")
        else:
            logger.info("Closed shared Redis client")
