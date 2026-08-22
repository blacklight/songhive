"""
SQLAlchemy base configuration and session management.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from sqlalchemy import DateTime, func
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    id: Mapped[str] = mapped_column(primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
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
