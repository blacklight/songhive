"""
Shared async Redis client helpers.

Provides lazily-initialized Redis clients used by refresh token storage,
OAuth2 token storage, rate limiting, and other auth/state needs. Clients are
created from ``config.redis.url`` and reused via a connection pool.

``redis.asyncio`` connections bind to the event loop that opened them, and
Songhive runs several loops in one process — Tornado's main loop, the a2wsgi
ASGI bridge loop, a fresh ``asyncio.run`` loop per Celery task, and temporary
startup loops. A pooled connection awaited on a different loop raises
``RuntimeError: ... Future attached to a different loop``. The shared client
is therefore cached *per running loop* so a caller can never receive a
client whose connections were opened on a different loop.
"""

import asyncio
import logging
import threading
from typing import Optional

import redis as sync_redis
from redis.asyncio import Redis

from ..config.schema import SonghiveConfig

logger = logging.getLogger(__name__)

# Client for callers without a running event loop (application startup; it
# becomes ``app.state.redis`` and only ever serves the request loop).
_redis_client: Optional[Redis] = None
# One shared client per running event loop; see module docstring.
_loop_clients: dict[asyncio.AbstractEventLoop, Redis] = {}
# get_redis_client runs on loop threads (a2wsgi, Tornado) and plain threads.
_registry_lock = threading.Lock()
_sync_redis_client: Optional[sync_redis.Redis] = None


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Return the running event loop, or ``None`` in synchronous context."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _prune_loop_clients() -> None:
    """Drop entries bound to closed loops; callers must hold ``_registry_lock``.

    A closed loop can never serve another command, so the client is dead
    weight; its sockets are released by the garbage collector.
    """
    for loop in [key for key in _loop_clients if key.is_closed()]:
        del _loop_clients[loop]


def get_redis_client(config: SonghiveConfig) -> Redis:
    """
    Return a shared async Redis client for the current event loop.

    Calls made while a loop is running get a client dedicated to that loop;
    calls with no running loop share ``_redis_client`` (the client stored on
    ``app.state.redis`` at startup). The client is created lazily from
    ``config.redis.url`` and cached for the process lifetime (or the loop's).
    Callers that need a different client in tests can patch
    ``redis.asyncio.Redis.from_url`` or monkeypatch
    ``songhive.services.redis._redis_client`` directly.

    Use :func:`create_redis_client` for a non-shared instance — e.g. when the
    caller must hand a dedicated client to a specific loop.
    """
    global _redis_client
    loop = _running_loop()
    with _registry_lock:
        _prune_loop_clients()
        if loop is None:
            if _redis_client is None:
                _redis_client = create_redis_client(config)
                logger.info("Initialized shared Redis client")

            assert _redis_client  # for mypy
            return _redis_client
        client = _loop_clients.get(loop)
        if client is None:
            client = create_redis_client(config)
            _loop_clients[loop] = client
            logger.info("Initialized shared Redis client for event loop")
        return client


def create_redis_client(config: SonghiveConfig) -> Redis:
    """
    Create a fresh, non-shared async Redis client.

    Unlike :func:`get_redis_client`, this always returns a new instance rather
    than reusing a cached one. This is required when a Redis client must bind
    to a specific event loop — for example, the Tornado request handlers run
    on the main Tornado loop and get a dedicated client for the process
    lifetime rather than an entry in the per-loop registry.
    """
    return Redis.from_url(
        config.redis.url,
        decode_responses=True,
    )


def get_sync_redis_client(config: Optional[SonghiveConfig] = None) -> sync_redis.Redis:
    """
    Return a shared synchronous Redis client for the process.

    Used to publish WebSocket event envelopes from contexts where an async
    client cannot run — Celery tasks execute each job in a fresh
    ``asyncio.run`` loop, so a cached async client would break with
    "Future attached to a different loop". A synchronous client is
    thread-safe and works from any loop or thread. When ``config`` is omitted
    it is derived from ``load_config([])``, matching the Celery tasks.
    """
    global _sync_redis_client
    if _sync_redis_client is None:
        if config is None:
            from ..config import load_config

            config = load_config([])
        _sync_redis_client = sync_redis.Redis.from_url(config.redis.url)
        assert _sync_redis_client  # for mypy
    return _sync_redis_client


async def close_redis_client(client: Optional[Redis] = None) -> None:
    """
    Close an async Redis client.

    With an explicit ``client`` only that instance is closed (e.g. a dedicated
    client returned by :func:`create_redis_client`). Without one, the shared
    client registered for the *current* event loop is closed and dropped,
    along with the default no-loop client — that default is the
    ``app.state.redis`` client whose pooled connections live on the
    request-handling loop this is typically called from at shutdown. Clients
    registered to other live loops are left alone; entries bound to loops
    that already closed are pruned.
    """
    global _redis_client
    if client is not None:
        clients = [client]
    else:
        loop = _running_loop()
        clients = []
        with _registry_lock:
            _prune_loop_clients()
            if loop is not None:
                current = _loop_clients.pop(loop, None)
                if current is not None:
                    clients.append(current)
            if _redis_client is not None:
                clients.append(_redis_client)
                _redis_client = None
    for target in clients:
        try:
            await target.aclose()
        except Exception:
            logger.exception("Error closing shared Redis client")
        else:
            logger.info("Closed shared Redis client")
