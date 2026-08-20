"""
Import tasks: process uploaded audio files in the background.
"""

import asyncio
import logging
from pathlib import Path

from .celery import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="songhive.tasks.import_.process_upload")
def process_upload(upload_id: str, file_path: str, library_id: str) -> str:
    """
    Process an uploaded audio file:
    1. Extract metadata
    2. Create/update library entries
    3. Generate thumbnails if album art is embedded

    The library's owner is propagated to the created track and any newly
    created album.  If the library cannot be found, the track and album are
    created without an owner and fall back to the ownerless (local-equivalent)
    access rule.

    :returns: The ID of the created Upload record.
    """
    from ..config import load_config
    from ..models.base import get_session, init_db
    from ..music.importer import import_file
    from ..storage import get_storage

    logger.info("Processing upload %s for library %s", upload_id, library_id)

    config = load_config([])
    init_db(config.database.url)

    storage = get_storage(config.storage)

    async def _run() -> str:
        async with get_session() as session:
            from ..models.library import Library

            library = await session.get(Library, library_id)
            owner_id = library.owner_id if library is not None else None

            upload = await import_file(
                session,
                Path(file_path),
                library_id,
                storage,
                config.storage.backend,
                owner_id=owner_id,
            )
            return str(upload.id)

    return asyncio.run(_run())


@celery_app.task(name="songhive.tasks.import_.fetch_musicbrainz_metadata")
def fetch_musicbrainz_metadata(track_id: str):
    """
    Fetch additional metadata from MusicBrainz for a track.
    """
    # TODO: implement MusicBrainz lookup
