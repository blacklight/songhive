"""
Shared dataclasses for audio output providers and their drivers.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Optional


@dataclass(frozen=True)
class OutputCapabilities:
    """Capability flags published by an audio output provider."""

    metadata_updates: bool = False
    pause_supported: bool = False
    seek_supported: bool = False
    multi_listener: bool = False
    user_configurable: bool = False


@dataclass(frozen=True)
class OutputHealth:
    """Runtime health check result for an output driver."""

    ok: bool
    message: Optional[str] = None
    details: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class TrackMeta:
    """Minimal track metadata passed to an output driver."""

    track_id: str
    title: str
    artist: str
    album: Optional[str] = None
    duration: Optional[float] = None


@dataclass(frozen=True)
class AudioSource:
    """
    Descriptor for the audio bytes a driver should play.

    Sources may be resolved from a local ``StoredFile`` path, an async byte
    iterator (e.g. an external library stream), or a remote URL. Only one of
    ``path``, ``iterator`` or ``url`` should be populated for a given source.
    """

    kind: Literal["path", "iterator", "url"]
    path: Optional[Path] = None
    iterator: Optional[AsyncIterator[bytes]] = None
    url: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None
