"""
SQLAlchemy base configuration and session management.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from sqlalchemy import DateTime, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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


_engine = None
_session_factory = None


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
        _engine = create_async_engine(database_url, **kwargs)
    else:
        raise ValueError("Provide either database_url or engine")

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
