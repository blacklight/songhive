import pytest

from songhive.streams.icecast import IcecastDriver, IcecastOutput


def _valid_config() -> dict:
    return {
        "host": "example.com",
        "port": 8000,
        "mount": "/stream",
        "username": "source",
        "password": "secret",
        "protocol": "http",
        "format": "mp3",
        "bitrate": "128k",
        "sample_rate": 44100,
        "name": "Songhive",
        "description": "",
        "genre": "",
        "public": False,
    }


@pytest.mark.asyncio
async def test_icecast_validate_config_returns_capabilities():
    provider = IcecastOutput()
    caps = await provider.validate_config(_valid_config())
    assert caps.pause_supported is True
    assert caps.seek_supported is True
    assert caps.metadata_updates is False


@pytest.mark.asyncio
async def test_icecast_password_redacted_in_response():
    provider = IcecastOutput()
    config = _valid_config()
    await provider.validate_config(config)
    sanitized = provider.sanitize_config_for_response(config)
    assert sanitized["password"] == "<redacted>"
    assert sanitized["host"] == "example.com"


@pytest.mark.asyncio
async def test_icecast_defaults_missing_username():
    provider = IcecastOutput()
    config = _valid_config()
    del config["username"]
    await provider.validate_config(config)
    assert config["username"] == "source"


@pytest.mark.asyncio
async def test_icecast_mount_point_normalized():
    provider = IcecastOutput()
    config = _valid_config()
    config["mount"] = "stream"
    await provider.validate_config(config)
    assert config["mount"] == "/stream"


@pytest.mark.asyncio
async def test_icecast_invalid_format():
    provider = IcecastOutput()
    config = _valid_config()
    config["format"] = "flac"
    with pytest.raises(ValueError, match="Unsupported format"):
        await provider.validate_config(config)


@pytest.mark.asyncio
async def test_icecast_encoder_argv():
    provider = IcecastOutput()
    config = _valid_config()
    await provider.validate_config(config)
    driver = IcecastDriver(config)
    argv = driver._encoder_argv()
    assert argv[0] == "ffmpeg"
    assert "-f" in argv
    assert "s16le" in argv
    assert "-ac" in argv
    assert "2" in argv
    assert "pipe:0" in argv
    assert "-c:a" in argv
    assert "libmp3lame" in argv
    assert "-b:a" in argv
    assert "128k" in argv
    assert "-content_type" in argv
    assert "audio/mpeg" in argv
    assert "icecast://source:secret@example.com:8000/stream" in argv


@pytest.mark.asyncio
async def test_icecast_decoder_argv():
    provider = IcecastOutput()
    config = _valid_config()
    await provider.validate_config(config)
    driver = IcecastDriver(config)
    from pathlib import Path

    from songhive.streams.types import AudioSource

    argv = driver._decoder_argv(
        AudioSource(kind="path", path=Path("/tmp/track.flac")),
        position=12.0,
    )
    assert argv[0] == "ffmpeg"
    assert "-ss" in argv
    assert "12.0" in argv
    assert "/tmp/track.flac" in argv
    assert "pipe:1" in argv
