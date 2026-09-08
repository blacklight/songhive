"""
ActivityPub storage and key management for federation.

Uses pubby's SQLAlchemy-backed storage and keeps it in the same database
as the rest of Songhive; ``init_db_storage`` converts the configured async
database URL to a synchronous driver via ``pubby.storage.adapters.db``.
"""

from pathlib import Path
from typing import Optional

from pubby.crypto import ensure_private_key_file
from pubby.storage.adapters.db import DbActivityPubStorage, init_db_storage


def create_activitypub_storage(database_url: str) -> DbActivityPubStorage:
    """
    Create a pubby SQLAlchemy ActivityPub storage backend.

    Tables are created in the same database used by Songhive, with
    ``federation_`` prefixes to avoid collisions with existing tables.
    Async SQLAlchemy URLs (e.g. ``sqlite+aiosqlite``,
    ``postgresql+asyncpg``) are converted to their sync equivalents by
    ``init_db_storage``.

    :param database_url: The async database URL from Songhive config.
    :returns: A configured ``DbActivityPubStorage`` instance.
    """
    return init_db_storage(
        database_url,
        followers_table="federation_followers",
        interactions_table="federation_interactions",
        activities_table="federation_activities",
        actor_cache_table="federation_actor_cache",
    )


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
