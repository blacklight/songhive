"""
Local filesystem external-library adapter.

Indexes audio files from a server-mounted directory and serves them by path,
reusing Songhive's existing metadata and streaming infrastructure.
"""

import asyncio
import fnmatch
import hashlib
import logging
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from ..config.constants import AUDIO_EXTENSIONS
from ..config.loader import load_config
from .base import ExternalLibraryAdapter
from .errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from .types import (
    ExternalHealth,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalMutationResult,
    ExternalStream,
    ExternalTrackMetadata,
)

logger = logging.getLogger(__name__)

_DEFAULT_EXTENSIONS = frozenset(AUDIO_EXTENSIONS)

# Cap concurrent ffmpeg invocations across all local library syncs.
_FFMPEG_SEMAPHORE = asyncio.Semaphore(2)


class LocalExternalAdapter(ExternalLibraryAdapter):
    """Adapter that indexes audio files from a local filesystem directory."""

    provider_type = "local"
    user_configurable = False

    def __init__(self) -> None:
        super().__init__()
        self.resolved_root: Optional[Path] = None
        self._config_root: Optional[str] = None

    def _normalize_local_roots(self, raw_roots: list[str]) -> list[Path]:
        """Resolve configured allowlist roots to absolute paths."""
        normalized = []
        for root in raw_roots or []:
            if not root:
                continue
            try:
                path = Path(root).expanduser().resolve()
            except (OSError, ValueError):
                continue
            normalized.append(path)
        return normalized

    def _resolve_root(self, config: dict) -> Path:
        """Validate and resolve the configured root against the allowlist."""
        root_str = config.get("root")
        if not isinstance(root_str, str) or not root_str:
            raise ExternalConfigError(
                'config["root"] is required and must be a non-empty string',
                field="root",
            )

        if self.resolved_root is not None and self._config_root == root_str:
            return self.resolved_root

        try:
            resolved = Path(root_str).expanduser().resolve()
        except (OSError, ValueError) as exc:
            raise ExternalConfigError(
                f"Invalid root path: {root_str}: {exc}",
                field="root",
            ) from exc

        songhive_config = load_config([])
        allowed_roots = self._normalize_local_roots(songhive_config.external_libraries.local_roots)
        if not allowed_roots:
            raise ExternalConfigError(
                "No local_roots are configured; local libraries are disabled",
                field="root",
            )

        allowed = False
        for allowed_root in allowed_roots:
            if resolved == allowed_root:
                allowed = True
                break
            try:
                resolved.relative_to(allowed_root)
                allowed = True
                break
            except ValueError:
                continue

        if not allowed:
            raise ExternalConfigError(
                f"Root {resolved} is not within any configured local_roots",
                field="root",
            )

        if not resolved.exists():
            raise ExternalConfigError(
                f"Root does not exist: {resolved}",
                field="root",
            )
        if not resolved.is_dir():
            raise ExternalConfigError(
                f"Root is not a directory: {resolved}",
                field="root",
            )
        if not os.access(str(resolved), os.R_OK):
            raise ExternalConfigError(
                f"Root is not readable: {resolved}",
                field="root",
            )

        self.resolved_root = resolved
        self._config_root = root_str
        return resolved

    def _resolve_item_path(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        require_exists: bool = True,
        require_file: bool = True,
    ) -> Path:
        """Resolve and validate a provider key to a real path under the library root.

        Absolute provider keys and ``..`` traversal are rejected. The resolved
        path must remain within the allowlisted root. When ``follow_symlinks``
        is disabled, symlinks are rejected before opening.
        """
        root = self._resolve_root(config)
        provider_key = item.provider_key

        if not provider_key:
            raise ExternalItemNotFound("Empty provider key", provider_key=provider_key)

        if Path(provider_key).is_absolute() or ".." in Path(provider_key).parts:
            raise ExternalPermissionDenied(
                f"Invalid provider key: {provider_key}",
                operation="resolve_item_path",
            )

        follow_symlinks = bool(config.get("follow_symlinks", False))
        candidate = root / provider_key

        if not follow_symlinks and candidate.is_symlink():
            raise ExternalPermissionDenied(
                f"Symlinks are not followed for this library: {provider_key}",
                operation="resolve_item_path",
            )

        try:
            resolved = candidate.resolve()
        except (OSError, ValueError) as exc:
            raise ExternalItemNotFound(
                f"Could not resolve path: {provider_key}",
                provider_key=provider_key,
            ) from exc

        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ExternalPermissionDenied(
                f"Path resolves outside the library root: {provider_key}",
                operation="resolve_item_path",
            ) from exc

        if require_exists and not resolved.exists():
            raise ExternalItemNotFound(
                f"File not found: {provider_key}",
                provider_key=provider_key,
            )

        if require_file and not resolved.is_file():
            raise ExternalItemNotFound(
                f"Not a file: {provider_key}",
                provider_key=provider_key,
            )

        return resolved

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        """Validate the root and return the adapter's capabilities."""
        root = self._resolve_root(config)

        write_tags = bool(config.get("allow_write_tags")) and os.access(str(root), os.W_OK)
        delete_source = bool(config.get("allow_delete_source")) and os.access(str(root), os.W_OK)

        self._capabilities = ExternalLibraryCapabilities(
            list_items=True,
            read_bytes=True,
            range_read=True,
            download=True,
            compute_hash=bool(config.get("allow_hashing", True)),
            read_tags=True,
            write_tags=write_tags,
            delete_source=delete_source,
            detect_changes=True,
            validate_config=True,
            limits={"checksum_algorithm": "sha256"},
        )
        assert self._capabilities  # for mypy
        return self._capabilities

    def _is_excluded(self, rel_posix: str, exclude: list[str]) -> bool:
        """Return True when the root-relative path matches any exclude pattern."""
        return any(
            fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(Path(rel_posix).name, pattern) for pattern in exclude
        )

    def _walk_files(
        self,
        root: Path,
        base: Path,
        extensions: frozenset[str],
        recursive: bool,
        follow_symlinks: bool,
        exclude: list[str],
    ):
        """Yield ``(rel_path, stat_result)`` tuples for matching audio files."""
        if not base.exists() or not base.is_dir():
            return

        extension_set = {ext.lstrip(".").lower() for ext in extensions}
        stack = [base]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(str(current)) as it:
                    for entry in it:
                        full = Path(entry.path)
                        try:
                            rel = full.relative_to(root)
                        except ValueError:
                            continue
                        rel_posix = rel.as_posix()

                        if self._is_excluded(rel_posix, exclude):
                            continue

                        if entry.is_symlink() and not follow_symlinks:
                            continue

                        if entry.is_dir(follow_symlinks=follow_symlinks):
                            if recursive:
                                stack.append(full)
                            continue

                        if entry.is_file(follow_symlinks=follow_symlinks):
                            name = full.name
                            if name.lower().rsplit(".", 1)[-1] in extension_set:
                                try:
                                    st = entry.stat(follow_symlinks=follow_symlinks)
                                except (OSError, ValueError):
                                    continue
                                yield rel, st
            except OSError:
                continue

    async def iter_items(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalItemRef]:
        """Yield audio files under the configured root."""
        root = self._resolve_root(config)

        extensions = frozenset(config.get("extensions", _DEFAULT_EXTENSIONS))
        recursive = bool(config.get("recursive", True))
        follow_symlinks = bool(config.get("follow_symlinks", False))
        exclude = list(config.get("exclude", []))

        base = root
        if scope:
            scope = scope.lstrip("/")
            try:
                scope_path = (root / scope).resolve()
                scope_path.relative_to(root)
            except (ValueError, OSError) as exc:
                raise ExternalConfigError(
                    f"Scope {scope!r} is outside the library root",
                    field="scope",
                ) from exc
            base = scope_path

        records = await asyncio.to_thread(
            list,
            self._walk_files(root, base, extensions, recursive, follow_symlinks, exclude),
        )

        for rel, st in records:
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            if since is not None and st.st_mtime <= since.timestamp():
                continue

            mime_type = mimetypes.guess_type(rel.as_posix())[0]
            if mime_type is None:
                mime_type = f"audio/{rel.suffix.lstrip('.').lower()}" if rel.suffix else "application/octet-stream"

            yield ExternalItemRef(
                provider_key=rel.as_posix(),
                display_path=rel.as_posix(),
                etag=f"{st.st_mtime_ns}:{st.st_size}",
                mtime=mtime,
                size=st.st_size,
                mime_type=mime_type,
                checksum=None,
                sha256=None,
            )

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        """Read tags from a local audio file."""
        path = await asyncio.to_thread(self._resolve_item_path, config, item)

        from ..services.metadata import extract_metadata

        meta = extract_metadata(path)

        album_artist = ""
        raw_tags = meta.raw_tags or {}
        if isinstance(raw_tags, dict):
            for key, values in raw_tags.items():
                if key.lower() in {"albumartist", "album artist", "talb"} and values:
                    album_artist = str(values[0])
                    break
        if not album_artist:
            album_artist = meta.artist or ""

        raw_metadata: dict[str, Any] = {
            "display_path": item.provider_key,
            "mimetype": meta.mimetype,
            "raw_tags": raw_tags,
        }

        return ExternalTrackMetadata(
            title=meta.title or item.provider_key,
            artist=meta.artist or "",
            album=meta.album or "",
            album_artist=album_artist,
            track_number=meta.track_number,
            disc_number=meta.disc_number,
            duration=meta.duration,
            release_year=meta.year,
            genre=meta.genre,
            musicbrainz_id=None,
            cover_art=meta.cover_art,
            cover_art_mime=meta.cover_art_mime,
            raw_metadata=raw_metadata,
        )

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Return a path-backed stream for the item."""
        path = await asyncio.to_thread(self._resolve_item_path, config, item)

        return ExternalStream(
            kind="path",
            path=path,
            content_type=item.mime_type or "application/octet-stream",
            size=item.size,
            supports_range=True,
            temporary=False,
            headers={},
        )

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Return a complete path-backed stream for the item."""
        return await self.open_stream(config, item)

    async def compute_sha256(self, config: dict, item: ExternalItemRef) -> str:
        """Compute the audio hash or raw SHA-256 for the item."""
        path = await asyncio.to_thread(self._resolve_item_path, config, item)

        if config.get("fast_hash"):

            def _raw_hash() -> str:
                hasher = hashlib.sha256()
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        hasher.update(chunk)
                return hasher.hexdigest()

            return await asyncio.to_thread(_raw_hash)

        from ..services.storage import audio_hash

        async with _FFMPEG_SEMAPHORE:
            return await audio_hash(path)

    async def write_metadata(
        self,
        config: dict,
        item: ExternalItemRef,
        metadata: ExternalTrackMetadata,
    ) -> ExternalMutationResult:
        """Write tags back to the source audio file."""
        if not self.capabilities().write_tags:
            raise UnsupportedExternalOperation("write_tags is not enabled for this local library")

        path = await asyncio.to_thread(self._resolve_item_path, config, item)

        from ..services.metadata import AudioMetadataWrite, write_metadata

        write_obj = AudioMetadataWrite(
            title=metadata.title,
            artist=metadata.artist,
            album=metadata.album,
            track_number=metadata.track_number,
            disc_number=metadata.disc_number,
            genre=metadata.genre,
            year=metadata.release_year,
            cover_art=metadata.cover_art,
            cover_art_mime=metadata.cover_art_mime,
        )

        def _write() -> None:
            write_metadata(path, write_obj)

        await asyncio.to_thread(_write)

        st = path.stat()
        new_mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)

        return ExternalMutationResult(
            provider_key=item.provider_key,
            mtime=new_mtime,
        )

    async def delete_source(self, config: dict, item: ExternalItemRef) -> ExternalMutationResult:
        """Delete the source file from the filesystem."""
        if not self.capabilities().delete_source:
            raise UnsupportedExternalOperation("delete_source is not enabled for this local library")

        await asyncio.to_thread(self._resolve_item_path, config, item)
        root = self._resolve_root(config)
        candidate = root / item.provider_key

        await asyncio.to_thread(os.unlink, candidate)

        return ExternalMutationResult(provider_key=item.provider_key)

    async def healthcheck(self, config: dict) -> ExternalHealth:
        """Check whether the configured root is present and readable."""
        try:
            root = self._resolve_root(config)
        except ExternalConfigError as exc:
            return ExternalHealth(ok=False, message=str(exc))

        if root.exists() and root.is_dir() and os.access(str(root), os.R_OK):
            return ExternalHealth(ok=True, message="Local root is healthy")

        return ExternalHealth(ok=False, message=f"Local root is not accessible: {root}")
