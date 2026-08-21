"""
Music file importer: backwards-compatible wrapper around the canonical service.

This module is deprecated. New code should call
``songhive.services.import_.import_audio_file`` directly.
"""

from pathlib import Path
from typing import Literal, Optional, cast

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import load_config
from ..config.schema import StorageConfig
from ..models.upload import Upload
from ..services.import_ import import_audio_file
from ..services.storage import StorageService
from ..storage.base import StorageBackend


async def import_file(
    session: AsyncSession,
    file_path: Path,
    library_id: str,
    storage: StorageBackend,
    storage_backend: str,
    owner_id: Optional[str] = None,
) -> Upload:
    """
    Import an audio file into the library.

    This is a thin backwards-compatible shim that delegates to
    ``services.import_.import_audio_file``.
    """
    config = load_config([])
    storage_service = StorageService(
        storage,
        StorageConfig(
            backend=cast(Literal["local", "s3"], storage_backend),
            local_path=config.storage.local_path,
            max_upload_size=config.storage.max_upload_size,
        ),
    )

    with open(file_path, "rb") as f:
        result = await import_audio_file(
            session,
            storage_service=storage_service,
            file=f,
            filename=file_path.name,
            library_id=library_id,
            owner_id=owner_id,
            source="import",
            enrich=False,
        )

    return result.upload
