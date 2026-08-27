"""
Metadata write tests.
"""

import shutil
import subprocess
from pathlib import Path

import mutagen
import pytest

from songhive.music.metadata import extract_metadata
from songhive.services.metadata import AudioMetadataWrite, write_metadata

JPEG_DATA = b"\xff\xd8\xff\xe0cover"
PNG_DATA = b"\x89PNG\r\n\x1a\ncover"

FORMATS = ["mp3", "flac", "ogg", "opus", "m4a"]


def _make_silence(tmp_path: Path, ext: str) -> Path:
    """Generate a short sine-wave audio file in the requested format."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")

    path = tmp_path / f"sample.{ext}"
    codec = {
        "mp3": "libmp3lame",
        "flac": "flac",
        "ogg": "libvorbis",
        "opus": "libopus",
        "m4a": "aac",
    }[ext]
    sample_rate = "48000" if ext == "opus" else "44100"
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
            sample_rate,
            "-c:a",
            codec,
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _audio_hash(path: Path) -> str:
    """Compute an ffmpeg audio-stream-only SHA-256 hash."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")

    result = subprocess.run(
        [
            "ffmpeg",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c",
            "copy",
            "-f",
            "streamhash",
            "-hash",
            "sha256",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    line = result.stdout.strip().splitlines()[0]
    return line.split("=", 1)[1]


@pytest.fixture
def full_meta():
    return AudioMetadataWrite(
        title="Test Title",
        artist="Test Artist",
        album="Test Album",
        track_number=3,
        disc_number=1,
        genre="Rock",
        year=2022,
    )


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_metadata_round_trip(fmt, tmp_path, full_meta):
    """All supported fields round-trip for each format."""
    path = _make_silence(tmp_path, fmt)
    meta = AudioMetadataWrite(
        **{
            **full_meta.__dict__,
            "cover_art": JPEG_DATA,
            "cover_art_mime": "image/jpeg",
        }
    )

    write_metadata(path, meta)
    result = extract_metadata(path)

    assert result.title == "Test Title"
    assert result.artist == "Test Artist"
    assert result.album == "Test Album"
    assert result.track_number == 3
    assert result.disc_number == 1
    assert result.genre == "Rock"
    assert result.year == 2022
    assert result.cover_art == JPEG_DATA
    assert result.cover_art_mime == "image/jpeg"


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_metadata_cover_round_trip(fmt, tmp_path):
    """Cover art round-trips with the correct MIME type."""
    path = _make_silence(tmp_path, fmt)
    write_metadata(
        path,
        AudioMetadataWrite(
            title="Covered",
            cover_art=PNG_DATA,
            cover_art_mime="image/png",
        ),
    )
    result = extract_metadata(path)

    assert result.title == "Covered"
    assert result.cover_art == PNG_DATA
    assert result.cover_art_mime == "image/png"


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_metadata_clear_cover_art(fmt, tmp_path, full_meta):
    """Clearing cover art removes any previously embedded picture."""
    path = _make_silence(tmp_path, fmt)
    first = AudioMetadataWrite(
        **{
            **full_meta.__dict__,
            "cover_art": JPEG_DATA,
            "cover_art_mime": "image/jpeg",
        }
    )
    write_metadata(path, first)

    write_metadata(path, AudioMetadataWrite(clear_cover_art=True))
    result = extract_metadata(path)

    assert result.title == "Test Title"
    assert result.artist == "Test Artist"
    assert result.album == "Test Album"
    assert result.cover_art is None


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_metadata_partial_update(fmt, tmp_path, full_meta):
    """Writing a subset of fields leaves the other fields unchanged."""
    path = _make_silence(tmp_path, fmt)
    first = AudioMetadataWrite(
        **{
            **full_meta.__dict__,
            "cover_art": JPEG_DATA,
            "cover_art_mime": "image/jpeg",
        }
    )
    write_metadata(path, first)

    write_metadata(path, AudioMetadataWrite(title="Partial Title"))
    result = extract_metadata(path)

    assert result.title == "Partial Title"
    assert result.artist == "Test Artist"
    assert result.album == "Test Album"
    assert result.track_number == 3
    assert result.disc_number == 1
    assert result.genre == "Rock"
    assert result.year == 2022
    assert result.cover_art == JPEG_DATA
    assert result.cover_art_mime == "image/jpeg"


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_metadata_tagless_file(fmt, tmp_path, full_meta):
    """Writing to a file with no existing tags creates them."""
    path = _make_silence(tmp_path, fmt)
    audio = mutagen.File(str(path), easy=False)
    audio.delete()
    audio.save()

    write_metadata(
        path,
        AudioMetadataWrite(
            **{
                **full_meta.__dict__,
                "cover_art": PNG_DATA,
                "cover_art_mime": "image/png",
            }
        ),
    )
    result = extract_metadata(path)

    assert result.title == "Test Title"
    assert result.artist == "Test Artist"
    assert result.album == "Test Album"
    assert result.track_number == 3
    assert result.disc_number == 1
    assert result.genre == "Rock"
    assert result.year == 2022
    assert result.cover_art == PNG_DATA
    assert result.cover_art_mime == "image/png"


@pytest.mark.parametrize("fmt", FORMATS)
def test_write_metadata_audio_hash_invariant(fmt, tmp_path, full_meta):
    """Changing tags does not change the audio-only hash."""
    path = _make_silence(tmp_path, fmt)
    before = _audio_hash(path)

    write_metadata(
        path,
        AudioMetadataWrite(
            **{
                **full_meta.__dict__,
                "cover_art": JPEG_DATA,
                "cover_art_mime": "image/jpeg",
            }
        ),
    )
    after = _audio_hash(path)

    assert after == before
