"""
Image enrichment Celery task.

Artist images and album covers are resolved from MusicBrainz URL relations and
the Cover Art Archive after MusicBrainz metadata enrichment has run.
"""

import asyncio
import logging
from typing import Optional

from .celery import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="songhive.tasks.images.enrich_images")
def enrich_images(track_id: str, force: bool = False) -> bool:
    """
    Enqueue image enrichment (artist image + album cover) for a single track.

    :param track_id: The track whose artist and album should be enriched.
    :param force: Re-enrich even if the artist/album has already been processed.
    :returns: ``True`` if an image or cover was updated.
    """
    from ..config import load_config
    from ..models.base import get_session, init_db
    from ..services.musicbrainz import MusicBrainzService
    from ..services.storage import StorageService
    from ..storage import get_storage

    config = load_config([])
    if not config.musicbrainz.enabled or not config.musicbrainz.fetch_artist_images:
        return False

    init_db(config.database.url)
    storage = get_storage(config.storage)
    storage_service = StorageService(storage, config.storage)
    mb_service = MusicBrainzService(config.musicbrainz)

    async def _run() -> bool:
        async with get_session() as session:
            return await mb_service.enrich_images(session, track_id, storage_service, force=force)

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception("Image enrichment failed for track %s: %s", track_id, exc)
        return False


@celery_app.task(name="songhive.tasks.images.bulk_enrich_images")
def bulk_enrich_images(
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    all_: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Bulk image enrichment for artists and albums.

    Enqueues image enrichment for the requested scope of artists and albums.
    """
    from ..config import load_config
    from ..models.base import get_session, init_db
    from ..services.admin_tasks import bulk_enrich_images as _bulk_enrich_images
    from ..services.musicbrainz import MusicBrainzService
    from ..services.storage import StorageService
    from ..storage import get_storage

    config = load_config([])
    if not config.musicbrainz.enabled or not config.musicbrainz.fetch_artist_images:
        return {"artists": 0, "albums": 0, "updated": 0, "failed": 0}

    init_db(config.database.url)
    storage = get_storage(config.storage)
    storage_service = StorageService(storage, config.storage)
    mb_service = MusicBrainzService(config.musicbrainz)

    async def _run() -> dict[str, int]:
        async with get_session() as session:
            result = await _bulk_enrich_images(
                session,
                mb_service,
                storage_service,
                artist_id=artist_id,
                album_id=album_id,
                all_=all_,
                force=force,
                dry_run=dry_run,
            )
            await session.commit()
            return result

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception("Bulk image enrichment failed: %s", exc)
        return {"artists": 0, "albums": 0, "updated": 0, "failed": 0}
