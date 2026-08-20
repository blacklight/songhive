"""
Local filesystem storage backend using aiofiles for non-blocking I/O.
"""

from pathlib import Path
from typing import BinaryIO, Optional

import aiofiles
import aiofiles.os

from .base import StorageBackend


class LocalStorage(StorageBackend):
    """Store media files on the local filesystem."""

    def __init__(self, base_path: Path, max_upload_size: Optional[int] = None):
        super().__init__(max_upload_size=max_upload_size)
        self.base_path = base_path
        self._resolved_base = self.base_path.resolve()
        self._resolved_base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        """Resolve a relative path under base_path, rejecting escapes."""
        if Path(path).is_absolute():
            raise ValueError(f"Absolute storage paths are not allowed: {path!r}")
        if ".." in Path(path).parts:
            raise ValueError(f"Storage paths may not contain '..' segments: {path!r}")

        full_path = (self.base_path / path).resolve()
        if not full_path.is_relative_to(self._resolved_base):
            raise ValueError(f"Storage path escapes base directory: {path!r}")

        return full_path

    async def store(self, file: BinaryIO, path: str, *_, **__) -> str:
        """Store a file on the local filesystem."""
        size = self._file_size(file)
        self._rewind(file)
        self._check_upload_size(size)

        full_path = self._resolve(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            total = 0
            async with aiofiles.open(full_path, "wb") as dest:
                while chunk := file.read(64 * 1024):
                    total += len(chunk)
                    self._check_upload_size(total)
                    await dest.write(chunk)
            return path
        except Exception:
            if await aiofiles.os.path.exists(full_path):
                await aiofiles.os.remove(full_path)
            raise

    async def retrieve(self, path: str) -> Optional[Path]:
        """Retrieve a file path from local storage."""
        full_path = self._resolve(path)
        if await aiofiles.os.path.exists(full_path):
            return full_path
        return None

    async def delete(self, path: str) -> bool:
        """Delete a file from local storage."""
        full_path = self._resolve(path)
        if await aiofiles.os.path.exists(full_path):
            await aiofiles.os.remove(full_path)
            return True
        return False

    async def exists(self, path: str) -> bool:
        """Check if a file exists in local storage."""
        full_path = self._resolve(path)
        return await aiofiles.os.path.exists(full_path)

    async def url(self, path: str, cdn_prefix: Optional[str] = None) -> str:
        """Return the public URL for a stored path."""
        # Validate the path without disclosing the resolved filesystem location.
        self._resolve(path)
        if cdn_prefix:
            return f"{cdn_prefix.rstrip('/')}/{path}"
        return f"/{path.lstrip('/')}"
