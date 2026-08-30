"""
SQLAlchemy base configuration and session management.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from sqlalchemy import DateTime, TypeDecorator, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)


class TZDateTime(TypeDecorator):
    """
    A DateTime type that enforces timezone-aware datetimes.

    Raises ValueError if a naive datetime is assigned. This prevents defensive
    ``if tzinfo is None`` checks throughout the codebase.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], *_, **__) -> Optional[datetime]:
        """Validate that the datetime is timezone-aware before binding."""
        if value is not None and value.tzinfo is None:
            raise ValueError(f"Naive datetime not allowed: {value!r}")
        return value

    def process_result_value(self, value: Optional[datetime], *_, **__) -> Optional[datetime]:
        """Ensure the datetime returned from the DB is timezone-aware."""
        if value is not None and value.tzinfo is None:
            # Defensive: assume UTC if the DB returns a naive datetime
            return value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )


_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def reset_db() -> None:
    """
    Clear the shared engine and session factory without disposing them.

    Tests should call this after disposing an engine they installed, so
    subsequent tests do not inherit a stale global.
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None


async def dispose_engine() -> None:
    """Dispose the shared engine, dropping all pooled connections.

    asyncpg connections are bound to the event loop that created them. Startup
    work (e.g. the settings overlay) may run in a temporary loop; disposing the
    engine afterwards ensures the request-handling loop creates fresh
    connections instead of reusing ones bound to a closed loop.
    """
    if _engine is not None:
        await _engine.dispose()


async def dispose_and_reset() -> None:
    """Dispose the shared engine and clear the globals.

    Celery worker tasks run each unit of work inside a fresh ``asyncio.run``,
    so the engine must be disposed (within that same loop) and then cleared so
    the next task creates a brand-new engine instead of reusing connections
    bound to a loop that has already closed.
    """
    await dispose_engine()
    reset_db()


def _default_engine_kwargs(database_url: str, **kwargs) -> dict:
    """Add SQLite-friendly defaults for the async engine.

    SQLite's default ``StaticPool`` keeps a single connection that can
    outlive an ``asyncio`` event loop. Tests that create and dispose
    multiple loops are more reliable with ``NullPool``.
    """
    if (
        kwargs.get("poolclass") is None
        and database_url.startswith("sqlite+aiosqlite")
        and ":memory:" not in database_url
    ):
        kwargs = {"poolclass": NullPool, **kwargs}

    return kwargs


def init_db(database_url: Optional[str] = None, *, engine=None, force: bool = False, **kwargs):
    """Initialize the database engine and session factory.

    Accepts either a database URL or a pre-constructed async engine. The
    ``force`` flag is intended for tests that need to re-initialize the shared
    engine between test cases.
    """
    global _engine, _session_factory
    if not force and _engine is not None:
        return

    if engine is not None:
        _engine = engine
    elif database_url is not None:
        _engine = create_async_engine(database_url, **_default_engine_kwargs(database_url, **kwargs))
    else:
        raise ValueError("Provide either database_url or engine")

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def create_all_tables(
    database_url: Optional[str] = None,
    engine: Optional[AsyncEngine] = None,
) -> None:
    """Create all SQLAlchemy tables if they do not already exist.

    Uses the global engine by default, or a provided engine/database URL.
    When a database URL is provided, the engine is disposed after use.
    """
    if engine is None and database_url is None:
        if _engine is None:
            raise RuntimeError("Database not initialized. Call init_db() first or provide a database URL/engine.")
        engine = _engine
    elif engine is None:
        if database_url is None:
            raise ValueError("Provide either database_url or engine")
        engine = create_async_engine(database_url, **_default_engine_kwargs(database_url))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if database_url is not None:
        await engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    session = _session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
