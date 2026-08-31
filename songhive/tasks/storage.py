"""
Storage Celery tasks: garbage collection for orphaned stored files.
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from ..services.admin_tasks import rehash_audio as _rehash_audio
from ..services.storage import StorageService
from ..storage.base import StorageBackend
from .celery import celery_app

logger = logging.getLogger(__name__)


async def _cleanup_orphaned_files(storage: StorageBackend, session: AsyncSession) -> int:
    """
    Delete ``StoredFile`` rows (and their backing files) not referenced by any
    other table.

    A stored file is considered orphaned when no ``Track`` (audio or embedded
    image), ``Album``, ``Artist``, ``Library``, ``Playlist``, ``Upload``,
    or ``TranscodedFile`` row points to it.

    :param storage: A configured storage backend.
    :param session: An active async SQLAlchemy session.
    :returns: The number of orphaned files removed.
    """
    from sqlalchemy import CompoundSelect, delete, select, union

    from ..models.album import Album
    from ..models.artist import Artist
    from ..models.library import Library
    from ..models.playlist import Playlist
    from ..models.stored_file import StoredFile
    from ..models.track import Track
    from ..models.transcoded_file import TranscodedFile
    from ..models.upload import Upload

    referenced_ids: CompoundSelect = union(
        select(Track.audio_file_id).where(Track.audio_file_id.is_not(None)),
        select(Track.image_file_id).where(Track.image_file_id.is_not(None)),
        select(Album.cover_file_id).where(Album.cover_file_id.is_not(None)),
        select(Upload.stored_file_id).where(Upload.stored_file_id.is_not(None)),
        select(Artist.image_file_id).where(Artist.image_file_id.is_not(None)),
        select(Artist.cover_file_id).where(Artist.cover_file_id.is_not(None)),
        select(Library.image_file_id).where(Library.image_file_id.is_not(None)),
        select(Library.cover_file_id).where(Library.cover_file_id.is_not(None)),
        select(Playlist.image_file_id).where(Playlist.image_file_id.is_not(None)),
        select(Playlist.cover_file_id).where(Playlist.cover_file_id.is_not(None)),
        select(TranscodedFile.stored_file_id),
    )

    stmt = (
        delete(StoredFile).where(~StoredFile.id.in_(referenced_ids)).returning(StoredFile.id, StoredFile.storage_path)
    )

    result = await session.execute(stmt)
    rows = result.mappings().all()

    for row in rows:
        logger.info("Deleting orphaned stored file %s at %s", row["id"], row["storage_path"])
        try:
            await storage.delete(row["storage_path"])
        except Exception:
            logger.exception("Failed to delete backing file for %s", row["id"])

    return len(rows)


@celery_app.task(name="songhive.tasks.storage.cleanup_orphaned_files")
def cleanup_orphaned_files() -> int:
    """
    Celery task that removes stored files that are no longer referenced.

    Loads the runtime configuration, initializes the database, and runs the
    async cleanup helper inside ``asyncio.run``.
    """
    from ..config import load_config
    from ..models.base import dispose_and_reset, get_session, init_db
    from ..storage import get_storage

    logger.info("Starting orphaned file cleanup task")

    config = load_config([])
    init_db(config.database.url)
    storage = get_storage(config.storage)

    async def _run() -> int:
        try:
            async with get_session() as session:
                return await _cleanup_orphaned_files(storage, session)
        finally:
            await dispose_and_reset()

    return asyncio.run(_run())


@celery_app.task(name="songhive.tasks.storage.rehash_audio_files")
def rehash_audio_files(dry_run: bool = False) -> dict[str, int]:
    """
    Celery task that migrates audio ``StoredFile`` rows to audio-only hashes.

    Loads the runtime configuration, initializes the database, and runs the
    async rehash helper inside ``asyncio.run``.
    """
    from ..config import load_config
    from ..models.base import dispose_and_reset, get_session, init_db
    from ..storage import get_storage

    logger.info("Starting audio rehash task (dry_run=%s)", dry_run)

    config = load_config([])
    init_db(config.database.url)
    storage = get_storage(config.storage)
    storage_service = StorageService(storage, config.storage)

    async def _run() -> dict[str, int]:
        try:
            async with get_session() as session:
                result = await _rehash_audio(session, storage_service, dry_run=dry_run)
                await session.commit()
                return result
        finally:
            await dispose_and_reset()

    return asyncio.run(_run())
