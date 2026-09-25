"""
Shared dataclasses for external library adapters.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional


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
    # Entity-provider extensions: richer, multi-valued provider metadata.
    # File providers leave these unset.
    artists: tuple[str, ...] = ()
    album_artists: tuple[str, ...] = ()
    genres: tuple[str, ...] = ()
    cover_url: Optional[str] = None
    description: Optional[str] = None
    composer: Optional[str] = None
    disc_count: Optional[int] = None
    provider_ids: dict[str, str] = field(default_factory=dict)


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
    # Entity-backed providers attach the full metadata inline so the sync
    # never needs a separate ``read_metadata`` call.
    metadata: Optional[ExternalTrackMetadata] = None


@dataclass(frozen=True)
class ExternalAlbumMetadata:
    """Provider-authored metadata for an album entity."""

    provider_key: str
    title: str
    artist_names: tuple[str, ...] = ()
    artist_provider_keys: tuple[str, ...] = ()
    release_year: Optional[int] = None
    genres: tuple[str, ...] = ()
    cover_url: Optional[str] = None
    description: Optional[str] = None
    provider_ids: dict[str, str] = field(default_factory=dict)
    etag: Optional[str] = None
    mtime: Optional[datetime] = None
    raw_metadata: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class ExternalArtistMetadata:
    """Provider-authored metadata for an artist entity."""

    provider_key: str
    name: str
    image_url: Optional[str] = None
    bio: Optional[str] = None
    provider_ids: dict[str, str] = field(default_factory=dict)
    etag: Optional[str] = None
    mtime: Optional[datetime] = None
    raw_metadata: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class ExternalPlaylistEntry:
    """One ordered entry of a provider playlist."""

    position: int
    track_provider_key: str


@dataclass(frozen=True)
class ExternalPlaylistMetadata:
    """Provider-authored metadata for a playlist entity."""

    provider_key: str
    title: str
    description: Optional[str] = None
    cover_url: Optional[str] = None
    owner_name: Optional[str] = None
    entries: tuple[ExternalPlaylistEntry, ...] = ()
    etag: Optional[str] = None
    mtime: Optional[datetime] = None
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
    # Value for the ``Content-Range`` response header (e.g. ``bytes 0-99/200``),
    # set when the stream was opened with an honoured byte range so the server
    # can answer with a proper 206 instead of a truncated 200.
    content_range: Optional[str] = None


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
    # Entity-backed providers advertise which entity kinds they enumerate.
    # ``list_items`` is reused for tracks.
    list_albums: bool = False
    list_artists: bool = False
    list_playlists: bool = False
    limits: Optional[dict[str, Any]] = None
