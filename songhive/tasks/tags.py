"""
Celery task: sync embedded audio tags from database metadata.
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Optional

import aiofiles
import aiofiles.os

from ..models.album import Album
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..music.metadata import AudioMetadataWrite, write_metadata
from ..services.hashtags import add_hashtags_to_entity, extract_hashtags_from_track
from ..services.metadata import _guess_image_mime
from ..services.storage import StorageService
from ..storage import S3Storage
from .celery import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="songhive.tasks.tags.sync_track_tags")
def sync_track_tags(track_id: str) -> bool:
    """
    Rewrite a track's audio file tags to match the database metadata.

    This is an idempotent, best-effort background task. It returns ``True`` when
    the tags are synced or when there is nothing to do, and ``False`` on a
    failure that should not be retried automatically.
    """
    from ..config import load_config
    from ..models.base import init_db

    config = load_config([])
    init_db(config.database.url)

    try:
        return asyncio.run(_sync_track_tags(track_id, config))
    except Exception as exc:
        logger.exception("sync_track_tags failed for track %s: %s", track_id, exc)
        return False


async def _sync_track_tags(track_id: str, config) -> bool:
    """Acquire a lock and orchestrate the tag-rewrite pipeline."""
    from ..models.base import get_session
    from ..services.redis import close_redis_client, get_redis_client
    from ..services.storage import StorageService as _StorageService
    from ..storage import get_storage

    storage = get_storage(config.storage)
    storage_service = _StorageService(storage, config.storage)
    redis = get_redis_client(config)

    lock_key = _lock_key(track_id)
    if not await _acquire_sync_lock(redis, lock_key):
        logger.info("sync_track_tags for %s is already in progress; skipping", track_id)
        return True

    temp_paths: List[Path] = []
    try:
        async with get_session() as session:
            track = await _load_track_for_sync(session, track_id)
            if track is None:
                return True

            meta = _build_metadata(track)

            auto_tags = extract_hashtags_from_track(track)
            if auto_tags:
                await add_hashtags_to_entity(
                    session,
                    "track",
                    track_id,
                    auto_tags,
                    user_id=track.owner_id,
                )

            cover_path = await _prepare_cover_art(storage_service, track, meta)
            if cover_path is not None:
                temp_paths.append(cover_path)

            audio_path = await _retrieve_audio_file(storage_service, track.audio_file, track_id)
            if audio_path is None:
                return False
            temp_paths.append(audio_path)

            if not await _write_audio_tags(audio_path, meta, track_id):
                return False

            return await _finalize_audio_file(session, track, audio_path, storage_service)

    except Exception:
        logger.exception("sync_track_tags failed for track %s", track_id)
        return False
    finally:
        await _release_sync_lock(redis, lock_key)
        await _remove_temp_files(storage_service, temp_paths)
        await close_redis_client()


def _lock_key(track_id: str) -> str:
    """Return the Redis lock key for a track tag sync."""
    return f"sync_tags:{track_id}"


async def _acquire_sync_lock(redis, lock_key: str) -> bool:
    """Try to acquire a short-lived exclusive lock for the track."""
    return bool(await redis.set(lock_key, "1", nx=True, ex=300))


async def _release_sync_lock(redis, lock_key: str) -> None:
    """Best-effort release of the sync lock."""
    try:
        await redis.delete(lock_key)
    except Exception:
        pass


async def _load_track_for_sync(session, track_id: str) -> Optional[Track]:
    """Load a track with all relationships needed for tag writing."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(Track)
        .options(
            selectinload(Track.artist),
            selectinload(Track.image_file),
            selectinload(Track.audio_file),
            selectinload(Track.album).selectinload(Album.cover_file),
        )
        .where(Track.id == track_id)
    )
    track = result.scalar_one_or_none()
    if track is None:
        logger.warning("Track %s not found for tag sync", track_id)
        return None
    if track.audio_file is None:
        logger.info("Track %s has no audio file; skipping tag sync", track_id)
        return None
    return track


async def _prepare_cover_art(
    storage_service: StorageService,
    track: Track,
    meta: AudioMetadataWrite,
) -> Optional[Path]:
    """
    Resolve, download and attach cover art to the metadata object.

    For S3 backends the temporary file is removed immediately and ``None`` is
    returned.  For local backends the original file path is returned so it can
    be tracked for cleanup only when it is a temp copy.
    """
    cover_file = _resolve_cover_file(track)
    if cover_file is None:
        meta.clear_cover_art = True
        return None

    cover_local_path = await storage_service.backend.retrieve(cover_file.storage_path)
    if cover_local_path is None:
        return None

    async with aiofiles.open(cover_local_path, "rb") as f:
        cover_data = await f.read()

    meta.cover_art = cover_data
    meta.cover_art_mime = cover_file.content_type or _guess_image_mime(cover_data)

    if isinstance(storage_service.backend, S3Storage):
        try:
            await aiofiles.os.remove(cover_local_path)
        except Exception:
            pass
        return None

    return cover_local_path


async def _retrieve_audio_file(
    storage_service: StorageService,
    audio_file: StoredFile,
    track_id: str,
) -> Optional[Path]:
    """Fetch the audio file to a local path and log failures."""
    audio_local_path = await storage_service.backend.retrieve(audio_file.storage_path)
    if audio_local_path is None:
        logger.warning("Could not retrieve audio file for track %s", track_id)
    return audio_local_path


async def _write_audio_tags(
    audio_local_path: Path,
    meta: AudioMetadataWrite,
    track_id: str,
) -> bool:
    """Write the metadata object into the audio file."""
    try:
        await asyncio.to_thread(write_metadata, audio_local_path, meta)
        return True
    except Exception:
        logger.exception("Failed to write metadata for track %s", track_id)
        return False


async def _finalize_audio_file(
    session,
    track: Track,
    audio_local_path: Path,
    storage_service: StorageService,
) -> bool:
    """Persist the new file size and re-upload to S3 if necessary."""
    try:
        stat = await aiofiles.os.stat(audio_local_path)
        track.audio_file.size = stat.st_size

        if isinstance(storage_service.backend, S3Storage):
            with open(audio_local_path, "rb") as f:
                await storage_service.backend.store(
                    f,
                    track.audio_file.storage_path,
                    content_type=track.audio_file.content_type,
                )

        await session.commit()
        return True
    except Exception:
        logger.exception("Failed to finalize audio file for track %s", track.id)
        await session.rollback()
        return False


async def _remove_temp_files(storage_service: StorageService, paths: List[Path]) -> None:
    """Remove temporary download files for S3 backends."""
    if not isinstance(storage_service.backend, S3Storage):
        return

    for path in paths:
        if path is None:
            continue
        try:
            await aiofiles.os.remove(path)
        except Exception:
            pass


def _build_metadata(track: Track) -> AudioMetadataWrite:
    """Build an ``AudioMetadataWrite`` from a Track and its related entities."""
    artist_name = track.artist.name if track.artist is not None else None
    album_title = track.album.title if track.album is not None else None
    year = None
    if track.release_year is not None:
        year = track.release_year
    elif track.album is not None and track.album.release_year is not None:
        year = track.album.release_year

    return AudioMetadataWrite(
        title=track.title,
        artist=artist_name,
        album=album_title,
        track_number=track.track_number,
        disc_number=track.disc_number,
        genre=track.genre,
        year=year,
    )


def _resolve_cover_file(track: Track) -> Optional[StoredFile]:
    """Return the cover art StoredFile for a track, if any."""
    if track.image_file is not None:
        return track.image_file
    if track.album is not None and track.album.cover_file is not None:
        return track.album.cover_file
    return None
