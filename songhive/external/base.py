"""
Abstract base class for external library adapters.
"""

import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, AsyncIterator, ClassVar, Optional

from .errors import ExternalLibraryError, UnsupportedExternalOperation
from .types import (
    ExternalAlbumMetadata,
    ExternalArtistMetadata,
    ExternalContents,
    ExternalHealth,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalMutationResult,
    ExternalPlaylistMetadata,
    ExternalStream,
    ExternalTrackMetadata,
)


class ExternalLibraryAdapter(ABC):
    """Abstract base class for external library adapters."""

    provider_type: ClassVar[str] = ""
    user_configurable: ClassVar[bool] = False

    _REDACTED_KEYS = re.compile(r"(secret|password|token|key|credential|cookie|header)", re.IGNORECASE)

    def __init__(self) -> None:
        self._capabilities: Optional[ExternalLibraryCapabilities] = None

    @abstractmethod
    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        """Validate provider configuration and return capabilities."""

    @abstractmethod
    async def iter_items(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalItemRef]:
        """Asynchronously iterate provider items."""
        if False:
            yield ExternalItemRef(provider_key="", display_path="")

    async def iter_albums(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalAlbumMetadata]:
        """Asynchronously iterate provider albums (entity-backed providers)."""
        raise UnsupportedExternalOperation("iter_albums is not supported by this adapter")
        if False:
            yield ExternalAlbumMetadata(provider_key="", title="")

    async def iter_artists(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalArtistMetadata]:
        """Asynchronously iterate provider artists (entity-backed providers)."""
        raise UnsupportedExternalOperation("iter_artists is not supported by this adapter")
        if False:
            yield ExternalArtistMetadata(provider_key="", name="")

    async def iter_playlists(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalPlaylistMetadata]:
        """Asynchronously iterate provider playlists (entity-backed providers)."""
        raise UnsupportedExternalOperation("iter_playlists is not supported by this adapter")
        if False:
            yield ExternalPlaylistMetadata(provider_key="", title="")

    def capabilities(self) -> ExternalLibraryCapabilities:
        """Return the cached capabilities populated by validate_config."""
        if self._capabilities is None:
            raise ExternalLibraryError("Capabilities have not been loaded; call validate_config first")
        return self._capabilities

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        """Read metadata/tags for the given item."""
        raise UnsupportedExternalOperation("read_metadata is not supported by this adapter")

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Open a byte stream for the given item, optionally constrained to a byte range."""
        raise UnsupportedExternalOperation("open_stream is not supported by this adapter")

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Return a complete byte stream for the given item."""
        raise UnsupportedExternalOperation("download is not supported by this adapter")

    async def compute_sha256(self, config: dict, item: ExternalItemRef) -> str:
        """Compute the SHA-256 hash of the item's audio payload."""
        raise UnsupportedExternalOperation("compute_sha256 is not supported by this adapter")

    async def write_metadata(
        self,
        config: dict,
        item: ExternalItemRef,
        metadata: ExternalTrackMetadata,
    ) -> ExternalMutationResult:
        """Write metadata/tags back to the provider."""
        raise UnsupportedExternalOperation("write_metadata is not supported by this adapter")

    async def rename_source(
        self,
        config: dict,
        item: ExternalItemRef,
        new_name: str,
    ) -> ExternalItemRef:
        """Rename the source file to ``new_name`` and return the updated reference."""
        raise UnsupportedExternalOperation("rename_source is not supported by this adapter")

    async def delete_source(self, config: dict, item: ExternalItemRef) -> ExternalMutationResult:
        """Delete the item from the provider."""
        raise UnsupportedExternalOperation("delete_source is not supported by this adapter")

    async def healthcheck(self, config: dict) -> ExternalHealth:
        """Check whether the provider is healthy."""
        raise UnsupportedExternalOperation("healthcheck is not supported by this adapter")

    def external_url(self, kind: str, provider_key: str) -> Optional[str]:
        """Return the provider's public browse URL for an entity, or None."""
        return None

    async def iter_contents(
        self,
        config: dict,
        kind: str,
        provider_key: str,
        *,
        etag: Optional[str] = None,
    ) -> "ExternalContents":
        """
        Return the ordered track contents of a lazy container.

        Adapters advertising ``limits["lazy_contents"]`` implement this for
        ``kind`` in ("playlist", "album"). Passing ``etag`` (the previously
        captured contents etag) makes the request conditional; adapters raise
        ``ContentsNotModified`` when the provider answers 304.
        """
        raise UnsupportedExternalOperation("iter_contents is not supported by this adapter")

    async def search(
        self,
        config: dict,
        query: str,
        *,
        limit: int = 20,
    ) -> list[dict]:
        """
        Search the provider for entities without persisting anything.

        Adapters advertising ``limits["search"]`` implement this. Each result
        is a normalized transient dict::

            {"kind": "track"|"album"|"artist"|"playlist",
             "provider_key": str, "title": str,
             "subtitle": Optional[str], "image_url": Optional[str],
             "external_url": Optional[str]}

        The default implementation returns no results.
        """
        return []

    def entity_from_payload(
        self,
        config: dict,
        kind: str,
        payload: dict,
    ) -> Optional[Any]:
        """
        Re-map a cached raw provider payload onto a metadata object.

        Used by the catalog-first materialization path — ``payload`` is the
        ``raw_metadata`` dict stored in the provider catalog. Returns an
        ``ExternalItemRef``/``External*Metadata`` matching ``kind``, or
        ``None`` when the payload can't be mapped.
        """
        return None

    async def fetch_entity_metadata(
        self,
        config: dict,
        kind: str,
        provider_key: str,
    ) -> Optional[Any]:
        """
        Fetch a single entity's metadata by provider key.

        Used by the import/materialization path when the provider catalog
        cache misses. Returns an ``ExternalItemRef``/``External*Metadata``
        matching ``kind``, or ``None`` when unsupported/not found.
        """
        return None

    async def fetch_entity_payload(
        self,
        config: dict,
        kind: str,
        provider_key: str,
    ) -> Optional[dict]:
        """
        Fetch the raw provider payload for an entity.

        Powers admin tooling that force-refreshes ``provider_catalog_entries``
        — the catalog stores raw payloads (re-mapped through
        ``entity_from_payload``), so the mapped metadata shape would not
        round-trip. Returns ``None`` when unsupported or not found.
        """
        return None

    def sanitize_config_for_response(self, config: dict) -> dict:
        """Return a shallow copy of config with sensitive values redacted."""
        redacted: dict[str, Any] = {}
        for key, value in config.items():
            if self._REDACTED_KEYS.search(key):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = value
        return redacted
