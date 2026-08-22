"""
Transcoder tests.
"""

import array
import asyncio
import math
import shutil
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from songhive.streaming.transcoder import Transcoder, TranscoderError


def _create_wav(path: Path, duration: float = 0.5, sample_rate: int = 8000) -> None:
    """Write a mono 16-bit PCM WAV file of the given duration."""
    samples = int(duration * sample_rate)
    data = array.array(
        "h",
        (int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(samples)),
    )
    with wave.open(str(path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(data.tobytes())


def test_transcoder_format_map():
    """Test that the transcoder supports expected formats."""
    t = Transcoder()
    assert "mp3" in t.FORMAT_MAP
    assert "ogg" in t.FORMAT_MAP
    assert "flac" in t.FORMAT_MAP
    assert "aac" in t.FORMAT_MAP
    assert "opus" in t.FORMAT_MAP


@pytest.mark.asyncio
async def test_transcode_unsupported_format():
    """Test that unsupported format raises an error."""
    t = Transcoder()
    with pytest.raises(TranscoderError, match="Unsupported format"):
        await t.transcode(Path("/tmp/test.wav"), "wav")


@pytest.mark.asyncio
async def test_stream_unsupported_format():
    """stream() raises TranscoderError for an unsupported format before spawning."""
    t = Transcoder()
    with pytest.raises(TranscoderError, match="Unsupported format"):
        async for _ in t.stream(Path("/tmp/test.wav"), "wav"):
            pass


@pytest.mark.asyncio
async def test_stream_missing_file():
    """stream() raises TranscoderError when the input file does not exist."""
    t = Transcoder()
    with pytest.raises(TranscoderError, match="does not exist"):
        async for _ in t.stream(Path("/tmp/nonexistent-audio.wav"), "opus"):
            pass


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
@pytest.mark.asyncio
async def test_stream_transcodes_wav_to_opus(tmp_path):
    """stream() yields non-empty chunks when transcoding a WAV file to Opus."""
    wav = tmp_path / "in.wav"
    _create_wav(wav, duration=0.5)

    t = Transcoder()
    chunks = []
    async for chunk in t.stream(wav, "opus", "128k"):
        if chunk:
            chunks.append(chunk)

    assert chunks


@pytest.mark.asyncio
async def test_stream_ffmpeg_error():
    """A non-zero ffmpeg exit raises TranscoderError with stderr."""
    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.stdout = MagicMock()
    fake_proc.stdout.read = AsyncMock(side_effect=[b"abc", b""])
    fake_proc.stderr = MagicMock()
    fake_proc.stderr.read = AsyncMock(return_value=b"ffmpeg failed")
    fake_proc.wait = AsyncMock(return_value=None)

    with patch(
        "songhive.streaming.transcoder.asyncio.create_subprocess_exec",
        return_value=fake_proc,
    ):
        t = Transcoder()
        with pytest.raises(TranscoderError, match="ffmpeg exited with code 1"):
            async for _ in t.stream(Path(__file__), "opus"):
                pass

    fake_proc.wait.assert_called_once()


@pytest.mark.asyncio
async def test_stream_cancellation_kills_process():
    """Cancelling the consumer terminates the ffmpeg subprocess."""
    fake_proc = MagicMock()
    fake_proc.returncode = None
    fake_proc.stdout = MagicMock()
    fake_proc.stdout.read = AsyncMock(side_effect=[b"abc", asyncio.Future()])
    fake_proc.stderr = MagicMock()
    fake_proc.wait = AsyncMock(return_value=None)
    fake_proc.kill = MagicMock()

    with patch(
        "songhive.streaming.transcoder.asyncio.create_subprocess_exec",
        return_value=fake_proc,
    ):
        t = Transcoder()
        gen = t.stream(Path(__file__), "opus")
        first = await anext(gen)
        assert first == b"abc"
        await gen.aclose()

    fake_proc.kill.assert_called_once()
    fake_proc.wait.assert_called()
