"""
MusicBrainz enrichment Celery task.
"""

import asyncio
import logging

from .celery import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="songhive.tasks.musicbrainz.enrich_track")
def enrich_track(track_id: str) -> bool:
    """
    Enqueue MusicBrainz enrichment for a single track.

    :returns: ``True`` if metadata was updated.
    """
    from ..config import load_config
    from ..models.base import get_session, init_db
    from ..services.musicbrainz import MusicBrainzService
    from ..services.storage import StorageService
    from ..storage import get_storage

    config = load_config([])
    if not config.musicbrainz.enabled:
        return False

    init_db(config.database.url)
    storage = get_storage(config.storage)
    storage_service = StorageService(storage, config.storage)
    mb_service = MusicBrainzService(config.musicbrainz)

    async def _run() -> bool:
        async with get_session() as session:
            return await mb_service.enrich_track(session, track_id, storage_service)

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception("MusicBrainz enrichment failed for track %s: %s", track_id, exc)
        return False
