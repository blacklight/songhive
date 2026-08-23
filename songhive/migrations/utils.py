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

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from alembic import command, op
from alembic.config import Config

from ..models import Base

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent


def to_sync_url(database_url: str) -> str:
    """Return a synchronous SQLAlchemy URL for the given async URL."""
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return database_url


def _sync_engine(database_url: str) -> Engine:
    """Create a synchronous engine for migration operations."""
    sync_url = to_sync_url(database_url)
    # Use NullPool for SQLite so the sync migration connection does not hold
    # a lock on the database after it is done; this prevents the async app
    # startup from blocking while it waits for the lock.
    kwargs = {"poolclass": NullPool} if sync_url.startswith("sqlite://") else {}
    return create_engine(sync_url, **kwargs)


def _get_alembic_config(database_url: str) -> Config:
    """Build an Alembic ``Config`` pointing at the bundled migration scripts."""
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", to_sync_url(database_url))
    return config


def _database_state(engine: Engine) -> tuple[bool, bool]:
    """Return ``(has_alembic_version, has_other_tables)``."""
    with engine.connect() as conn:
        table_names = inspect(conn).get_table_names()
    has_version = "alembic_version" in table_names
    has_tables = any(t != "alembic_version" for t in table_names)
    return has_version, has_tables


def table_exists(table_name: str) -> bool:
    """
    Return True if the table already exists in the database.

    This is useful for idempotent migrations that can be run multiple times
    without error. For example, migrations can check if a table exists before
    creating it, allowing the migration to be safely re-run.

    Example:
        >>> if not table_exists("my_table"):
        ...     op.create_table("my_table", ...)
    """
    try:
        bind = op.get_bind()
        return sa.inspect(bind).has_table(table_name)
    except Exception:
        # Offline mode or otherwise no real connection; assume not present.
        return False


def column_exists(table_name: str, column_name: str) -> bool:
    """
    Return True if the column exists in the specified table.

    This is useful for idempotent migrations that add columns conditionally.

    Example:
        >>> if not column_exists("users", "email_verified"):
        ...     op.add_column("users", sa.Column("email_verified", sa.Boolean()))
    """
    try:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if not inspector.has_table(table_name):
            return False
        columns = [col["name"] for col in inspector.get_columns(table_name)]
        return column_name in columns
    except Exception:
        # Offline mode or otherwise no real connection; assume not present.
        return False


def index_exists(index_name: str, table_name: str) -> bool:
    """
    Return True if the index exists on the specified table.

    This is useful for idempotent migrations that create indexes conditionally.

    Example:
        >>> if not index_exists("ix_users_email", "users"):
        ...     op.create_index("ix_users_email", "users", ["email"])
    """
    try:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if not inspector.has_table(table_name):
            return False
        indexes = [idx["name"] for idx in inspector.get_indexes(table_name)]
        return index_name in indexes
    except Exception:
        # Offline mode or otherwise no real connection; assume not present.
        return False


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
