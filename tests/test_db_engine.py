"""
Tests for the per-event-loop pooled engine registry in ``models.base``.
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from songhive.models import base as models_base


@pytest.fixture
def _reset_db_state():
    yield
    models_base.reset_db()


def test_default_engine_kwargs_adds_bounded_pool_for_postgres():
    kwargs = models_base._default_engine_kwargs(
        "postgresql+asyncpg://u:p@host/db",
        pool_size=7,
        max_overflow=3,
        pool_timeout=9.0,
        pool_recycle=60,
        pool_pre_ping=True,
    )
    assert kwargs == {
        "pool_size": 7,
        "max_overflow": 3,
        "pool_timeout": 9.0,
        "pool_recycle": 60,
        "pool_pre_ping": True,
    }


def test_default_engine_kwargs_postgres_defaults_apply_when_unset():
    kwargs = models_base._default_engine_kwargs("postgresql+asyncpg://u:p@host/db")
    assert kwargs["pool_size"] == models_base._POOL_DEFAULTS["pool_size"]
    assert kwargs["max_overflow"] == models_base._POOL_DEFAULTS["max_overflow"]
    assert kwargs["pool_timeout"] == models_base._POOL_DEFAULTS["pool_timeout"]


def test_default_engine_kwargs_strips_pool_kwargs_for_sqlite():
    kwargs = models_base._default_engine_kwargs(
        "sqlite+aiosqlite:///tmp/x.db",
        pool_size=7,
        max_overflow=3,
        pool_timeout=9.0,
        pool_recycle=60,
        pool_pre_ping=True,
    )
    assert kwargs == {"poolclass": NullPool}


def test_default_engine_kwargs_explicit_poolclass_wins():
    kwargs = models_base._default_engine_kwargs("postgresql+asyncpg://u:p@host/db", poolclass=NullPool)
    assert kwargs == {"poolclass": NullPool}


@pytest.mark.usefixtures("_reset_db_state")
def test_url_init_creates_bounded_postgres_engine():
    models_base.init_db(
        "postgresql+asyncpg://u:p@host/db",
        pool_size=7,
        max_overflow=3,
        pool_timeout=9.0,
        pool_recycle=60,
        pool_pre_ping=True,
    )
    # No engine is built until a loop asks for one — asyncpg connections
    # are bound to the creating loop.
    assert models_base._loop_engines == {}

    async def _go():
        engine = models_base._engine_for_loop(asyncio.get_running_loop())
        assert isinstance(engine.sync_engine.pool, AsyncAdaptedQueuePool)
        return engine

    engine = asyncio.run(_go())
    assert engine.sync_engine.pool is not None
    asyncio.run(engine.dispose())


@pytest.mark.usefixtures("_reset_db_state")
def test_url_init_creates_engine_per_loop():
    models_base.init_db("sqlite+aiosqlite:///:memory:")

    def _run():
        async def _go():
            return models_base._engine_for_loop(asyncio.get_running_loop())

        return asyncio.run(_go())

    loop_a_engine = _run()
    loop_b_engine = _run()
    assert isinstance(loop_a_engine, AsyncEngine)
    assert loop_a_engine is not loop_b_engine
    # The first loop's entry is pruned once the loop closes.
    assert len(models_base._loop_engines) == 1


@pytest.mark.usefixtures("_reset_db_state")
def test_dispose_engine_empties_pool_but_keeps_registration():
    models_base.init_db("sqlite+aiosqlite:///:memory:")

    async def _go():
        loop = asyncio.get_running_loop()
        engine = models_base._engine_for_loop(loop)
        await models_base.dispose_engine()
        # Registration survives dispose — only pooled connections drop.
        assert models_base._loop_engines.get(loop) is engine
        assert models_base._engine_url == "sqlite+aiosqlite:///:memory:"

    asyncio.run(_go())


@pytest.mark.usefixtures("_reset_db_state")
def test_dispose_and_reset_clears_registration():
    models_base.init_db("sqlite+aiosqlite:///:memory:")

    async def _go():
        models_base._engine_for_loop(asyncio.get_running_loop())
        await models_base.dispose_and_reset()

    asyncio.run(_go())
    assert models_base._engine_url is None
    assert models_base._loop_engines == {}


@pytest.mark.usefixtures("_reset_db_state")
async def test_forced_url_init_clears_explicit_engine_state():
    """``init_db(url, force=True)`` after an explicit engine must switch
    modes: the leftover engine would otherwise keep winning in
    ``_factory_for_current_context`` and silently ignore the new URL."""
    old_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        models_base.init_db(engine=old_engine)
        assert models_base._engine is old_engine

        models_base.init_db("sqlite+aiosqlite:///:memory:", force=True)
        assert models_base._engine is None
        assert models_base._session_factory is None
        assert models_base._engine_url == "sqlite+aiosqlite:///:memory:"
    finally:
        await old_engine.dispose()


@pytest.mark.usefixtures("_reset_db_state")
async def test_forced_engine_init_clears_url_state():
    """The reverse transition: an explicit engine replaces URL mode."""
    models_base.init_db("sqlite+aiosqlite:///:memory:")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        models_base.init_db(engine=engine, force=True)
        assert models_base._engine is engine
        assert models_base._engine_url is None
        assert models_base._engine_kwargs == {}
    finally:
        await engine.dispose()
