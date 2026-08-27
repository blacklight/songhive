"""
Music metadata utilities - re-exports from services.metadata.
"""

from ..services.metadata import (
    AudioMetadata,
    AudioMetadataWrite,
    extract_metadata,
    write_metadata,
)

__all__ = ["AudioMetadata", "AudioMetadataWrite", "extract_metadata", "write_metadata"]
