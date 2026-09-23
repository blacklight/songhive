import asyncio
from pathlib import Path

import pytest

from songhive.streams.icecast import IcecastDriver, IcecastOutput
from songhive.streams.types import AudioSource, TrackMeta


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
    argv = driver._decoder_argv(
        AudioSource(kind="path", path=Path("/tmp/track.flac")),
        position=12.0,
    )
    assert argv[0] == "ffmpeg"
    assert "-re" in argv
    assert argv.index("-re") < argv.index("-ss")
    assert "-ss" in argv
    assert "12.0" in argv
    assert "/tmp/track.flac" in argv
    assert "pipe:1" in argv


@pytest.mark.asyncio
async def test_icecast_silence_argv_is_realtime():
    provider = IcecastOutput()
    config = _valid_config()
    await provider.validate_config(config)
    driver = IcecastDriver(config)
    argv = driver._silence_argv()
    # Without -re, anullsrc floods the encoder far faster than realtime and
    # Icecast drops listeners that fall behind.
    assert "-re" in argv
    assert argv.index("-re") < argv.index("-f")
    assert "lavfi" in argv
    assert any(arg.startswith("anullsrc=") for arg in argv)
    assert "pipe:1" in argv


class _FakeStream:
    async def read(self, _n: int = -1) -> bytes:
        return b""


class _FakeDecoderProc:
    def __init__(self) -> None:
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.stdin = None
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = 0


class _FakeEncoderProc:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.stdin = _FakeStream()
        self.stdout = None
        self.stderr = None

    def kill(self) -> None:
        self.returncode = 0


@pytest.mark.asyncio
async def test_icecast_decoder_no_source_ended_when_encoder_dead(monkeypatch):
    provider = IcecastOutput()
    config = _valid_config()
    await provider.validate_config(config)
    driver = IcecastDriver(config)
    driver._encoder = _FakeEncoderProc(returncode=1)
    driver._task_count = 1

    async def fake_exec(*_args, **_kwargs):
        return _FakeDecoderProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await driver._run_decoder(AudioSource(kind="path", path=Path("/tmp/track.flac")), 0.0, 1)
    assert driver.events.empty()


@pytest.mark.asyncio
async def test_icecast_decoder_argv_includes_volume_filter():
    provider = IcecastOutput()
    config = _valid_config()
    await provider.validate_config(config)
    driver = IcecastDriver(config)
    argv = driver._decoder_argv(
        AudioSource(kind="path", path=Path("/tmp/track.flac")),
        position=0.0,
    )
    assert argv.index("-af") < argv.index("-f")
    assert argv[argv.index("-af") + 1] == "volume=1.0"


@pytest.mark.asyncio
async def test_icecast_set_volume_restarts_decoder_with_new_gain(monkeypatch):
    """set_volume while playing restarts the decoder with the new gain."""
    provider = IcecastOutput()
    config = _valid_config()
    await provider.validate_config(config)
    driver = IcecastDriver(config)
    driver._encoder = _FakeEncoderProc()

    argvs: list[list[str]] = []

    async def fake_exec(*args, **_kwargs):
        argvs.append([str(a) for a in args])
        return _FakeDecoderProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    source = AudioSource(kind="path", path=Path("/tmp/track.flac"))
    meta = TrackMeta(track_id="t1", title="t", artist="a")
    await driver.set_source(source, position=5.0, metadata=meta)
    assert driver._decoder_task is not None
    await asyncio.sleep(0)

    await driver.set_volume(0.25)
    await asyncio.sleep(0)

    assert driver._volume == pytest.approx(0.25)
    decoder_argvs = [a for a in argvs if "pipe:1" in a]
    assert len(decoder_argvs) >= 2
    assert f"volume={0.25}" in decoder_argvs[-1]


@pytest.mark.asyncio
async def test_icecast_set_volume_while_paused_only_records(monkeypatch):
    """set_volume while paused stores the gain without touching the silence."""
    provider = IcecastOutput()
    config = _valid_config()
    await provider.validate_config(config)
    driver = IcecastDriver(config)
    driver._paused = True
    driver._current_source = AudioSource(kind="path", path=Path("/tmp/track.flac"))

    async def fake_exec(*_args, **_kwargs):
        return _FakeDecoderProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await driver.set_volume(0.5)

    assert driver._volume == pytest.approx(0.5)
    # No new decoder task was spawned for the volume change alone.
    assert driver._decoder_task is None
    # The next resume builds its argv with the new gain.
    argv = driver._decoder_argv(driver._current_source, position=0.0)
    assert argv[argv.index("-af") + 1] == "volume=0.5"
