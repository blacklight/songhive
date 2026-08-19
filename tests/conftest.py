"""
Shared test fixtures.
"""

import asyncio
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.api.middleware.auth import create_access_token
from songhive.config.schema import SonghiveConfig
from songhive.models.base import Base, init_db
from songhive.models.invite import Invite  # noqa: F401
from songhive.models.oauth_client import OAuth2Client  # noqa: F401
from songhive.models.stored_file import StoredFile  # noqa: F401
from songhive.models.user import User  # noqa: F401
from songhive.services.auth import create_user


@pytest.fixture(autouse=True)
def _ensure_test_secret_key(monkeypatch):
    """Provide a fallback JWT secret key so tests can build a SonghiveConfig."""
    monkeypatch.setenv("SONGHIVE_AUTH__SECRET_KEY", "a" * 64)


@pytest.fixture
def fake_redis():
    """Create a fresh async fake Redis client for each test."""
    from fakeredis.aioredis import FakeRedis

    return FakeRedis(decode_responses=True)


@pytest.fixture
def config(tmp_path):
    """Create a test configuration backed by a fresh SQLite database."""
    return SonghiveConfig(
        server={
            "host": "127.0.0.1",
            "port": 8000,
            "debug": True,
            "cors_origins": ["http://localhost:8080"],
        },
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
def client(app, db_session, fake_redis, monkeypatch):
    """Create a test client with the test session and a fake Redis client."""
    # Ensure the application lifespan uses the per-test fake Redis instead of
    # trying to connect to a real Redis server.
    monkeypatch.setattr("songhive.api.app.get_redis_client", lambda _config: fake_redis)

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


@pytest.fixture
def make_user(db_session):
    """Return a helper that creates a user and flushes it to the test session."""

    async def _make_user(
        username: str,
        email: Optional[str] = None,
        password: str = "secret",
        role: str = "user",
        is_active: bool = True,
        email_verified: Optional[bool] = None,
    ) -> User:
        if email is None:
            email = f"{username}@example.com"
        user = await create_user(
            db_session,
            username=username,
            email=email,
            password=password,
            role=role,
            is_active=is_active,
        )
        if email_verified is not None:
            user.email_verified = email_verified
        await db_session.flush()
        return user

    return _make_user


@pytest.fixture
async def regular_user(make_user):
    """Create a regular, active, email-verified user."""
    return await make_user("regular", email_verified=True)


@pytest.fixture
async def moderator_user(make_user):
    """Create a user with the moderator role."""
    return await make_user("moderator", role="moderator", email_verified=True)


@pytest.fixture
async def admin_user(make_user):
    """Create an admin user."""
    return await make_user("admin", role="admin", email_verified=True)


@pytest.fixture
async def inactive_user(make_user):
    """Create an inactive user."""
    return await make_user("inactive", is_active=False)


@pytest.fixture
async def unverified_user(make_user):
    """Create an active but unverified user."""
    return await make_user("unverified", email_verified=False)


@pytest.fixture
def auth_headers(config):
    """Return a helper that builds an Authorization header for a user."""

    def _auth_headers(user: User) -> dict[str, str]:
        token = create_access_token(str(user.id), config.auth.secret_key)
        return {"Authorization": f"Bearer {token}"}

    return _auth_headers
