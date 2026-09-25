"""
Download archive Celery tasks: asynchronous ZIP builds and cleanup.

``build_download_archive`` materializes a ``DownloadArchive`` row into a ZIP
``StoredFile`` and notifies the requester. Concurrency is capped
instance-wide through a Redis sorted-set semaphore
(``config.downloads.max_concurrent_archives``); when the cap is reached the
task retries with a short countdown instead of competing for resources.

``cleanup_completed_downloads`` runs on a beat schedule: it removes
ready/failed archives past ``downloads.retention_hours`` and fails
pending/processing rows abandoned by a crashed worker
(``downloads.stale_run_hours``).
"""

import asyncio
import logging
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import select

from ..config.schema import SonghiveConfig
from ..models._enums import Visibility
from ..models.download import DownloadArchive
from ..models.notification import NotificationType
from ..services.storage import StorageService
from .celery import celery_app

logger = logging.getLogger(__name__)

_SLOT_KEY = "songhive:downloads:archive-slots"
# Slot lease: a crashed worker's slot expires even without ZREM.
_SLOT_LEASE_SECONDS = 3600
# Waiting-for-a-slot retries (20s apart ≈ 80 minutes of queueing).
_SLOT_RETRY_COUNTDOWN = 20
_SLOT_MAX_RETRIES = 240

# Atomically expire stale slots and take one below the limit.
_ACQUIRE_SLOT_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
    return 0
end
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return 1
"""


def _acquire_slot(archive_id: str, config: SonghiveConfig) -> Optional[object]:
    """
    Try to take an archive build slot.

    Returns the Redis client to release the slot with, or ``None`` when the
    cap is reached. When Redis is unavailable the limiter fails open —
    matching the API rate-limiter policy — and returns a client to release
    with anyway (a no-op if it stayed down).
    """
    from ..services.redis import get_sync_redis_client

    try:
        client = get_sync_redis_client(config)
        now = time.time()
        acquired = client.eval(
            _ACQUIRE_SLOT_SCRIPT,
            1,
            _SLOT_KEY,
            now - _SLOT_LEASE_SECONDS,
            config.downloads.max_concurrent_archives,
            now,
            archive_id,
            _SLOT_LEASE_SECONDS * 2,
        )
        if not acquired:
            return None
        return client
    except Exception:
        logger.warning("Download slot check unavailable; proceeding without cap")
        return _FailOpenClient()


class _FailOpenClient:
    """Slot handle used when Redis is unavailable; release is a no-op."""

    def zrem(self, *args, **kwargs):
        return 0


def _release_slot(client: Optional[Any], archive_id: str) -> None:
    if client is None:
        return
    try:
        client.zrem(_SLOT_KEY, archive_id)
    except Exception:
        logger.warning("Could not release download slot for %s", archive_id)


def _archive_filename(label: str) -> str:
    from ..services.downloads import sanitize_member_name

    return f"{sanitize_member_name(label)}.zip"


async def _mark_failed(archive_id: str, message: str) -> None:
    """Best-effort transition of an archive to ``failed``."""
    from ..models.base import get_session

    async with get_session() as session:
        archive = await session.get(DownloadArchive, archive_id)
        if archive is not None and archive.status in ("pending", "processing"):
            archive.status = "failed"
            archive.error = message[:500]
            archive.completed_at = datetime.now(timezone.utc)


async def _build_archive(archive_id: str, config: SonghiveConfig, storage_service: StorageService) -> None:
    """Materialize the archive and update its row; never raises."""
    from ..models.base import get_session
    from ..services import downloads as downloads_service
    from ..services.notifications import create_notification

    try:
        async with get_session() as session:
            archive = await session.get(DownloadArchive, archive_id)
            if archive is None or archive.status not in ("pending", "processing"):
                return
            archive.status = "processing"
            items = list(archive.items or [])

        with tempfile.TemporaryDirectory(prefix="songhive-dl-") as tmp:
            zip_path, item_errors = await downloads_service.materialize_archive(
                storage_service,
                config,
                items,
                Path(tmp),
            )
            async with get_session() as session:
                archive = await session.get(DownloadArchive, archive_id)
                if archive is None:
                    return
                with open(zip_path, "rb") as handle:
                    stored = await storage_service.store_file(
                        session,
                        handle,
                        "application/zip",
                        prefix="downloads",
                        owner_id=archive.user_id,
                        visibility=Visibility.PRIVATE.value,
                        original_filename=_archive_filename(archive.label),
                    )
                archive.archive_file_id = stored.id
                archive.status = "ready"
                archive.item_errors = item_errors or None
                archive.completed_at = datetime.now(timezone.utc)
                await create_notification(
                    session,
                    user_id=archive.user_id,
                    type=NotificationType.DOWNLOAD,
                    source_url=f"/downloads#{archive.id}",
                    payload={
                        "archive_id": archive.id,
                        "label": archive.label,
                        "size": stored.size,
                        "item_errors": len(item_errors),
                    },
                )
    except Exception as exc:  # noqa: BLE001 — any build failure fails the archive
        logger.exception("Download archive %s failed", archive_id)
        try:
            await _mark_failed(archive_id, str(exc) or type(exc).__name__)
        except Exception:
            logger.exception("Could not mark download archive %s as failed", archive_id)


def _run_async(coro) -> None:
    """Run an async body and always dispose the engine for the next task."""
    from ..models.base import dispose_and_reset

    async def _wrapped() -> None:
        try:
            await coro
        finally:
            await dispose_and_reset()

    asyncio.run(_wrapped())


def _fail_archive_sync(archive_id: str, message: str) -> None:
    """Mark an archive failed from sync task context."""
    from ..config import load_config
    from ..models.base import init_db

    try:
        init_db(load_config([]).database.url)
        _run_async(_mark_failed(archive_id, message))
    except Exception:
        logger.exception("Could not mark download archive %s as failed", archive_id)


@celery_app.task(
    bind=True,
    name="songhive.tasks.downloads.build_download_archive",
    max_retries=_SLOT_MAX_RETRIES,
)
def build_download_archive(self, archive_id: str) -> None:
    """
    Build the ZIP for a ``DownloadArchive`` row.

    Waits for an instance-wide build slot (bounded retries), materializes all
    items, stores the archive, and notifies the owner.
    """
    from ..config import load_config
    from ..models.base import init_db
    from ..storage import get_storage

    config = load_config([])

    client = _acquire_slot(archive_id, config)
    if client is None:
        try:
            raise self.retry(countdown=_SLOT_RETRY_COUNTDOWN)
        except MaxRetriesExceededError:
            _fail_archive_sync(archive_id, "Archive build timed out waiting for a worker slot")
            return

    try:
        init_db(config.database.url)
        storage_service = StorageService(get_storage(config.storage), config.storage)
        _run_async(_build_archive(archive_id, config, storage_service))
    except Exception:
        logger.exception("Download archive %s crashed before completion", archive_id)
        _fail_archive_sync(archive_id, "Archive build failed unexpectedly")
    finally:
        _release_slot(client, archive_id)


async def _cleanup_archives(session, storage_service: StorageService, config: SonghiveConfig) -> int:
    """Fail stale builds and delete finished archives past retention."""
    from ..services.downloads import delete_archive_payload

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(hours=config.downloads.stale_run_hours)
    retention_cutoff = now - timedelta(hours=config.downloads.retention_hours)

    stale = (
        (
            await session.execute(
                select(DownloadArchive).where(
                    DownloadArchive.status.in_(("pending", "processing")),
                    DownloadArchive.updated_at < stale_cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    for archive in stale:
        logger.warning("Failing abandoned download archive %s", archive.id)
        archive.status = "failed"
        archive.error = "Archive build did not complete"
        archive.completed_at = now

    expired = (
        (
            await session.execute(
                select(DownloadArchive).where(
                    DownloadArchive.status.in_(("ready", "failed")),
                    DownloadArchive.completed_at.is_not(None),
                    DownloadArchive.completed_at < retention_cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    for archive in expired:
        await delete_archive_payload(session, storage_service, archive)
        await session.delete(archive)

    return len(expired)


@celery_app.task(name="songhive.tasks.downloads.cleanup_completed_downloads")
def cleanup_completed_downloads() -> int:
    """
    Periodic task: remove finished archives past their retention window.

    Returns the number of archives removed.
    """
    from ..config import load_config
    from ..models.base import get_session, init_db
    from ..storage import get_storage

    logger.info("Starting completed download archive cleanup")
    config = load_config([])
    init_db(config.database.url)
    storage_service = StorageService(get_storage(config.storage), config.storage)

    async def _run() -> int:
        from ..models.base import dispose_and_reset

        try:
            async with get_session() as session:
                return await _cleanup_archives(session, storage_service, config)
        finally:
            await dispose_and_reset()

    removed = asyncio.run(_run())
    logger.info("Removed %d completed download archives", removed)
    return removed
