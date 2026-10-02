"""
SQLAlchemy base configuration and session management.
"""

import asyncio
import logging
import threading
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

    # Fetch server-generated values (``server_default``/``onupdate``) eagerly
    # during flush so attributes like ``updated_at`` stay populated after
    # commit instead of expiring into lazy-load traps outside a greenlet.
    __mapper_args__ = {"eager_defaults": True}

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

# URL-based initialization state. ``init_db`` stores the URL and engine kwargs
# instead of building a global engine: asyncpg connections are bound to the
# event loop that created them, and Songhive runs multiple loops in one
# process (Tornado's main loop, the a2wsgi ASGI loop, per-task ``asyncio.run``
# loops in Celery workers, and temporary startup loops). A pooled connection
# checked out on the wrong loop raises ``Future attached to a different
# loop``, so engines are created lazily *per running loop* — each with its own
# bounded QueuePool, which also caps how many Postgres connections a single
# process can hold open (NullPool previously let a request stampede exhaust
# ``max_connections``).
_engine_url: Optional[str] = None
_engine_kwargs: dict = {}
_loop_engines: dict[asyncio.AbstractEventLoop, AsyncEngine] = {}
_loop_factories: dict[asyncio.AbstractEventLoop, "async_sessionmaker[AsyncSession]"] = {}
_registry_lock = threading.Lock()

# Engine kwargs that only make sense for pooled drivers. NullPool (SQLite)
# rejects them, so ``_default_engine_kwargs`` strips them there.
_POOL_KWARGS = frozenset({"pool_size", "max_overflow", "pool_timeout", "pool_recycle", "pool_pre_ping"})

# Defaults applied to ``postgresql+asyncpg://`` engines; callers may override
# any of them by passing the same keyword to ``init_db`` (call sites forward
# ``database_engine_kwargs(config.database)``).
_POOL_DEFAULTS = {
    "pool_size": 5,
    "max_overflow": 10,
    "pool_timeout": 5.0,
    "pool_recycle": 1800,
    "pool_pre_ping": True,
}


def reset_db() -> None:
    """
    Clear the shared engine and session factory without disposing them.

    Tests should call this after disposing an engine they installed, so
    subsequent tests do not inherit a stale global.
    """
    global _engine, _session_factory, _engine_url, _engine_kwargs
    _engine = None
    _session_factory = None
    _engine_url = None
    _engine_kwargs = {}
    with _registry_lock:
        _loop_engines.clear()
        _loop_factories.clear()


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Return the running event loop, or ``None`` in synchronous context."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _prune_loop_engines() -> None:
    """Drop registry entries bound to closed loops; callers must hold the lock.

    Connections inside a dead loop's pool can never be awaited again, so the
    engine is dead weight; its sockets are released by the garbage collector.
    """
    for loop in [key for key in _loop_engines if key.is_closed()]:
        del _loop_engines[loop]
    for loop in [key for key in _loop_factories if key.is_closed()]:
        del _loop_factories[loop]


def _engine_for_loop(loop: asyncio.AbstractEventLoop) -> AsyncEngine:
    """Return the URL-initialized engine bound to ``loop``, creating it lazily."""
    engine = _loop_engines.get(loop)
    if engine is None:
        with _registry_lock:
            _prune_loop_engines()
            engine = _loop_engines.get(loop)
            if engine is None:
                if _engine_url is None:
                    raise RuntimeError("Database not initialized. Call init_db() first.")
                engine = create_async_engine(_engine_url, **_default_engine_kwargs(_engine_url, **_engine_kwargs))
                _loop_engines[loop] = engine
    return engine


def _factory_for_current_context() -> "async_sessionmaker[AsyncSession]":
    """Return the session factory for the running event loop."""
    if _engine is not None:
        assert _session_factory  # for mypy
        return _session_factory

    loop = _running_loop()
    if loop is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    factory = _loop_factories.get(loop)
    if factory is None:
        engine = _engine_for_loop(loop)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        _loop_factories[loop] = factory
    return factory


async def dispose_engine() -> None:
    """Dispose the engines bound to the current event loop.

    asyncpg connections are bound to the event loop that created them. Startup
    work (e.g. the settings overlay) may run in a temporary loop; disposing the
    loop's engine afterwards ensures the request-handling loop creates fresh
    connections instead of reusing ones bound to a closed loop. Engines stay
    registered — ``dispose`` only empties their pools, so a persistent loop
    simply reconnects on the next checkout.
    """
    loop = _running_loop()
    targets = []
    if loop is not None:
        with _registry_lock:
            engine = _loop_engines.get(loop)
            if engine is not None:
                targets.append(engine)
    if _engine is not None:
        targets.append(_engine)
    for target in targets:
        try:
            await target.dispose()
        except Exception:
            logger.exception("Failed to dispose database engine")


async def dispose_and_reset() -> None:
    """Dispose the current loop's engine and clear the globals.

    Celery worker tasks run each unit of work inside a fresh ``asyncio.run``,
    so the engine must be disposed (within that same loop) and then cleared so
    the next task creates a brand-new engine instead of reusing connections
    bound to a loop that has already closed.
    """
    await dispose_engine()
    reset_db()


def _default_engine_kwargs(database_url: str, **kwargs) -> dict:
    """Add loop-safe, bounded defaults for the async engine.

    ``postgresql+asyncpg://`` engines get a bounded ``AsyncAdaptedQueuePool``
    (the async default) sized by the ``database.pool_*`` settings — each event
    loop keeps its own pool, so a request stampede can at most hold
    ``pool_size + max_overflow`` connections per loop instead of one fresh
    connection per in-flight request. Waiters queue for ``pool_timeout``
    seconds and then fail fast instead of exhausting Postgres
    ``max_connections``.

    ``sqlite+aiosqlite://`` keeps ``NullPool``: SQLite connections are cheap,
    and its default ``StaticPool`` can outlive an ``asyncio`` event loop.
    Pool-specific kwargs are stripped for NullPool, which rejects them.
    """
    if database_url.startswith("postgresql+asyncpg://"):
        if kwargs.get("poolclass") is None:
            merged = {**_POOL_DEFAULTS, **kwargs}
            kwargs = merged
    elif database_url.startswith("sqlite+aiosqlite://"):
        kwargs = {k: v for k, v in kwargs.items() if k not in _POOL_KWARGS}
        if kwargs.get("poolclass") is None:
            kwargs = {"poolclass": NullPool, **kwargs}

    return kwargs


def init_db(database_url: Optional[str] = None, *, engine=None, force: bool = False, **kwargs):
    """Initialize the database engine and session factory.

    Accepts either a database URL or a pre-constructed async engine. A URL is
    stored and engines are created lazily per event loop (see module-level
    notes); an explicit ``engine`` is shared by all loops — the ``force`` flag
    is intended for tests that need to re-initialize the shared engine between
    test cases. Extra ``**kwargs`` (e.g. ``database_engine_kwargs(config.database)``)
    are forwarded to ``create_async_engine``.
    """
    global _engine, _session_factory, _engine_url, _engine_kwargs
    if not force and (_engine is not None or _engine_url is not None):
        return

    with _registry_lock:
        _loop_engines.clear()
        _loop_factories.clear()

    if engine is not None:
        _engine = engine
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
        _engine_url = None
        _engine_kwargs = {}
    elif database_url is not None:
        # The two modes are mutually exclusive: an explicit engine left over
        # from a previous init would keep winning in
        # ``_factory_for_current_context`` and silently ignore the new URL.
        _engine = None
        _session_factory = None
        _engine_url = database_url
        _engine_kwargs = kwargs
    else:
        raise ValueError("Provide either database_url or engine")


async def create_all_tables(
    database_url: Optional[str] = None,
    engine: Optional[AsyncEngine] = None,
) -> None:
    """Create all SQLAlchemy tables if they do not already exist.

    Uses the engine bound to the current loop by default, or a provided
    engine/database URL. When a database URL is provided, the engine is
    disposed after use.
    """
    if engine is None and database_url is None:
        loop = _running_loop()
        if loop is None:
            raise RuntimeError("Database not initialized. Call init_db() first or provide a database URL/engine.")
        engine = _engine if _engine is not None else _engine_for_loop(loop)
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
    """Get an async database session bound to the current event loop."""
    session = _factory_for_current_context()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
