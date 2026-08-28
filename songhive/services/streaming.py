"""
Streaming service: resolve track backing files, transcode cache, and listen history.
"""

import io
from typing import Optional, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.history import ListeningHistory
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.transcoded_file import TranscodedFile
from ..models.upload import Upload
from ..services.storage import StorageService


async def get_upload_for_track(session: AsyncSession, track_id: str) -> Optional[Upload]:
    """Get the best available upload for a track."""
    result = await session.execute(select(Upload).where(Upload.track_id == track_id).limit(1))
    return cast(Optional[Upload], result.scalar_one_or_none())


async def resolve_track_file(session: AsyncSession, track_id: str) -> Optional[StoredFile]:
    """Return the StoredFile backing a track, falling back to an upload."""
    result = await session.execute(select(Track).where(Track.id == track_id).options(selectinload(Track.audio_file)))
    track = result.scalar_one_or_none()
    if track is None:
        return None

    if track.audio_file_id is not None:
        return track.audio_file

    upload = await get_upload_for_track(session, track_id)
    if upload is not None and upload.stored_file_id is not None:
        return upload.stored_file

    return None


async def get_cached_transcode(
    session: AsyncSession,
    track_id: str,
    format_: str,
    bitrate: str,
) -> Optional[StoredFile]:
    """Return a cached StoredFile for the given track/format/bitrate or None."""
    result = await session.execute(
        select(TranscodedFile)
        .where(
            TranscodedFile.track_id == track_id,
            TranscodedFile.format == format_,
            TranscodedFile.bitrate == bitrate,
        )
        .options(selectinload(TranscodedFile.stored_file))
    )
    transcoded_file = result.scalar_one_or_none()
    if transcoded_file is None:
        return None
    return transcoded_file.stored_file


async def cache_transcode(
    session: AsyncSession,
    storage_service: StorageService,
    track: Track,
    format_: str,
    bitrate: str,
    output_bytes: bytes,
    content_type: str,
) -> StoredFile:
    """
    Store transcoded bytes and create a TranscodedFile cache row.

    The ``StoredFile`` creation and ``TranscodedFile`` insert share a single
    savepoint so a duplicate ``TranscodedFile`` race does not leave an
    unreferenced (orphaned) ``StoredFile`` row behind.
    """
    file_like = io.BytesIO(output_bytes)

    try:
        async with session.begin_nested():
            existing = await get_cached_transcode(session, track.id, format_, bitrate)
            if existing is not None:
                return existing

            stored_file = await storage_service.store_file(
                session,
                file_like,
                content_type,
                prefix="transcoded",
                owner_id=track.owner_id,
                visibility=track.visibility,
            )

            transcoded_file = TranscodedFile(
                track_id=track.id,
                format=format_,
                bitrate=bitrate,
                stored_file_id=stored_file.id,
            )
            session.add(transcoded_file)
            await session.flush()
    except IntegrityError as exc:
        if not StorageService._is_unique_constraint_error(exc):
            raise

        # Another request cached the same transcode concurrently; reuse it.
        existing = await get_cached_transcode(session, track.id, format_, bitrate)
        if existing is not None:
            return existing
        raise

    return stored_file


async def record_listen(session: AsyncSession, user_id: str, track_id: str) -> None:
    """
    Record a listen: insert history and increment the track play count.

    The play count is incremented with an atomic ``UPDATE`` expression so
    concurrent listens do not silently under-count.
    """
    session.add(ListeningHistory(user_id=user_id, track_id=track_id))
    await session.execute(update(Track).where(Track.id == track_id).values(play_count=Track.play_count + 1))

    track = await session.get(Track, track_id)
    if track is not None:
        await session.flush()
        await session.refresh(track)
