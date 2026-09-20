"""
Podcast tasks: periodic feed refreshes for followed podcasts.

``scan_due_podcasts`` runs on a fixed beat cadence and enqueues a
``refresh_podcast`` task per feed whose last fetch is older than
``podcasts.refresh_interval_minutes`` — the same scan-then-enqueue shape as
``external_libraries.scan_scheduled_syncs``.
"""

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from ..config import load_config
from ..models.base import dispose_and_reset, get_session, init_db
from ..models.podcast import Podcast
from ..models.user import User
from .celery import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="songhive.tasks.podcasts.refresh_podcast",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(),
)
def refresh_podcast(self, podcast_id: str) -> Optional[str]:
    """
    Refresh a single podcast feed.

    Records fetch/parse failures on the row's ``last_error`` instead of
    retrying — a broken feed stays broken until the publisher fixes it, and
    hammering it with retries only adds load.
    """
    from ..services import podcasts as podcasts_service
    from ..services.podcasts import FeedFetchError

    config = load_config([])
    init_db(config.database.url)

    async def _run() -> str:
        try:
            async with get_session() as session:
                podcast = await session.get(Podcast, podcast_id)
                if podcast is None:
                    return "missing"
                try:
                    new_count = await podcasts_service.refresh_podcast(session, podcast, config, force=True)
                except FeedFetchError as exc:
                    logger.info("Podcast %s refresh failed: %s", podcast_id, exc)
                    await session.commit()
                    return "error"
                await session.commit()
                logger.info("Podcast %s refreshed: %d new episodes", podcast_id, new_count)
                return "done"
        finally:
            await dispose_and_reset()

    result = asyncio.run(_run())
    if result == "missing":
        if self.request.retries < self.max_retries:
            logger.info("Podcast %s not committed yet; retrying refresh", podcast_id)
            raise self.retry(countdown=5 * 2**self.request.retries)
        logger.warning("Dropping refresh for unknown podcast %s", podcast_id)
        return None
    return result


@celery_app.task(name="songhive.tasks.podcasts.scan_due_podcasts")
def scan_due_podcasts() -> int:
    """
    Enqueue refreshes for followed podcasts whose feeds are due.

    Feeds that were never fetched, or whose ``last_fetched_at`` is older
    than ``podcasts.refresh_interval_minutes``, get a ``refresh_podcast``
    task. Podcasts nobody follows are left alone.
    """
    from ..services import podcasts as podcasts_service

    config = load_config([])
    if not config.podcasts.enabled:
        return 0
    init_db(config.database.url)

    async def _run() -> list[str]:
        try:
            async with get_session() as session:
                return await podcasts_service.due_podcast_ids(
                    session, timedelta(minutes=config.podcasts.refresh_interval_minutes)
                )
        finally:
            await dispose_and_reset()

    podcast_ids = asyncio.run(_run())
    for podcast_id in podcast_ids:
        try:
            refresh_podcast.delay(podcast_id)
        except Exception as exc:
            logger.warning("Cannot enqueue podcast refresh for %s: %s", podcast_id, exc)
    return len(podcast_ids)


@celery_app.task(
    bind=True,
    name="songhive.tasks.podcasts.sync_gpodder_account",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(),
)
def sync_gpodder_account(self, user_id: str) -> Optional[str]:
    """
    Run one GPodder subscription sync for a user.

    Sync failures are recorded on the config row's ``last_error`` instead of
    retried — a broken server stays broken until it is fixed, and hammering
    it with retries only adds load.
    """
    from ..services import gpodder as gpodder_service
    from ..services.gpodder import GPodderError

    config = load_config([])
    init_db(config.database.url)

    async def _run() -> str:
        try:
            async with get_session() as session:
                user = await session.get(User, user_id)
                if user is None:
                    return "missing"
                row = await gpodder_service.get_sync_config(session, user)
                if row is None or not row.enabled:
                    return "disabled"
                try:
                    result = await gpodder_service.run_sync(session, user, row, config)
                except GPodderError as exc:
                    logger.info("GPodder sync for user %s failed: %s", user_id, exc)
                    await session.commit()
                    return "error"
                await session.commit()
                logger.info(
                    "GPodder sync for user %s: +%d/-%d local, pushed +%d/-%d",
                    user_id,
                    result.subscribed,
                    result.unsubscribed,
                    result.pushed_adds,
                    result.pushed_removes,
                )
                return "done"
        finally:
            await dispose_and_reset()

    result = asyncio.run(_run())
    if result == "missing":
        if self.request.retries < self.max_retries:
            logger.info("User %s not committed yet; retrying gpodder sync", user_id)
            raise self.retry(countdown=5 * 2**self.request.retries)
        logger.warning("Dropping gpodder sync for unknown user %s", user_id)
        return None
    return result


@celery_app.task(name="songhive.tasks.podcasts.scan_due_gpodder_syncs")
def scan_due_gpodder_syncs() -> int:
    """
    Enqueue GPodder syncs for accounts due for one.

    Enabled sync configs whose ``last_attempt_at`` is unset or older than
    ``podcasts.gpodder_sync_interval_minutes`` get a ``sync_gpodder_account``
    task.
    """
    from ..services import gpodder as gpodder_service

    config = load_config([])
    if not config.podcasts.enabled:
        return 0
    init_db(config.database.url)

    async def _run() -> list[str]:
        try:
            async with get_session() as session:
                return await gpodder_service.due_sync_user_ids(
                    session, timedelta(minutes=config.podcasts.gpodder_sync_interval_minutes)
                )
        finally:
            await dispose_and_reset()

    user_ids = asyncio.run(_run())
    for user_id in user_ids:
        try:
            sync_gpodder_account.delay(user_id)
        except Exception as exc:
            logger.warning("Cannot enqueue gpodder sync for %s: %s", user_id, exc)
    return len(user_ids)
