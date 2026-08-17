"""
Shared test fixtures.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from songhive.api.app import create_app
from songhive.config.schema import SonghiveConfig
from songhive.models.base import Base
from songhive.models.user import User  # noqa: F401


@pytest.fixture
def config():
    """Create a test configuration."""
    return SonghiveConfig(
        server={"host": "127.0.0.1", "port": 8000, "debug": True},
        database={"url": "sqlite+aiosqlite:///test.db"},
        federation={"enabled": False},
    )


@pytest.fixture
async def db_session(tmp_path):
    """Create an async database session backed by a fresh SQLite database."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def app(config):
    """Create a test FastAPI application."""
    return create_app(config)


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)
