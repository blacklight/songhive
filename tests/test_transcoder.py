"""
Transcoder tests.
"""

import pytest

from songhive.streaming.transcoder import Transcoder, TranscoderError


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
        from pathlib import Path

        await t.transcode(Path("/tmp/test.wav"), "wav")
