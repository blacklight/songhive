"""
Alembic environment configuration.

This file is executed by the ``alembic`` command-line tool and by the
programmatic ``command.upgrade`` / ``command.stamp`` calls used by the
application.  It imports all Songhive models so ``Base.metadata`` is fully
populated and derives the database URL from the running configuration.
"""

import logging
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from songhive.migrations.utils import to_sync_url

# Allow running ``alembic`` from the repository root before the package is
# installed. When called programmatically the package is already importable.
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import songhive.models  # noqa: E402,F401
from songhive.config import load_config  # noqa: E402
from songhive.models.base import Base  # noqa: E402

logger = logging.getLogger("alembic.env")

# this is the Alembic Config object, which provides access to the values within
# the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the database URL to use for this migration run."""
    # If a URL was already injected (e.g. by ``ensure_migrated``) and is not the
    # placeholder from the default ``alembic.ini``, use it directly.
    existing = config.get_main_option("sqlalchemy.url")
    if existing and not existing.startswith("driver://"):
        return to_sync_url(existing)

    # Prefer the explicit environment variable used by the Docker Compose stack.
    env_url = os.environ.get("SONGHIVE_DATABASE__URL")
    if env_url:
        return to_sync_url(env_url)

    # Fall back to the full Songhive configuration.  Provide a dummy auth secret
    # so that loading the config does not fail when only the database URL is
    # required for a migration command.
    if not os.environ.get("SONGHIVE_AUTH__SECRET_KEY"):
        os.environ["SONGHIVE_AUTH__SECRET_KEY"] = "x" * 64

    try:
        cfg = load_config([])
        return to_sync_url(cfg.database.url)
    except Exception as exc:
        logger.warning("Could not load Songhive config for migration URL: %s", exc)

    if existing:
        return to_sync_url(existing)

    raise RuntimeError("No database URL found. Set SONGHIVE_DATABASE__URL or configure database.url in config.toml.")


config.set_main_option("sqlalchemy.url", _database_url())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
