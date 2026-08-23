"""
Alembic migration helpers.

Migrations are run automatically when the application starts (inside
``create_app``) and can also be triggered manually with ``songhive admin migrate``.

The initial ``base`` revision is intentionally empty: the schema that already
exists in databases deployed before this point is treated as the baseline.  A
fresh database gets the current schema via ``Base.metadata.create_all`` and is
stamped with the current head, so new installs are migration-managed from that
point on.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from ..models import Base

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent


def _to_sync_url(database_url: str) -> str:
    """Return a synchronous SQLAlchemy URL for the given async URL."""
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return database_url


def _sync_engine(database_url: str) -> Engine:
    """Create a synchronous engine for migration operations."""
    return create_engine(_to_sync_url(database_url))


def _get_alembic_config(database_url: str) -> Config:
    """Build an Alembic ``Config`` pointing at the bundled migration scripts."""
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", _to_sync_url(database_url))
    return config


def _database_state(engine: Engine) -> tuple[bool, bool]:
    """Return ``(has_alembic_version, has_other_tables)``."""
    with engine.connect() as conn:
        table_names = inspect(conn).get_table_names()
    has_version = "alembic_version" in table_names
    has_tables = any(t != "alembic_version" for t in table_names)
    return has_version, has_tables


def ensure_migrated(database_url: str, target: str = "head") -> None:
    """
    Ensure the database schema is up to date with the Alembic revision tree.

    - If the database is empty, create the current schema and stamp the target.
    - If the database has tables but no ``alembic_version``, it is a pre-existing
      baseline database: stamp ``base`` and then apply any pending migrations.
    - If ``alembic_version`` exists, run ``alembic upgrade`` to the target.
    """
    engine = _sync_engine(database_url)
    try:
        has_version, has_tables = _database_state(engine)
        config = _get_alembic_config(database_url)

        if not has_version:
            if not has_tables:
                logger.info("Database is empty; creating baseline schema and stamping %s", target)
                Base.metadata.create_all(engine)
                command.stamp(config, target)
            else:
                logger.info("Database has baseline tables; stamping base and upgrading to %s", target)
                command.stamp(config, "base")
                command.upgrade(config, target)
        else:
            logger.info("Applying Alembic migrations up to %s", target)
            command.upgrade(config, target)
    finally:
        engine.dispose()
