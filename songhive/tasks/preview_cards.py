"""
Preview card tasks: fetch and cache OpenGraph metadata for activity links.
"""

import asyncio
import logging
from typing import Optional

from ..config import load_config
from ..models.activity import Activity
from ..models.base import dispose_and_reset, get_session, init_db
from .celery import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="songhive.tasks.preview_cards.fetch_preview_card",
    max_retries=5,
    default_retry_delay=5,
)
def fetch_preview_card(self, activity_id: str) -> Optional[str]:
    """
    Fetch and link the preview card for ``activity_id``'s first URL.

    The task re-checks the ``preview_cards_enabled`` instance setting and
    the local author's ``preview_cards_enabled`` preference, re-extracts
    the URL from the stored content (mention/hashtag anchors never count),
    reuses a fresh cached card for the URL, and re-fetches stale or
    missing ones. A missing activity row — the enqueue races the posting
    transaction's commit — is retried a few times before being dropped.
    """
    from ..services.preview_cards import process_activity_preview_card

    config = load_config([])
    init_db(config.database.url)

    async def _run() -> str:
        try:
            async with get_session() as session:
                activity = await session.get(Activity, activity_id)
                if activity is None:
                    return "missing"
                await process_activity_preview_card(session, activity)
                await session.commit()
                return "done"
        finally:
            await dispose_and_reset()

    result = asyncio.run(_run())
    if result == "missing":
        if self.request.retries < self.max_retries:
            logger.info("Activity %s not committed yet; retrying card fetch", activity_id)
            raise self.retry(countdown=5 * 2**self.request.retries)
        logger.warning("Dropping preview card fetch for unknown activity %s", activity_id)
        return None
    return result
