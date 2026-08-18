"""
Shared test fixtures.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.config.schema import SonghiveConfig
from songhive.models.base import Base, init_db
from songhive.models.invite import Invite  # noqa: F401
from songhive.models.user import User  # noqa: F401


@pytest.fixture
def fake_redis():
    """Create a fresh async fake Redis client for each test."""
    from fakeredis.aioredis import FakeRedis

    return FakeRedis(decode_responses=True)


@pytest.fixture
def config(tmp_path):
    """Create a test configuration backed by a fresh SQLite database."""
    return SonghiveConfig(
        server={"host": "127.0.0.1", "port": 8000, "debug": True},
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={"enabled": False},
        auth={"secret_key": "a" * 32},
    )


@pytest.fixture
def engine(tmp_path):
    """Create an async SQLAlchemy engine backed by a fresh SQLite database."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"
    engine = create_async_engine(url)

    async def _create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_tables())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
async def db_session(engine):
    """Create an async database session backed by the shared test engine."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def app(config, engine):
    """Create a test FastAPI application using the shared test engine."""
    init_db(engine=engine, force=True)
    return create_app(config)


@pytest.fixture
def client(app, db_session, fake_redis):
    """Create a test client with the test session and a fake Redis client."""
    with TestClient(app) as client:
        client.app.dependency_overrides[get_db] = _override_db(db_session)
        client.app.state.redis = fake_redis
        yield client
        client.app.dependency_overrides.pop(get_db, None)


def _override_db(session):
    """Return a FastAPI dependency that yields the provided session."""

    async def _db():
        yield session

    return _db
