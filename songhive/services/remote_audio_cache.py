"""
Bounded on-disk cache for remuxed remote audio (provider-agnostic).

Segmented remote streams (TIDAL DASH/Hi-Res, future Spotify/YouTube-style
providers) are remuxed through ffmpeg once per ``(provider, item, quality)``
and cached as a real file so later plays are seekable and cheap. The cache is
bounded two ways, whichever evicts first: files not touched for
``retention_seconds`` are removed, and the directory is trimmed oldest-first
to stay under ``max_bytes``.

Concurrent builds of the same key are deduplicated by a per-key asyncio lock;
the file is built under a temp name and atomically renamed so readers never
see a partial artifact (and a second racing process simply overwrites it).
"""

import asyncio
import hashlib
import logging
import os
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

from ..config.schema import SonghiveConfig

logger = logging.getLogger(__name__)


class RemoteAudioCache:
    """File cache with per-key build dedup and time/byte eviction."""

    def __init__(
        self,
        directory: Path,
        *,
        retention_seconds: int,
        max_bytes: int,
    ) -> None:
        self.directory = Path(directory)
        self.retention_seconds = retention_seconds
        self.max_bytes = max_bytes
        self._locks: dict[str, asyncio.Lock] = {}

    def path_for(self, cache_key: str, ext: str = ".flac") -> Path:
        """Return the canonical cache path for ``cache_key`` (hashed name)."""
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}{ext}"

    async def get_or_build(
        self,
        cache_key: str,
        build: Callable[[Path], Awaitable[None]],
        *,
        ext: str = ".flac",
    ) -> Path:
        """
        Return the cached file for ``cache_key``, building it if missing.

        ``build`` receives a temp path in the cache directory and must write
        the artifact there; the file is then atomically moved into place.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.path_for(cache_key, ext)
        if target.exists():
            os.utime(target)
            return target

        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            if target.exists():
                os.utime(target)
                return target
            tmp = self.directory / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            try:
                await build(tmp)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
        await asyncio.to_thread(self.evict)
        return target

    def evict(self) -> None:
        """Enforce retention: drop expired files, then oldest-first to the cap."""
        try:
            entries: list[tuple[float, int, Path]] = []
            now = time.time()
            for path in self.directory.iterdir():
                if not path.is_file() or path.name.startswith("."):
                    continue
                stat = path.stat()
                if self.retention_seconds > 0 and now - stat.st_mtime > self.retention_seconds:
                    path.unlink(missing_ok=True)
                    continue
                entries.append((stat.st_mtime, stat.st_size, path))

            total = sum(size for _, size, _ in entries)
            if self.max_bytes > 0:
                for _, size, path in sorted(entries):
                    if total <= self.max_bytes:
                        break
                    path.unlink(missing_ok=True)
                    total -= size
        except FileNotFoundError:
            return


_cache: Optional["RemoteAudioCache"] = None


def get_remote_audio_cache(config: SonghiveConfig) -> RemoteAudioCache:
    """Return the process-wide remote-audio cache built from the config.

    Settings live under ``external_libraries.tidal`` (the first provider with
    segmented streams): ``remote_cache_dir``, ``remote_cache_max_bytes`` and
    ``remote_cache_retention_seconds``.
    """
    global _cache
    if _cache is not None:
        return _cache

    tidal = config.external_libraries.tidal
    if tidal.remote_cache_dir is not None:
        directory = Path(tidal.remote_cache_dir)
    else:
        base = config.external_libraries.stream_temp_dir or Path(tempfile.gettempdir())
        directory = Path(base) / "remote-audio"
    _cache = RemoteAudioCache(
        directory,
        retention_seconds=tidal.remote_cache_retention_seconds,
        max_bytes=tidal.remote_cache_max_bytes,
    )
    return _cache
