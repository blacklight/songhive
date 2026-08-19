"""
Abstract storage backend interface.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Optional


class StorageBackend(ABC):
    """Abstract base class for media storage backends."""

    @abstractmethod
    async def store(self, file: BinaryIO, path: str, content_type: Optional[str] = None) -> str:
        """
        Store a file and return its storage path/key.

        :param file: File-like object to store.
        :param path: Relative path/key for the file.
        :param content_type: MIME type of the file.
        :returns: The resolved storage path/URL.
        """

    @abstractmethod
    async def retrieve(self, path: str) -> Optional[Path]:
        """
        Retrieve a file by its storage path.

        :param path: The storage path/key.
        :returns: Local path to the file (may be a temp file for remote backends),
                  or None if not found.
        """

    @abstractmethod
    async def delete(self, path: str) -> bool:
        """
        Delete a file from storage.

        :param path: The storage path/key.
        :returns: True if deleted, False if not found.
        """

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if a file exists in storage."""

    @abstractmethod
    async def url(self, path: str, cdn_prefix: Optional[str] = None) -> str:
        """Return the public URL for a stored path, optionally prefixed with a CDN URL."""
