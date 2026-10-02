"""
ActivityPub storage and key management for federation.

Uses pubby's SQLAlchemy-backed storage and keeps it in the same database
as the rest of Songhive; ``init_db_storage`` converts the configured async
database URL to a synchronous driver via ``pubby.storage.adapters.db``.
"""

from pathlib import Path
from typing import Optional

from pubby.crypto import ensure_private_key_file
from pubby.storage.adapters.db import (
    DbActivityPubStorage,
    get_db_storage,
    init_db_storage,
    reset_db_storage,
)

from ..config.schema import database_engine_kwargs

# Table names keeping pubby's ap_* tables under a federation_ prefix to
# avoid collisions with Songhive's own models.
_TABLE_NAMES = {
    "followers_table": "federation_followers",
    "follow_requests_table": "federation_follow_requests",
    "interactions_table": "federation_interactions",
    "activities_table": "federation_activities",
    "actor_cache_table": "federation_actor_cache",
}

# ``pool_pre_ping`` applies to every pool class; QueuePool-only sizing
# options are stripped for SQLite URLs (its SingletonThreadPool for
# in-memory databases rejects them).
_POOL_KWARGS = ("pool_size", "max_overflow", "pool_timeout", "pool_recycle")

# Pubby's sync storage only ever runs short CRUD calls, so a small pool
# suffices even in the app process — and keeps each Celery prefork child
# bounded next to its 1+2 async task pool instead of growing a full
# 5+10 pool per child.
_SYNC_POOL_BOUNDS = {"pool_size": 2, "max_overflow": 3}


def create_activitypub_storage(database_url: str, **engine_kwargs) -> DbActivityPubStorage:
    """
    Create a pubby SQLAlchemy ActivityPub storage backend.

    Tables are created in the same database used by Songhive, with
    ``federation_`` prefixes to avoid collisions with existing tables.
    Async SQLAlchemy URLs (e.g. ``sqlite+aiosqlite``,
    ``postgresql+asyncpg``) are converted to their sync equivalents by
    ``init_db_storage``.

    Prefer :func:`get_federation_storage` — each ``init_db_storage`` call
    builds a new engine with its own connection pool and runs
    ``create_all``, so creating storage per request leaks pools until the
    database refuses connections.

    :param database_url: The async database URL from Songhive config.
    :param engine_kwargs: Forwarded to ``sqlalchemy.create_engine``.
    :returns: A configured ``DbActivityPubStorage`` instance.
    """
    return init_db_storage(database_url, **_TABLE_NAMES, **engine_kwargs)


def get_federation_storage(database, **engine_kwargs) -> DbActivityPubStorage:
    """
    Return the shared pubby storage for the configured database.

    Memoized by :func:`pubby.storage.adapters.db.get_db_storage` on the
    URL, table names and engine kwargs — the same storage (and therefore
    the same bounded connection pool) is reused for the lifetime of the
    process instead of leaking one pool per call.

    :param database: The ``database`` config section — its ``pool_*``
        settings feed the storage engine — or a bare database URL string
        (the ``DatabaseConfig`` defaults apply, keeping one memo key).
    :param engine_kwargs: Extra ``sqlalchemy.create_engine`` kwargs,
        overriding the derived ``pool_*`` values.
    :returns: The memoized ``DbActivityPubStorage`` instance.
    """
    if isinstance(database, str):
        url = database
    else:
        url = database.url
    # ``database_engine_kwargs`` also tolerates a str/duck-typed config —
    # missing attributes fall back to the defaults, so URL-string callers
    # (tests) hit the same memoized storage as config-object callers.
    engine_kwargs = {
        **database_engine_kwargs(database),
        **_SYNC_POOL_BOUNDS,
        **engine_kwargs,
    }
    if url.startswith("sqlite"):
        engine_kwargs = {k: v for k, v in engine_kwargs.items() if k not in _POOL_KWARGS}
    return get_db_storage(url, **_TABLE_NAMES, **engine_kwargs)


def reset_federation_storage() -> None:
    """Drop every memoized storage instance (tests)."""
    reset_db_storage()


def _default_private_key_path() -> Path:
    """Default XDG-style path for the auto-generated actor private key."""
    return Path.home() / ".local" / "share" / "songhive" / "federation" / "actor.pem"


def get_or_create_private_key(private_key_path: Optional[Path] = None) -> Path:
    """
    Return the path to a private key, generating one if it does not exist.

    Delegates to ``pubby.crypto.ensure_private_key_file``: a missing or
    empty file gets a fresh RSA-2048 keypair written with ``0o600``
    permissions, and parent directories are created as needed.

    :param private_key_path: Explicit key path from config. If ``None``,
        a default path under ``~/.local/share/songhive/federation/`` is used.
    :returns: Resolved path to the PEM private key.
    """
    return ensure_private_key_file(private_key_path if private_key_path is not None else _default_private_key_path())
