"""
Storage Celery tasks: garbage collection for orphaned stored files.
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..storage.base import StorageBackend
from .celery import celery_app

logger = logging.getLogger(__name__)


async def _cleanup_orphaned_files(storage: StorageBackend, session: AsyncSession) -> int:
    """
    Delete ``StoredFile`` rows (and their backing files) not referenced by any
    ``Track``, ``Album``, or ``Upload``.

    :param storage: A configured storage backend.
    :param session: An active async SQLAlchemy session.
    :returns: The number of orphaned files removed.
    """
    from sqlalchemy import select

    from ..models.album import Album
    from ..models.artist import Artist  # noqa: F401
    from ..models.library import Library  # noqa: F401
    from ..models.stored_file import StoredFile
    from ..models.track import Track
    from ..models.upload import Upload
    from ..models.user import User  # noqa: F401

    referenced_by_track = select(Track.audio_file_id).where(Track.audio_file_id.is_not(None))
    referenced_by_album = select(Album.cover_file_id).where(Album.cover_file_id.is_not(None))
    referenced_by_upload = select(Upload.stored_file_id).where(Upload.stored_file_id.is_not(None))

    stmt = select(StoredFile).where(
        ~StoredFile.id.in_(referenced_by_track),
        ~StoredFile.id.in_(referenced_by_album),
        ~StoredFile.id.in_(referenced_by_upload),
    )

    result = await session.execute(stmt)
    orphans = result.scalars().all()

    _count = 0
    for _count, stored_file in enumerate(orphans):
        logger.info("Deleting orphaned stored file %s at %s", stored_file.id, stored_file.storage_path)
        try:
            await storage.delete(stored_file.storage_path)
        except Exception:
            logger.exception("Failed to delete backing file for %s", stored_file.id)
        await session.delete(stored_file)

    return _count


@celery_app.task(name="songhive.tasks.storage.cleanup_orphaned_files")
def cleanup_orphaned_files() -> int:
    """
    Celery task that removes stored files that are no longer referenced.

    Loads the runtime configuration, initializes the database, and runs the
    async cleanup helper inside ``asyncio.run``.
    """
    from ..config import load_config
    from ..models.base import get_session, init_db
    from ..storage import get_storage

    logger.info("Starting orphaned file cleanup task")

    config = load_config([])
    init_db(config.database.url)
    storage = get_storage(config.storage)

    async def _run() -> int:
        async with get_session() as session:
            return await _cleanup_orphaned_files(storage, session)

    return asyncio.run(_run())
