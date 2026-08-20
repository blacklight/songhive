"""
Storage service: content-addressable media file management.
"""

import hashlib
import tempfile
from pathlib import Path
from typing import BinaryIO, Optional

import aiofiles
import aiofiles.os
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import StorageConfig
from ..models._enums import Visibility
from ..models.stored_file import StoredFile
from ..storage.base import StorageBackend


class StorageService:
    """
    Service layer for storing, retrieving, and deleting media files.

    The service computes a SHA-256 hash of the uploaded content, builds a
    content-addressable storage path, and avoids storing duplicate content.
    """  # noqa: E501

    CHUNK_SIZE = 64 * 1024

    def __init__(self, backend: StorageBackend, config: StorageConfig):
        self.backend = backend
        self.config = config

    async def _write_and_hash(self, file: BinaryIO, dest_path: Path) -> tuple[str, int]:
        """
        Stream ``file`` to ``dest_path`` in chunks while computing SHA-256.

        Returns the hex digest and total number of bytes written.
        """
        hasher = hashlib.sha256()
        total = 0

        async with aiofiles.open(dest_path, "wb") as dest:
            while chunk := file.read(self.CHUNK_SIZE):
                total += len(chunk)
                hasher.update(chunk)
                await dest.write(chunk)

        return hasher.hexdigest(), total

    async def store_file(
        self,
        session: AsyncSession,
        file: BinaryIO,
        content_type: str,
        original_filename: Optional[str] = None,
        prefix: str = "files",
        owner_id: Optional[str] = None,
        visibility: str = Visibility.PRIVATE.value,
    ) -> StoredFile:
        """
        Store a file in a content-addressable layout and return a ``StoredFile``.

        The returned instance is *uncommitted*; the caller is responsible for
        adding it to the session and flushing/committing.

        ``owner_id`` and ``visibility`` only apply to newly created rows.  If
        the content already exists, the existing ``StoredFile`` is returned
        without modification.
        """
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        tmp_path = Path(tmp.name)

        try:
            hash_hex, size = await self._write_and_hash(file, tmp_path)
            path = f"{prefix}/{hash_hex[:2]}/{hash_hex[2:4]}/{hash_hex}"

            existing = await session.scalar(select(StoredFile).where(StoredFile.sha256 == hash_hex))
            if existing:
                return existing

            if not await self.backend.exists(path):
                with open(tmp_path, "rb") as f:
                    await self.backend.store(f, path, content_type=content_type)

            return StoredFile(
                storage_path=path,
                storage_backend=self.config.backend,
                content_type=content_type,
                size=size,
                sha256=hash_hex,
                original_filename=original_filename,
                owner_id=owner_id,
                visibility=visibility,
            )
        finally:
            if await aiofiles.os.path.exists(tmp_path):
                await aiofiles.os.remove(tmp_path)

    async def get_url(self, stored_file: StoredFile) -> str:
        """Return the public URL for a stored file."""
        return await self.backend.url(stored_file.storage_path, cdn_prefix=self.config.cdn_prefix)

    async def delete_file(self, stored_file: StoredFile) -> bool:
        """Delete a stored file's backing object."""
        return await self.backend.delete(stored_file.storage_path)
