"""
Synchronous Webmention storage backed by the ``webmentions`` DB adapter.

The bundled adapter manages its own ``webmentions`` table through a dedicated
synchronous SQLAlchemy engine — mirroring how ``federation/storage.py`` hosts
pubby's tables on the same database. All access happens in Celery tasks (or
via ``asyncio.to_thread``), so the sync session lifecycle never touches the
async engine.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.pool import NullPool
from webmentions import Webmention
from webmentions.storage.adapters.db import DbWebmentionsStorage, init_db_storage

from ..migrations.utils import to_sync_url

logger = logging.getLogger(__name__)


class SonghiveWebmentionsStorage(DbWebmentionsStorage):
    """DB storage that tolerates mentions without a ``published`` date.

    The bundled model declares ``published`` NOT NULL while the incoming
    parser may produce mentions without one; normalize it before storing so a
    missing date cannot trip the column constraint.
    """

    def store_webmention(self, mention: Webmention) -> Any:
        if mention.published is None:
            mention.published = mention.created_at or datetime.now(timezone.utc)
        return super().store_webmention(mention)


def create_webmentions_storage(database_url: str) -> SonghiveWebmentionsStorage:
    """Create the Webmention storage against the app's database.

    Initializes the dedicated ``webmentions`` table when missing and returns a
    storage wrapper sharing the engine, model, and session factory created by
    ``init_db_storage``.
    """
    sync_url = to_sync_url(database_url)
    kwargs = {"poolclass": NullPool} if sync_url.startswith("sqlite://") else {}
    storage = init_db_storage(sync_url, table_name="webmentions", **kwargs)
    return SonghiveWebmentionsStorage(
        engine=storage.engine,
        model=storage.model,
        session_factory=storage.session_factory,
    )
