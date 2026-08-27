"""
Tests for the audio-only hash helper.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from songhive.services.storage import audio_hash

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is required for these tests",
)


def _generate_audio(path: Path, frequency: int = 1000) -> None:
    """Generate a short sine-wave audio file in a supported format."""
    codec = {
        ".mp3": "libmp3lame",
        ".flac": "flac",
        ".ogg": "libvorbis",
        ".m4a": "aac",
    }[path.suffix]
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration=0.5",
            "-c:a",
            codec,
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _tag_copy(src: Path, dst: Path, **metadata: str) -> None:
    """Copy an audio file with new container metadata and no re-encoding."""
    cmd = ["ffmpeg", "-y", "-i", str(src), "-c", "copy"]
    for key, value in metadata.items():
        cmd.extend(["-metadata", f"{key}={value}"])
    cmd.append(str(dst))
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.mark.parametrize("ext", [".mp3", ".flac", ".ogg", ".m4a"])
@pytest.mark.asyncio
async def test_audio_hash_is_tag_invariant(ext: str, tmp_path: Path) -> None:
    """The same audio content with different tags yields the same hash."""
    base = tmp_path / f"base{ext}"
    tagged_a = tmp_path / f"tagged_a{ext}"
    tagged_b = tmp_path / f"tagged_b{ext}"

    _generate_audio(base)
    _tag_copy(base, tagged_a, title="First Title", artist="First Artist")
    _tag_copy(base, tagged_b, title="Second Title", artist="Second Artist", genre="Test")

    base_hash = await audio_hash(base)
    tagged_a_hash = await audio_hash(tagged_a)
    tagged_b_hash = await audio_hash(tagged_b)

    assert len(base_hash) == 64
    assert tagged_a_hash == base_hash
    assert tagged_b_hash == base_hash


@pytest.mark.parametrize("ext", [".mp3", ".flac", ".ogg", ".m4a"])
@pytest.mark.asyncio
async def test_audio_hash_differs_for_different_audio(ext: str, tmp_path: Path) -> None:
    """Different audio content yields a different hash."""
    first = tmp_path / f"first{ext}"
    second = tmp_path / f"second{ext}"

    _generate_audio(first, frequency=1000)
    _generate_audio(second, frequency=1200)

    first_hash = await audio_hash(first)
    second_hash = await audio_hash(second)

    assert first_hash != second_hash


@pytest.mark.asyncio
async def test_audio_hash_raises_on_ffmpeg_error(tmp_path: Path) -> None:
    """A non-zero ffmpeg exit code raises RuntimeError with the stderr message."""
    bad_file = tmp_path / "not_audio.txt"
    bad_file.write_text("this is not audio")

    with pytest.raises(RuntimeError, match="ffmpeg failed with return code") as exc_info:
        await audio_hash(bad_file)

    message = str(exc_info.value)
    assert "return code" in message
    assert len(message) > len("ffmpeg failed with return code ")
