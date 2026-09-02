"""
Shared dataclasses for external library adapters.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional


@dataclass(frozen=True)
class ExternalItemRef:
    """Reference to an item on an external provider."""

    provider_key: str
    display_path: str
    etag: Optional[str] = None
    mtime: Optional[datetime] = None
    size: Optional[int] = None
    mime_type: Optional[str] = None
    checksum: Optional[str] = None
    sha256: Optional[str] = None


@dataclass(frozen=True)
class ExternalTrackMetadata:
    """Metadata for a track stored on an external provider."""

    title: str
    artist: str
    album: str
    album_artist: str
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    duration: Optional[float] = None
    release_year: Optional[int] = None
    genre: Optional[str] = None
    musicbrainz_id: Optional[str] = None
    cover_art: Optional[bytes] = None
    cover_art_mime: Optional[str] = None
    raw_metadata: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class ExternalStream:
    """Container describing how to stream a track from an external provider."""

    kind: Literal["path", "iterator", "url"]
    path: Optional[Path] = None
    iterator: Optional[AsyncIterator[bytes]] = None
    url: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None
    supports_range: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    temporary: bool = False
    safe_to_redirect: bool = False


@dataclass(frozen=True)
class ExternalMutationResult:
    """Result of a mutation performed on an external provider."""

    provider_key: str
    etag: Optional[str] = None
    mtime: Optional[datetime] = None
    checksum: Optional[str] = None
    sha256: Optional[str] = None


@dataclass(frozen=True)
class ExternalHealth:
    """Health check result for an external provider."""

    ok: bool
    message: Optional[str] = None
    details: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class ExternalLibraryCapabilities:
    """Capability flags published by an external library adapter."""

    list_items: bool = False
    read_bytes: bool = False
    stream_url: bool = False
    range_read: bool = False
    download: bool = False
    compute_hash: bool = False
    read_tags: bool = False
    write_tags: bool = False
    rename_source: bool = False
    delete_source: bool = False
    detect_changes: bool = False
    validate_config: bool = False
    limits: Optional[dict[str, Any]] = None
