"""
Metadata extraction tests.
"""

from pathlib import Path

from songhive.services.metadata import AudioMetadata, extract_metadata


def test_extract_metadata_nonexistent():
    """Test metadata extraction gracefully handles missing files."""
    result = extract_metadata(Path("/nonexistent/file.mp3"))
    assert isinstance(result, AudioMetadata)
    assert result.title is None
