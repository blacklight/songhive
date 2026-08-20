"""
Storage service: content-addressable media file management.
"""

import asyncio
import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import BinaryIO, Literal, Optional, Union, overload

import aiofiles
import aiofiles.os
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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

    @staticmethod
    def _is_unique_constraint_error(exc: IntegrityError) -> bool:
        """Return True when an IntegrityError is a uniqueness constraint violation."""
        cause = getattr(exc, "orig", None)
        message = str(cause) if cause is not None else str(exc)
        return "unique" in message.lower()

    def __init__(self, backend: StorageBackend, config: StorageConfig):
        self.backend = backend
        self.config = config

    def _rewind(self, file: BinaryIO) -> None:
        """Seek the stream back to the start when possible."""
        try:
            file.seek(0)
        except (OSError, AttributeError):
            pass

    async def _write_and_hash(self, file: BinaryIO, dest_path: Path) -> tuple[str, int]:
        """
        Stream ``file`` to ``dest_path`` in chunks while computing SHA-256.

        The source read is offloaded to a worker thread so the event loop is
        not blocked by synchronous I/O. Returns the hex digest and total number
        of bytes written.
        """
        hasher = hashlib.sha256()
        total = 0

        async with aiofiles.open(dest_path, "wb") as dest:
            while True:
                chunk = await asyncio.to_thread(file.read, self.CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                hasher.update(chunk)
                await dest.write(chunk)

        return hasher.hexdigest(), total

    @overload
    async def store_file(
        self,
        session: AsyncSession,
        file: BinaryIO,
        content_type: str,
        *,
        original_filename: Optional[str] = None,
        prefix: str = "files",
        owner_id: Optional[str] = None,
        visibility: str = Visibility.PRIVATE.value,
        return_duplicate: Literal[False] = False,
    ) -> StoredFile: ...

    @overload
    async def store_file(
        self,
        session: AsyncSession,
        file: BinaryIO,
        content_type: str,
        *,
        original_filename: Optional[str] = None,
        prefix: str = "files",
        owner_id: Optional[str] = None,
        visibility: str = Visibility.PRIVATE.value,
        return_duplicate: Literal[True] = True,
    ) -> tuple[StoredFile, bool]: ...

    async def store_file(
        self,
        session: AsyncSession,
        file: BinaryIO,
        content_type: str,
        *,
        original_filename: Optional[str] = None,
        prefix: str = "files",
        owner_id: Optional[str] = None,
        visibility: str = Visibility.PRIVATE.value,
        return_duplicate: bool = False,
    ) -> Union[StoredFile, tuple[StoredFile, bool]]:
        """
        Store a file in a content-addressable layout and return a ``StoredFile``.

        The returned instance is added to the session and flushed.  Callers are
        still responsible for committing.  ``owner_id`` and ``visibility`` only
        apply to newly created rows.  If the content already exists, the
        existing ``StoredFile`` is returned without modification.

        Set ``return_duplicate=True`` to also receive a boolean that is ``True``
        when the existing row was returned.
        """
        self._rewind(file)

        fd, tmp_name = tempfile.mkstemp()
        os.close(fd)
        os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
        tmp_path = Path(tmp_name)

        try:
            hash_hex, size = await self._write_and_hash(file, tmp_path)
            path = f"{prefix}/{hash_hex[:2]}/{hash_hex[2:4]}/{hash_hex}"

            existing = await session.scalar(select(StoredFile).where(StoredFile.sha256 == hash_hex))
            if existing is not None:
                if return_duplicate:
                    return existing, True
                return existing

            if not await self.backend.exists(path):
                with open(tmp_path, "rb") as f:
                    await self.backend.store(f, path, content_type=content_type)

            stored_file = StoredFile(
                storage_path=path,
                storage_backend=self.config.backend,
                content_type=content_type,
                size=size,
                sha256=hash_hex,
                original_filename=original_filename,
                owner_id=owner_id,
                visibility=visibility,
            )

            try:
                async with session.begin_nested():
                    session.add(stored_file)
                    await session.flush()
            except IntegrityError as exc:
                if not self._is_unique_constraint_error(exc):
                    raise
                existing = await session.scalar(select(StoredFile).where(StoredFile.sha256 == hash_hex))
                if existing is not None:
                    if return_duplicate:
                        return existing, True
                    return existing
                raise

            if return_duplicate:
                return stored_file, False
            return stored_file
        finally:
            if await aiofiles.os.path.exists(tmp_path):
                await aiofiles.os.remove(tmp_path)

    async def get_url(self, stored_file: StoredFile) -> str:
        """Return a stable API-relative download URL for a stored file."""
        return f"/api/v1/files/{stored_file.id}/download"

    async def delete_file(self, stored_file: StoredFile) -> bool:
        """Delete a stored file's backing object."""
        return await self.backend.delete(stored_file.storage_path)
