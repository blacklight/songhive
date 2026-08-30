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

import hashlib
import logging
import os
from pathlib import Path
from types import TracebackType
from typing import Any, Optional

import sqlalchemy as sa
from alembic import command, op
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

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


# 64-bit key derived from a stable string so all Songhive processes agree on the
# same advisory lock.  The key is kept positive to fit PostgreSQL's ``bigint``.
_MIGRATION_LOCK_KEY = int(hashlib.sha256(b"songhive:migrations:baseline").hexdigest()[:16], 16) & 0x7FFFFFFFFFFFFFFF

# POSIX ``fcntl`` lock operation values, used when the ``fcntl`` module is
# available.  These are the standard Linux/BSD constants.
_FCNTL_LOCK_EX = 0x02
_FCNTL_LOCK_UN = 0x08


def _has_fcntl() -> bool:
    """Return True when the ``fcntl`` module is available."""
    try:
        import fcntl  # noqa: F401

        return True
    except ImportError:
        return False


def _open_sqlite_lock_file(sync_url: str) -> Optional[Any]:
    """
    Return an open file object for a lock file next to the SQLite database.

    Returns ``None`` for in-memory databases, missing ``fcntl``, or URLs that
    cannot be resolved to a filesystem path.
    """
    if not _has_fcntl():
        return None

    try:
        parsed = sa.make_url(sync_url)
    except Exception:
        return None

    db_path = parsed.database
    if not db_path or ":memory:" in db_path:
        return None

    db_path = os.path.abspath(db_path)
    lock_path = f"{db_path}.songhive-migration.lock"
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        return os.fdopen(fd, "r+")
    except Exception:
        logger.warning("Unable to open SQLite migration lock file %s", lock_path)
        return None


class _MigrationLock:
    """
    Serialize ``ensure_migrated`` so only one process can create or stamp the
    baseline schema at a time.

    PostgreSQL backends use a 64-bit advisory lock.  SQLite uses a ``fcntl``
    file lock on a companion file next to the database.  Other backends fall
    back to no lock (migrations are not expected to run concurrently there).
    """

    def __init__(self, engine: Engine, database_url: str) -> None:
        self.engine = engine
        self.database_url = database_url
        self._lock_conn: Optional[Any] = None
        self._lock_fd: Optional[Any] = None

    def __enter__(self) -> None:
        sync_url = to_sync_url(self.database_url)
        if sync_url.startswith("postgresql"):
            self._lock_conn = self.engine.connect()
            self._lock_conn.execute(
                sa.text("SELECT pg_advisory_lock(:key)"),
                {"key": _MIGRATION_LOCK_KEY},
            )
            self._lock_conn.commit()
            return

        if sync_url.startswith("sqlite"):
            self._lock_fd = _open_sqlite_lock_file(sync_url)
            if self._lock_fd is not None:
                import fcntl

                fcntl.flock(self._lock_fd.fileno(), _FCNTL_LOCK_EX)

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        if self._lock_conn is not None:
            try:
                self._lock_conn.execute(
                    sa.text("SELECT pg_advisory_unlock(:key)"),
                    {"key": _MIGRATION_LOCK_KEY},
                )
                self._lock_conn.commit()
            except Exception:
                logger.exception("Failed to release PostgreSQL migration advisory lock")
            finally:
                self._lock_conn.close()
                self._lock_conn = None

        if self._lock_fd is not None:
            try:
                import fcntl

                fcntl.flock(self._lock_fd.fileno(), _FCNTL_LOCK_UN)
                self._lock_fd.close()
            except Exception:
                pass
            finally:
                self._lock_fd = None


def ensure_migrated(database_url: str, target: str = "head") -> None:
    """
    Ensure the database schema is up to date with the Alembic revision tree.

    - If the database is empty, create the current schema and stamp the target.
    - If the database has tables but no ``alembic_version``, it is a pre-existing
      baseline database: stamp ``base`` and then apply any pending migrations.
    - If ``alembic_version`` exists, run ``alembic upgrade`` to the target.

    A backend-specific lock is acquired for the whole operation so concurrent
    calls (e.g. the app and worker containers starting at the same time) do not
    race to create the baseline schema.
    """
    engine = _sync_engine(database_url)
    try:
        with _MigrationLock(engine, database_url):
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
