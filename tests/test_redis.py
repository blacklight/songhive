"""
Tests for the shared Redis service helper.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from songhive.config.schema import SonghiveConfig
from songhive.services import redis as redis_module
from songhive.services.redis import close_redis_client, create_redis_client, get_redis_client


@pytest.fixture(autouse=True)
def _reset_redis_client():
    """Reset the shared Redis client registries before each test."""
    redis_module._redis_client = None
    redis_module._loop_clients.clear()
    yield
    redis_module._redis_client = None
    redis_module._loop_clients.clear()


def _fake_redis():
    """Return a fake Redis client with an async aclose method."""
    fake = MagicMock()
    fake.aclose = AsyncMock()
    return fake


def test_get_redis_client_uses_config_url(monkeypatch):
    """Test that the client is created from config.redis.url with a shared pool."""
    fake = _fake_redis()
    from_url = MagicMock(return_value=fake)
    monkeypatch.setattr(redis_module.Redis, "from_url", from_url)

    config = SonghiveConfig(redis={"url": "redis://localhost:6379/5"})
    client = get_redis_client(config)

    assert client is fake
    from_url.assert_called_once_with(config.redis.url, decode_responses=True)
    assert redis_module._redis_client is fake


def test_get_redis_client_returns_singleton(monkeypatch):
    """Test that the same client is returned on subsequent calls."""
    fake = _fake_redis()
    monkeypatch.setattr(redis_module.Redis, "from_url", MagicMock(return_value=fake))

    config = SonghiveConfig()
    first = get_redis_client(config)
    second = get_redis_client(config)

    assert first is second
    redis_module.Redis.from_url.assert_called_once()


def test_create_redis_client_returns_fresh_instance(monkeypatch):
    """create_redis_client always returns a new, non-shared client."""
    fake_a = _fake_redis()
    fake_b = _fake_redis()
    from_url = MagicMock(side_effect=[fake_a, fake_b])
    monkeypatch.setattr(redis_module.Redis, "from_url", from_url)

    config = SonghiveConfig(redis={"url": "redis://localhost:6379/5"})
    dedicated = create_redis_client(config)

    assert dedicated is fake_a
    from_url.assert_called_once_with(config.redis.url, decode_responses=True)
    # The singleton is untouched.
    assert redis_module._redis_client is None

    shared = get_redis_client(config)
    assert shared is fake_b
    assert shared is not dedicated


@pytest.mark.asyncio
async def test_close_redis_client_closes_explicit_client(monkeypatch):
    """close_redis_client(client) closes the given client without touching the registry."""
    dedicated = _fake_redis()
    singleton = _fake_redis()
    monkeypatch.setattr(redis_module.Redis, "from_url", MagicMock(return_value=singleton))

    config = SonghiveConfig()
    get_redis_client(config)  # populate the shared client for this loop
    loop = asyncio.get_running_loop()
    assert redis_module._loop_clients[loop] is singleton

    await close_redis_client(dedicated)

    dedicated.aclose.assert_awaited_once()
    # The shared client is left intact.
    assert redis_module._loop_clients[loop] is singleton
    singleton.aclose.assert_not_called()


@pytest.mark.asyncio
async def test_close_redis_client_closes_and_clears_singleton(monkeypatch):
    """Test that close_redis_client acloses the client and resets the singleton."""
    fake = _fake_redis()
    monkeypatch.setattr(redis_module.Redis, "from_url", MagicMock(return_value=fake))

    config = SonghiveConfig()
    client = get_redis_client(config)
    assert client is fake

    await close_redis_client()

    fake.aclose.assert_awaited_once()
    assert redis_module._redis_client is None


@pytest.mark.asyncio
async def test_close_redis_client_is_noop_when_not_initialized():
    """Test that close_redis_client is safe when no client was created."""
    redis_module._redis_client = None
    await close_redis_client()
    assert redis_module._redis_client is None


@pytest.mark.asyncio
async def test_get_redis_client_after_close_creates_new_client(monkeypatch):
    """Test that a new client is created after the previous one is closed."""
    first = _fake_redis()
    second = _fake_redis()
    from_url = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(redis_module.Redis, "from_url", from_url)

    config = SonghiveConfig()
    assert get_redis_client(config) is first

    await close_redis_client()
    assert get_redis_client(config) is second


@pytest.mark.asyncio
async def test_close_redis_client_clears_singleton_on_error(monkeypatch):
    """Test that the singleton is reset even when aclose raises."""
    fake = _fake_redis()
    fake.aclose = AsyncMock(side_effect=RuntimeError("event loop is closed"))
    monkeypatch.setattr(redis_module.Redis, "from_url", MagicMock(return_value=fake))

    config = SonghiveConfig()
    client = get_redis_client(config)
    assert client is fake

    await close_redis_client()

    fake.aclose.assert_awaited_once()
    assert redis_module._redis_client is None


def test_redis_client_recreated_across_event_loops(monkeypatch):
    """Celery tasks call asyncio.run for each task; the client must not be reused."""
    first = _fake_redis()
    second = _fake_redis()
    from_url = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(redis_module.Redis, "from_url", from_url)

    config = SonghiveConfig()

    async def _use():
        client = get_redis_client(config)
        assert client is first
        await close_redis_client()

    async def _use_again():
        client = get_redis_client(config)
        assert client is second
        await close_redis_client()

    asyncio.run(_use())
    asyncio.run(_use_again())

    assert from_url.call_count == 2


def test_get_redis_client_is_scoped_to_the_running_loop(monkeypatch):
    """Each running event loop gets its own client; loops never share a pool.

    Regression test for the Tornado/a2wsgi split: code running on the Tornado
    loop (e.g. external-library streaming) previously received the same
    shared client as FastAPI requests on the a2wsgi loop, so pooled
    connections bound to one loop were later awaited on the other —
    "Future attached to a different loop".
    """
    first = _fake_redis()
    second = _fake_redis()
    from_url = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(redis_module.Redis, "from_url", from_url)

    config = SonghiveConfig()

    async def _get():
        return get_redis_client(config)

    loop_one = asyncio.run(_get())
    loop_two = asyncio.run(_get())

    assert loop_one is first
    assert loop_two is second
    assert loop_one is not loop_two
    assert from_url.call_count == 2


def test_get_redis_client_without_loop_returns_default(monkeypatch):
    """Calls with no running loop share the ``app.state.redis`` default."""
    default = _fake_redis()
    in_loop = _fake_redis()
    from_url = MagicMock(side_effect=[default, in_loop])
    monkeypatch.setattr(redis_module.Redis, "from_url", from_url)

    config = SonghiveConfig()

    assert get_redis_client(config) is default
    assert redis_module._redis_client is default

    async def _get():
        return get_redis_client(config)

    # A running loop must not receive the default client — its connections
    # would be bound to whatever loop used it first.
    assert asyncio.run(_get()) is in_loop


def test_close_redis_client_closes_default_and_loop_clients(monkeypatch):
    """close_redis_client() closes the current loop's client and the default."""
    default = _fake_redis()
    loop_client = _fake_redis()
    from_url = MagicMock(side_effect=[default, loop_client])
    monkeypatch.setattr(redis_module.Redis, "from_url", from_url)

    config = SonghiveConfig()
    # No running loop here: this creates the ``app.state.redis`` default.
    assert get_redis_client(config) is default

    async def _run():
        # get_redis_client now runs inside an event loop.
        assert get_redis_client(config) is loop_client
        await close_redis_client()

    asyncio.run(_run())

    default.aclose.assert_awaited_once()
    loop_client.aclose.assert_awaited_once()
    assert redis_module._redis_client is None
    assert not redis_module._loop_clients
