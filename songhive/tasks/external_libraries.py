"""
Celery tasks for external-library sync, scheduled scanning, and metadata write-back.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from kombu.exceptions import OperationalError as KombuOperationalError
from sqlalchemy import select

from ..config import load_config
from ..external.errors import ExternalLibraryError
from ..external.registry import get_external_adapter
from ..external.sync import _metadata_fingerprint, sync_external_library
from ..external.types import ExternalItemRef, ExternalTrackMetadata
from ..models.base import dispose_and_reset, get_session, init_db
from ..models.external_library import ExternalLibrary
from ..models.external_sync_run import ExternalSyncRun
from ..models.external_track import ExternalTrack
from ..services.redis import close_redis_client, get_redis_client
from ..services.secrets import decrypt_json
from .celery import celery_app

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _sanitize_error(exc: Any) -> str:
    """Return a short, config-free string for an exception or message."""
    if isinstance(exc, str):
        return exc[:512]
    return str(exc)[:512]


def _parse_since(value: Optional[datetime | str]) -> Optional[datetime]:
    """Convert a Celery-safe ISO string (or datetime) into a timezone-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_external_track_metadata(external_track: ExternalTrack) -> ExternalTrackMetadata:
    """Build a provider-facing metadata object from the current Songhive track."""
    track = external_track.track
    if track is None:
        raise ExternalLibraryError("External track is not linked to a Songhive track")

    artist_name = track.artist.name if track.artist is not None else None
    album_title = track.album.title if track.album is not None else None
    album_artist = None
    if track.album is not None and track.album.artist is not None:
        album_artist = track.album.artist.name
    if album_artist is None:
        album_artist = artist_name

    return ExternalTrackMetadata(
        title=track.title,
        artist=artist_name or "",
        album=album_title or "",
        album_artist=album_artist or "",
        track_number=track.track_number,
        disc_number=track.disc_number,
        duration=track.duration,
        release_year=track.release_year,
        genre=track.genre,
        musicbrainz_id=track.musicbrainz_id,
        cover_art=None,
        cover_art_mime=None,
        raw_metadata=track.raw_metadata,
    )


async def _sync_external_library(
    external_library_id: str,
    triggered_by: str,
    triggered_by_user_id: Optional[str],
    include_tombstones: bool,
    sync_run_id: Optional[str] = None,
    since: Optional[datetime | str] = None,
    scope: Optional[str] = None,
) -> dict:
    """Run a sync inside a managed session and return the result."""
    redis = get_redis_client(load_config([]))
    try:
        async with get_session() as session:
            run = await sync_external_library(
                session,
                external_library_id,
                triggered_by=triggered_by,
                triggered_by_user_id=triggered_by_user_id,
                include_tombstones=include_tombstones,
                sync_run_id=sync_run_id,
                since=_parse_since(since),
                scope=scope,
                redis=redis,
            )
            return {"sync_run_id": run.id, "status": run.status}
    except ExternalLibraryError as exc:
        logger.exception(
            "External library sync failed: library=%s triggered_by=%s",
            external_library_id,
            triggered_by,
        )
        return {
            "sync_run_id": None,
            "status": "failed",
            "error": _sanitize_error(exc),
        }
    except Exception:
        logger.exception(
            "Unexpected external library sync failure: library=%s triggered_by=%s",
            external_library_id,
            triggered_by,
        )
        return {
            "sync_run_id": None,
            "status": "failed",
            "error": "Unexpected sync failure",
        }
    finally:
        await close_redis_client()
        await dispose_and_reset()


@celery_app.task(name="songhive.tasks.external_libraries.sync_external_library")
def sync_external_library_task(
    external_library_id: str,
    triggered_by: str = "manual",
    triggered_by_user_id: Optional[str] = None,
    include_tombstones: bool = False,
    sync_run_id: Optional[str] = None,
    since: Optional[str] = None,
    scope: Optional[str] = None,
) -> dict:
    """Celery task entry point for syncing a single external library."""
    config = load_config([])
    init_db(config.database.url)

    try:
        return asyncio.run(
            _sync_external_library(
                external_library_id,
                triggered_by,
                triggered_by_user_id,
                include_tombstones,
                sync_run_id=sync_run_id,
                since=since,
                scope=scope,
            )
        )
    except KombuOperationalError:
        logger.exception("Broker error in sync_external_library_task for %s", external_library_id)
        raise


async def _scan_scheduled_syncs() -> int:
    """Scan for external libraries that are due for a scheduled sync."""
    config = load_config([])
    enqueued = 0

    try:
        async with get_session() as session:
            minimum_interval = config.external_libraries.minimum_sync_interval_seconds
            max_concurrent = config.external_libraries.max_concurrent_syncs

            result = await session.execute(
                select(ExternalLibrary).where(
                    ExternalLibrary.enabled.is_(True),
                    ExternalLibrary.sync_enabled.is_(True),
                    ExternalLibrary.sync_interval_seconds.isnot(None),
                )
            )

            for library in result.scalars().all():
                active_run = await session.execute(
                    select(ExternalSyncRun)
                    .where(
                        ExternalSyncRun.external_library_id == str(library.id),
                        ExternalSyncRun.status.in_(["queued", "running"]),
                    )
                    .limit(1)
                )
                if active_run.scalar_one_or_none() is not None:
                    continue

                last_time = library.last_sync_completed_at or library.last_sync_started_at
                interval = library.sync_interval_seconds or 0
                interval = max(interval, minimum_interval)

                if last_time is None:
                    due = True
                else:
                    due = (_utcnow() - last_time).total_seconds() >= interval

                if not due:
                    continue

                if enqueued >= max_concurrent:
                    break

                try:
                    sync_external_library_task.delay(
                        str(library.id),
                        triggered_by="scheduled",
                    )
                    enqueued += 1
                except KombuOperationalError:
                    logger.exception("Broker error enqueuing scheduled sync for %s", library.id)
                    raise

            return enqueued
    except Exception:
        logger.exception("Scheduled external-library sync scan failed")
        return 0
    finally:
        await close_redis_client()
        await dispose_and_reset()


@celery_app.task(name="songhive.tasks.external_libraries.scan_scheduled_syncs")
def scan_scheduled_syncs_task() -> int:
    """Celery task that scans for due scheduled external-library syncs."""
    config = load_config([])
    init_db(config.database.url)

    try:
        return asyncio.run(_scan_scheduled_syncs())
    except KombuOperationalError:
        logger.exception("Broker error in scan_scheduled_syncs_task")
        raise


async def _write_back_metadata(external_track_id: str) -> bool:
    """Write Songhive metadata back to the external provider."""
    try:
        async with get_session() as session:
            result = await session.execute(select(ExternalTrack).where(ExternalTrack.id == external_track_id))
            external_track = result.scalar_one_or_none()
            if external_track is None:
                logger.warning("External track %s not found for write-back", external_track_id)
                return True

            if not external_track.write_back_pending:
                return True

            # Reload external_library and its library with the track loaded.
            library_result = await session.execute(
                select(ExternalLibrary).where(ExternalLibrary.id == external_track.external_library_id)
            )
            external_library = library_result.scalar_one_or_none()
            if external_library is None:
                raise ExternalLibraryError(
                    f"External library {external_track.external_library_id} not found for write-back"
                )

            capabilities = external_library.capabilities or {}
            if not capabilities.get("write_tags"):
                logger.info(
                    "Adapter %s does not support write_tags; skipping write-back", external_library.provider_type
                )
                return True

            adapter_cls = get_external_adapter(external_library.provider_type)
            adapter = adapter_cls()

            raw_config = external_library.config
            decrypted_config = decrypt_json(raw_config) if isinstance(raw_config, str) else dict(raw_config or {})
            await adapter.validate_config(decrypted_config)

            metadata = _build_external_track_metadata(external_track)

            item = ExternalItemRef(
                provider_key=external_track.provider_key,
                display_path=(
                    external_track.raw_metadata.get("display_path", external_track.provider_key)
                    if isinstance(external_track.raw_metadata, dict)
                    else external_track.provider_key
                ),
                etag=external_track.provider_etag,
                mtime=external_track.provider_mtime,
                size=external_track.provider_size,
                mime_type=external_track.provider_mime_type,
                checksum=external_track.provider_checksum,
                sha256=external_track.sha256,
            )

            try:
                mutation = await adapter.write_metadata(decrypted_config, item, metadata)
            except Exception as exc:
                logger.exception("write_metadata failed for %s", external_track_id)
                external_track.write_back_error = _sanitize_error(exc)
                return False

            external_track.provider_etag = mutation.etag
            external_track.provider_mtime = mutation.mtime
            external_track.provider_checksum = mutation.checksum
            if mutation.sha256:
                external_track.sha256 = mutation.sha256
            external_track.write_back_pending = False
            external_track.write_back_error = None
            external_track.metadata_fingerprint = _metadata_fingerprint(metadata)

            track = external_track.track
            if track is not None:
                track.external_metadata_synced_at = _utcnow()

            return True
    except Exception:
        logger.exception("Unexpected write-back failure for external track %s", external_track_id)
        return False
    finally:
        await close_redis_client()
        await dispose_and_reset()


@celery_app.task(name="songhive.tasks.external_libraries.write_back_metadata")
def write_back_metadata_task(external_track_id: str) -> bool:
    """Celery task entry point for writing Songhive metadata back to a provider."""
    config = load_config([])
    init_db(config.database.url)

    try:
        return asyncio.run(_write_back_metadata(external_track_id))
    except KombuOperationalError:
        logger.exception("Broker error in write_back_metadata_task for %s", external_track_id)
        raise
