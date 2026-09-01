"""
Import tasks: process uploaded audio files in the background.
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..models._enums import Visibility
from ..models.track import Track
from ..models.user import User
from ..services.federation import publish_track_activity
from .celery import celery_app

logger = logging.getLogger(__name__)

_AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav"}


@celery_app.task(name="songhive.tasks.import_.process_upload")
def process_upload(
    library_id: str,
    owner_id: Optional[str] = None,
    *,
    stored_file_id: Optional[str] = None,
    file_path: Optional[str] = None,
    filename: Optional[str] = None,
    visibility: str = "private",
    force: bool = False,
    enrich: bool = True,
    source: str = "upload",
    content_type: Optional[str] = None,
) -> str:
    """
    Process a stored audio file or a filesystem path and import it into a library.

    Exactly one of ``stored_file_id`` or ``file_path`` must be provided.

    :returns: The ID of the created Upload record, or the existing track ID on
        duplicate detection (when ``force`` is ``False``).
    """
    if (stored_file_id is None) == (file_path is None):
        raise ValueError("Provide exactly one of stored_file_id or file_path")

    from ..config import load_config
    from ..models.base import dispose_and_reset, get_session, init_db
    from ..models.stored_file import StoredFile
    from ..services.import_ import DuplicateTrackError, import_audio_file
    from ..services.storage import StorageService
    from ..storage import get_storage
    from ..ws.events import EventWebSocket

    config = load_config([])
    init_db(config.database.url)

    storage = get_storage(config.storage)
    storage_service = StorageService(storage, config.storage)

    async def _run() -> str:
        try:
            async with get_session() as session:
                file: Optional[BinaryIO] = None
                actual_filename = filename or "audio.mp3"

                if stored_file_id:
                    stored_file = await session.get(StoredFile, stored_file_id)
                    if stored_file is None:
                        raise ValueError(f"StoredFile {stored_file_id} not found")
                    actual_filename = filename or stored_file.original_filename or "audio.mp3"
                    local_path = await storage_service.backend.retrieve(stored_file.storage_path)
                    if local_path is None:
                        raise ValueError(f"Could not retrieve stored file: {stored_file.storage_path}")
                    file = open(local_path, "rb")
                else:
                    assert file_path is not None
                    path = Path(file_path)
                    actual_filename = filename or path.name
                    file = open(path, "rb")

                try:
                    result = await import_audio_file(
                        session,
                        storage_service=storage_service,
                        file=file,
                        filename=actual_filename,
                        library_id=library_id,
                        owner_id=owner_id,
                        visibility=visibility,
                        force=force,
                        enrich=enrich,
                        source=source,
                        content_type=content_type,
                    )
                finally:
                    if file is not None:
                        file.close()

                if owner_id and result.track.visibility == Visibility.PUBLIC.value:
                    owner = await session.get(User, owner_id)
                    loaded_track = await session.execute(
                        select(Track).options(selectinload(Track.artist)).where(Track.id == str(result.track.id))
                    )
                    track = loaded_track.scalar_one()
                    track.federation_object_id = str(uuid.uuid4())
                    await session.commit()
                    artist = track.artist
                    if owner is not None and artist is not None:
                        await asyncio.to_thread(
                            publish_track_activity,
                            track,
                            artist,
                            owner,
                            config,
                            track.federation_object_id,
                        )

                assert result.upload is not None
                EventWebSocket.broadcast(
                    "import.completed",
                    {
                        "library_id": library_id,
                        "track_id": str(result.track.id),
                        "upload_id": str(result.upload.id),
                    },
                    topic="import",
                )
                return str(result.upload.id)
        finally:
            await dispose_and_reset()

    try:
        return asyncio.run(_run())
    except DuplicateTrackError as exc:
        logger.info("Duplicate upload for library %s: %s", library_id, exc.existing_track_id)
        EventWebSocket.broadcast(
            "import.duplicate",
            {
                "library_id": library_id,
                "existing_track_id": exc.existing_track_id,
            },
            topic="import",
        )
        return exc.existing_track_id


@celery_app.task(name="songhive.tasks.import_.scan_directory")
def scan_directory(
    path: str,
    library_id: str,
    owner_id: Optional[str] = None,
) -> int:
    """
    Recursively scan ``path`` for audio files and enqueue an import per file.

    :returns: The number of files enqueued.
    """
    from ..config import load_config

    config = load_config([])

    resolved = Path(path).expanduser().resolve()
    allowed_roots = [Path(r).expanduser().resolve() for r in config.imports.scan_roots]
    if not allowed_roots:
        raise ValueError("directory scanning is not configured")
    if not any(resolved.is_relative_to(root) for root in allowed_roots):
        raise ValueError(f"Path {resolved} is outside configured scan roots")

    count = 0
    for file_path in resolved.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in _AUDIO_EXTENSIONS:
            process_upload.delay(  # type: ignore
                library_id,
                owner_id,
                file_path=str(file_path),
                source="import",
            )
            count += 1

    return count
