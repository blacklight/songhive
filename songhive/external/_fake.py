"""
In-memory fake external library adapter for tests.
"""

import hashlib
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from .base import ExternalLibraryAdapter
from .errors import ExternalConfigError, ExternalItemNotFound, ExternalWriteBackError
from .types import (
    ExternalHealth,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalMutationResult,
    ExternalStream,
    ExternalTrackMetadata,
)


class FakeExternalAdapter(ExternalLibraryAdapter):
    """Fake adapter backed by an in-memory dict for end-to-end testing."""

    provider_type = "fake"
    user_configurable = True

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        """Validate that the config contains an ``items`` dict."""
        items = config.get("items")
        if not isinstance(items, dict):
            raise ExternalConfigError('config["items"] must be a dict', field="items")

        self._capabilities = ExternalLibraryCapabilities(
            list_items=True,
            read_bytes=True,
            stream_url=True,
            range_read=True,
            download=True,
            compute_hash=True,
            read_tags=True,
            write_tags=True,
            delete_source=True,
            detect_changes=True,
            validate_config=True,
            limits={"checksum_algorithm": "sha256", "max_page_size": 1000},
        )
        return self._capabilities

    def _get_item(self, config: dict, provider_key: str) -> dict[str, Any]:
        """Return a normalized item record from the in-memory config."""
        items = config.get("items", {})
        value = items.get(provider_key)
        if value is None:
            raise ExternalItemNotFound(
                f"Item not found: {provider_key}",
                provider_key=provider_key,
            )

        if isinstance(value, bytes):
            return {
                "data": value,
                "metadata": {},
                "mimetype": "application/octet-stream",
                "mtime": None,
                "etag": None,
                "sha256": None,
                "checksum": None,
                "size": len(value),
            }

        if not isinstance(value, dict):
            raise ExternalConfigError(
                f"Item {provider_key!r} must be bytes or a dict",
                field=f"items.{provider_key}",
            )

        data = value.get("data", b"")
        if isinstance(data, str):
            data = data.encode()
        elif isinstance(data, list):
            data = bytes(data)

        return {
            "data": data,
            "metadata": value.get("metadata", {}) or {},
            "mimetype": value.get("mimetype", value.get("mime_type", "application/octet-stream")),
            "mtime": value.get("mtime"),
            "etag": value.get("etag"),
            "sha256": value.get("sha256"),
            "checksum": value.get("checksum"),
            "size": value.get("size", len(data)),
        }

    async def iter_items(
        self,
        config: dict,
        since: Optional[datetime] = None,
    ) -> AsyncIterator[ExternalItemRef]:
        """Yield an ``ExternalItemRef`` for every in-memory item."""
        items = config.get("items", {})
        for provider_key in items:
            record = self._get_item(config, provider_key)
            mtime = record["mtime"]
            if since is not None and mtime is not None and mtime <= since:
                continue
            yield self._make_ref(provider_key, record)

    def _make_ref(self, provider_key: str, record: dict[str, Any]) -> ExternalItemRef:
        data = record["data"]
        sha256 = record["sha256"] or hashlib.sha256(data).hexdigest()
        checksum = record["checksum"] or sha256
        etag = record["etag"] or sha256
        return ExternalItemRef(
            provider_key=provider_key,
            display_path=provider_key,
            etag=etag,
            mtime=record["mtime"],
            size=record["size"],
            mime_type=record["mimetype"],
            checksum=checksum,
            sha256=sha256,
        )

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        """Read metadata from the in-memory item record."""
        record = self._get_item(config, item.provider_key)
        meta = record["metadata"]

        title = meta.get("title") or item.provider_key
        artist = meta.get("artist") or ""
        album = meta.get("album") or ""
        album_artist = meta.get("album_artist") or artist or ""

        track_number = _to_int(meta.get("track_number"))
        disc_number = _to_int(meta.get("disc_number"))
        duration = _to_float(meta.get("duration"))
        release_year = _to_int(meta.get("release_year")) or _to_int(meta.get("year"))

        return ExternalTrackMetadata(
            title=str(title),
            artist=str(artist),
            album=str(album),
            album_artist=str(album_artist),
            track_number=track_number,
            disc_number=disc_number,
            duration=duration,
            release_year=release_year,
            genre=meta.get("genre"),
            musicbrainz_id=meta.get("musicbrainz_id") or meta.get("musicbrainz_trackid"),
            cover_art=meta.get("cover_art"),
            cover_art_mime=meta.get("cover_art_mime"),
            raw_metadata=dict(meta),
        )

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Open a byte stream for the item, optionally constrained to a range."""
        record = self._get_item(config, item.provider_key)
        data = record["data"]
        content_type = record["mimetype"] or item.mime_type or "application/octet-stream"

        if config.get("prefer_url") or config.get("safe_url"):
            return ExternalStream(
                kind="url",
                url=f"https://songhive.invalid/fake/{item.provider_key}",
                content_type=content_type,
                size=len(data),
                supports_range=False,
                headers={} if config.get("safe_url") else {"X-Fake-Auth": "token"},
                safe_to_redirect=bool(config.get("safe_url")),
            )

        if config.get("prefer_path"):
            fd, path = tempfile.mkstemp(prefix="fake-external-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
            except Exception:
                os.close(fd)
                raise
            return ExternalStream(
                kind="path",
                path=Path(path),
                content_type=content_type,
                size=len(data),
                supports_range=True,
                headers={},
                temporary=True,
            )

        start = 0
        end = max(len(data) - 1, 0)
        if range is not None:
            start, end = range
            if start < 0 or end >= len(data) or start > end:
                raise ExternalConfigError(f"Invalid byte range: {range}", field="range")
            data = data[start : end + 1]

        return ExternalStream(
            kind="iterator",
            iterator=self._stream_bytes(data),
            content_type=content_type,
            size=len(data),
            supports_range=True,
            headers={},
        )

    async def _stream_bytes(self, data: bytes) -> AsyncIterator[bytes]:
        chunk_size = 1024
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Return a full byte stream for the item."""
        return await self.open_stream(config, item)

    async def compute_sha256(self, config: dict, item: ExternalItemRef) -> str:
        """Return the SHA-256 hash of the item's payload."""
        record = self._get_item(config, item.provider_key)
        return hashlib.sha256(record["data"]).hexdigest()

    async def write_metadata(
        self,
        config: dict,
        item: ExternalItemRef,
        metadata: ExternalTrackMetadata,
    ) -> ExternalMutationResult:
        """Write metadata back to the in-memory item record."""
        items = config.get("items", {})
        if item.provider_key not in items:
            raise ExternalItemNotFound(
                f"Item not found: {item.provider_key}",
                provider_key=item.provider_key,
            )

        value = items[item.provider_key]
        if isinstance(value, bytes):
            items[item.provider_key] = {"data": value, "metadata": {}}
            value = items[item.provider_key]

        if not isinstance(value, dict):
            raise ExternalWriteBackError(
                f"Cannot write metadata to item {item.provider_key!r}",
                provider_key=item.provider_key,
            )

        new_meta: dict[str, Any] = {}
        if metadata.raw_metadata:
            new_meta.update(metadata.raw_metadata)

        explicit = {
            "title": metadata.title,
            "artist": metadata.artist,
            "album": metadata.album,
            "album_artist": metadata.album_artist,
            "track_number": metadata.track_number,
            "disc_number": metadata.disc_number,
            "duration": metadata.duration,
            "release_year": metadata.release_year,
            "genre": metadata.genre,
            "musicbrainz_id": metadata.musicbrainz_id,
            "cover_art": metadata.cover_art,
            "cover_art_mime": metadata.cover_art_mime,
        }
        for key, val in explicit.items():
            if val is not None:
                new_meta[key] = val

        value["metadata"] = new_meta
        mtime = datetime.now(timezone.utc)
        value["mtime"] = mtime
        value["etag"] = str(uuid.uuid4())

        data = value.get("data", b"")
        if isinstance(data, list):
            data = bytes(data)
        sha256 = hashlib.sha256(data).hexdigest()
        value["sha256"] = sha256

        return ExternalMutationResult(
            provider_key=item.provider_key,
            etag=value["etag"],
            mtime=mtime,
            checksum=sha256,
            sha256=sha256,
        )

    async def delete_source(self, config: dict, item: ExternalItemRef) -> ExternalMutationResult:
        """Remove the item from the in-memory store."""
        items = config.get("items", {})
        if item.provider_key not in items:
            raise ExternalItemNotFound(
                f"Item not found: {item.provider_key}",
                provider_key=item.provider_key,
            )
        del items[item.provider_key]
        return ExternalMutationResult(provider_key=item.provider_key)

    async def healthcheck(self, config: dict) -> ExternalHealth:
        """Always report healthy."""
        return ExternalHealth(ok=True, message="Fake adapter is healthy")


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
