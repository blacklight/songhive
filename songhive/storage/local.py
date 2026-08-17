"""
Local filesystem storage backend.
"""

import os
from pathlib import Path
from typing import BinaryIO, Optional

from .base import StorageBackend


class LocalStorage(StorageBackend):
    """Store media files on the local filesystem."""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    async def store(self, file: BinaryIO, path: str, content_type: Optional[str] = None) -> str:
        """Store a file on the local filesystem."""
        full_path = self.base_path / path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(full_path, "wb") as dest:
            while chunk := file.read(64 * 1024):
                dest.write(chunk)

        return path

    async def retrieve(self, path: str) -> Optional[Path]:
        """Retrieve a file path from local storage."""
        full_path = self.base_path / path
        if full_path.exists():
            return full_path
        return None

    async def delete(self, path: str) -> bool:
        """Delete a file from local storage."""
        full_path = self.base_path / path
        if full_path.exists():
            os.remove(full_path)
            return True
        return False

    async def exists(self, path: str) -> bool:
        """Check if a file exists in local storage."""
        return (self.base_path / path).exists()
