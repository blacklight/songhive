"""
Abstract base class for external library adapters.
"""

import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, AsyncIterator, ClassVar, Optional

from .errors import ExternalLibraryError, UnsupportedExternalOperation
from .types import (
    ExternalHealth,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalMutationResult,
    ExternalStream,
    ExternalTrackMetadata,
)


class ExternalLibraryAdapter(ABC):
    """Abstract base class for external library adapters."""

    provider_type: ClassVar[str] = ""
    user_configurable: ClassVar[bool] = False

    _REDACTED_KEYS = re.compile(r"(secret|password|token|key|credential)", re.IGNORECASE)

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
    ) -> AsyncIterator[ExternalItemRef]:
        """Asynchronously iterate provider items."""
        if False:
            yield ExternalItemRef(provider_key="", display_path="")

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

    async def delete_source(self, config: dict, item: ExternalItemRef) -> ExternalMutationResult:
        """Delete the item from the provider."""
        raise UnsupportedExternalOperation("delete_source is not supported by this adapter")

    async def healthcheck(self, config: dict) -> ExternalHealth:
        """Check whether the provider is healthy."""
        raise UnsupportedExternalOperation("healthcheck is not supported by this adapter")

    def sanitize_config_for_response(self, config: dict) -> dict:
        """Return a shallow copy of config with sensitive values redacted."""
        redacted: dict[str, Any] = {}
        for key, value in config.items():
            if self._REDACTED_KEYS.search(key):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = value
        return redacted
