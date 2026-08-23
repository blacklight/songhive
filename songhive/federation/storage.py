"""
ActivityPub storage and key management for federation.

Uses pubby's SQLAlchemy-backed storage and keeps it in the same database
as the rest of Songhive by converting the configured async database URL to
a synchronous driver that pubby can use.
"""

from pathlib import Path
from typing import Optional

from pubby.storage.adapters.db import DbActivityPubStorage, init_db_storage
from sqlalchemy import make_url

# Mapping from Songhive's async SQLAlchemy drivers to sync equivalents that
# pubby's SQLAlchemy storage can use.
_ASYNC_TO_SYNC_DRIVERS = {
    "sqlite+aiosqlite": "sqlite",
    "postgresql+asyncpg": "postgresql+psycopg2",
}


def get_sync_database_url(database_url: str) -> str:
    """
    Convert a Songhive async SQLAlchemy URL to a sync driver URL for pubby.

    Supports ``sqlite+aiosqlite -> sqlite`` and
    ``postgresql+asyncpg -> postgresql+psycopg2``.
    If the URL is already sync, it is returned unchanged.

    :param database_url: The async database URL from Songhive config.
    :returns: A sync database URL usable by ``pubby.storage.adapters.db``.
    :raises ValueError: If the driver is not supported for federation storage.
    """
    url = make_url(database_url)

    if url.drivername in _ASYNC_TO_SYNC_DRIVERS:
        url = url.set(drivername=_ASYNC_TO_SYNC_DRIVERS[url.drivername])
    elif url.drivername in (
        "sqlite",
        "postgresql",
        "postgresql+psycopg2",
        "postgresql+psycopg",
    ):
        return url.render_as_string(hide_password=False)
    else:
        raise ValueError(
            f"Unsupported database URL for pubby storage: {database_url}. "
            "Only sqlite+aiosqlite and postgresql+asyncpg are currently supported."
        )

    return url.render_as_string(hide_password=False)


def create_activitypub_storage(database_url: str) -> DbActivityPubStorage:
    """
    Create a pubby SQLAlchemy ActivityPub storage backend.

    Tables are created in the same database used by Songhive, with
    ``federation_`` prefixes to avoid collisions with existing tables.

    :param database_url: The async database URL from Songhive config.
    :returns: A configured ``DbActivityPubStorage`` instance.
    """
    sync_url = get_sync_database_url(database_url)
    return init_db_storage(
        sync_url,
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

    :param private_key_path: Explicit key path from config. If ``None``,
        a default path under ``~/.local/share/songhive/federation/`` is used.
    :returns: Resolved path to the PEM private key.
    """
    from pubby.crypto import export_private_key_pem, generate_rsa_keypair

    key_path = private_key_path if private_key_path is not None else _default_private_key_path()
    key_path = key_path.expanduser().resolve()
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if not key_path.exists() or key_path.stat().st_size == 0:
        private_key, _ = generate_rsa_keypair()
        key_path.write_text(export_private_key_pem(private_key), encoding="utf-8")
        key_path.chmod(0o600)

    return key_path
