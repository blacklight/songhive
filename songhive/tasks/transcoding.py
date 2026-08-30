"""
Transcoding tasks: pre-transcode audio files to common formats.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import load_config
from ..models.base import dispose_and_reset, get_session, init_db
from ..models.upload import Upload
from ..services.storage import StorageService
from ..services.streaming import cache_transcode
from ..storage import get_storage
from ..streaming.transcoder import Transcoder
from .celery import celery_app

logger = logging.getLogger(__name__)


async def _transcode_upload(upload_id: str, target_format: str, bitrate: str) -> str:
    """
    Pre-transcode an upload and cache the result.

    :returns: The ``StoredFile.id`` of the cached transcode.
    """
    config = load_config([])
    init_db(config.database.url)

    storage_backend = get_storage(config.storage)
    storage_service = StorageService(storage_backend, config.storage)

    try:
        async with get_session() as session:
            result = await session.execute(
                select(Upload)
                .where(Upload.id == upload_id)
                .options(selectinload(Upload.track), selectinload(Upload.stored_file))
            )
            upload = result.scalar_one_or_none()
            if upload is None:
                logger.warning("transcode_upload called for missing upload %s", upload_id)
                return ""

            track = upload.track
            if track is None:
                logger.warning("Upload %s has no associated track", upload_id)
                return ""

            stored_file = upload.stored_file
            source_path = None
            if stored_file is not None:
                source_path = await storage_backend.retrieve(stored_file.storage_path)
            elif upload.storage_path:
                source_path = await storage_backend.retrieve(upload.storage_path)

            if source_path is None:
                logger.warning("No source file found for upload %s", upload_id)
                return ""

            transcoder = Transcoder(config.streaming.ffmpeg_path)

            with tempfile.TemporaryDirectory() as tmp_dir:
                transcode_result = await transcoder.transcode(
                    source_path,
                    target_format,
                    output_dir=Path(tmp_dir),
                    bitrate=bitrate,
                )
                output_bytes = transcode_result.output_path.read_bytes()

            cached = await cache_transcode(
                session,
                storage_service,
                track,
                target_format,
                bitrate,
                output_bytes,
                transcode_result.mimetype,
            )

            return cached.id
    finally:
        await dispose_and_reset()


@celery_app.task(name="songhive.tasks.transcoding.transcode_upload")
def transcode_upload(upload_id: str, target_format: str, bitrate: str = "192k") -> str:
    """
    Celery task that transcodes an uploaded audio file and caches the result.

    The async helper is executed via ``asyncio.run`` so it can reuse the same
    streaming services used by the on-the-fly endpoint.
    """
    return asyncio.run(_transcode_upload(upload_id, target_format, bitrate))
