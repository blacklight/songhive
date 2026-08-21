"""
Metadata extraction tests.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.id3 import APIC, TALB, TIT2, TPE1
from mutagen.mp3 import MP3

from songhive.services.metadata import AudioMetadata, extract_metadata


def test_extract_metadata_nonexistent():
    """Test metadata extraction gracefully handles missing files."""
    result = extract_metadata(Path("/nonexistent/file.mp3"))
    assert isinstance(result, AudioMetadata)
    assert result.title is None


@pytest.fixture
def tagged_mp3(tmp_path):
    """Create a minimal MP3 with ID3 tags and embedded cover art."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")

    path = tmp_path / "song.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.5",
            "-b:a",
            "128k",
            "-ac",
            "1",
            "-ar",
            "44100",
            str(path),
        ],
        check=True,
        capture_output=True,
    )

    audio = MP3(str(path))
    audio.tags["TIT2"] = TIT2(encoding=3, text="Test Title")
    audio.tags["TPE1"] = TPE1(encoding=3, text="Test Artist")
    audio.tags["TALB"] = TALB(encoding=3, text="Test Album")
    audio.tags["APIC"] = APIC(
        encoding=3,
        mime="image/jpeg",
        type=3,
        desc="cover",
        data=b"\xff\xd8\xff\xe0fake cover",
    )
    audio.save()
    return path


def test_extract_metadata_parses_tags_and_cover(tagged_mp3):
    """extract_metadata returns tags, cover art and raw tags."""
    result = extract_metadata(tagged_mp3)
    assert result.title == "Test Title"
    assert result.artist == "Test Artist"
    assert result.album == "Test Album"
    assert result.cover_art == b"\xff\xd8\xff\xe0fake cover"
    assert result.cover_art_mime == "image/jpeg"
    assert "TIT2" in result.raw_tags
    assert result.mimetype == "audio/mp3"
