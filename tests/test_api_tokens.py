"""Tests for the API token Celery task."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from songhive.config.schema import SonghiveConfig
from songhive.models.api_token import ApiToken
from songhive.models.base import Base
from songhive.models.user import User
from songhive.tasks.api_tokens import flush_usage_timestamps


class _LocalSessionFactory:
    """Create a fresh async session backed by the given database URL."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    async def __aenter__(self):
        self.engine = create_async_engine(self.database_url, poolclass=NullPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.factory()
        await self.session.__aenter__()
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.__aexit__(exc_type, exc, tb)
        await self.engine.dispose()


class _FakeRedis:
    """In-memory fake Redis with the async interface used by the task."""

    def __init__(self):
        self._data = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    async def get(self, key):
        return self._data.get(key)

    async def delete(self, key):
        self._data.pop(key, None)
        return 1

    async def scan(self, cursor=0, match=None, count=None):
        prefix = match.replace("*", "") if match else ""
        keys = [k for k in self._data if k.startswith(prefix)]
        return 0, keys


def _patch_task_env(monkeypatch, db_url, media_dir):
    """Patch the task's dependencies so it runs against a test database."""
    import songhive.tasks.api_tokens as api_tokens_module

    config = SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": db_url},
        storage={"backend": "local", "local_path": str(media_dir)},
    )

    monkeypatch.setattr(api_tokens_module, "load_config", lambda *_: config)
    monkeypatch.setattr(api_tokens_module, "init_db", lambda url: None)

    @asynccontextmanager
    async def _get_session():
        async with _LocalSessionFactory(db_url) as session:
            yield session

    monkeypatch.setattr(api_tokens_module, "get_session", _get_session)


def test_flush_usage_timestamps_updates_last_used_at(tmp_path, monkeypatch):
    """The task flushes buffered last_used_at timestamps and closes Redis."""
    import songhive.tasks.api_tokens as api_tokens_module

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tokens.db'}"
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    _patch_task_env(monkeypatch, db_url, media_dir)

    async def _setup():
        async with _LocalSessionFactory(db_url) as session:
            user = User(username="alice", email="alice@example.com", password_hash="x")
            session.add(user)
            await session.flush()

            token = ApiToken(
                user_id=user.id,
                jti="token-jti-1",
                name="Test Token",
                last_used_at=None,
            )
            session.add(token)
            await session.commit()
            return token.jti

    jti = asyncio.run(_setup())

    now = datetime.now(timezone.utc).isoformat()
    fake_redis = _FakeRedis()
    fake_redis._data[f"api_token:last_used:{jti}"] = now

    close_mock = AsyncMock()
    monkeypatch.setattr(api_tokens_module, "get_redis_client", lambda _cfg: fake_redis)
    monkeypatch.setattr(api_tokens_module, "close_redis_client", close_mock)

    flush_usage_timestamps()

    close_mock.assert_awaited_once()
    assert f"api_token:last_used:{jti}" not in fake_redis._data

    async def _verify():
        async with _LocalSessionFactory(db_url) as session:
            result = await session.execute(select(ApiToken).where(ApiToken.jti == jti))
            token = result.scalar_one()
            assert token.last_used_at is not None
            assert token.last_used_at.isoformat() == now

    asyncio.run(_verify())
