"""Tests for the native HTTP stream provider and driver."""

import asyncio
import base64
import json
from pathlib import Path

import pytest

from songhive.streams.http import (
    HttpStreamDriver,
    HttpStreamOutput,
    stream_data_key,
    stream_listener_key,
    stream_meta_key,
)
from songhive.streams.types import AudioSource, TrackMeta


def _valid_config() -> dict:
    return {
        "mount": "radio",
        "format": "mp3",
        "bitrate": "128k",
        "sample_rate": 44100,
        "name": "Songhive",
        "description": "",
        "genre": "",
    }


@pytest.mark.asyncio
async def test_http_validate_config_returns_capabilities():
    provider = HttpStreamOutput()
    caps = await provider.validate_config(_valid_config())
    assert caps.pause_supported is True
    assert caps.seek_supported is True
    assert caps.multi_listener is True
    assert caps.metadata_updates is False


@pytest.mark.asyncio
async def test_http_mount_normalized_to_slug():
    provider = HttpStreamOutput()
    config = _valid_config()
    config["mount"] = "/my-stream/"
    await provider.validate_config(config)
    assert config["mount"] == "my-stream"


@pytest.mark.asyncio
async def test_http_mount_rejects_invalid_slugs():
    provider = HttpStreamOutput()
    for bad in ("", "a/b", "../etc", "mount with spaces", "-leading-dash", "x" * 65):
        config = _valid_config()
        config["mount"] = bad
        with pytest.raises(ValueError):
            await provider.validate_config(config)


@pytest.mark.asyncio
async def test_http_invalid_format():
    provider = HttpStreamOutput()
    config = _valid_config()
    config["format"] = "flac"
    with pytest.raises(ValueError, match="Unsupported format"):
        await provider.validate_config(config)


@pytest.mark.asyncio
async def test_http_listen_token_redacted():
    provider = HttpStreamOutput()
    config = _valid_config()
    config["listen_token"] = "s3cret"
    await provider.validate_config(config)
    sanitized = provider.sanitize_config_for_response(config)
    assert sanitized["listen_token"] == "<redacted>"
    assert sanitized["mount"] == "radio"


@pytest.mark.asyncio
async def test_http_encoder_argv_outputs_to_stdout():
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    driver = HttpStreamDriver(config)
    argv = driver._encoder_argv()
    assert argv[0] == "ffmpeg"
    assert "pipe:0" in argv
    assert "pipe:1" in argv
    assert "libmp3lame" in argv
    assert "128k" in argv
    assert "mp3" in argv
    # No Icecast URL or ICY connect-time options leak into the argv.
    assert not any("icecast://" in arg for arg in argv)
    assert not any(arg.startswith("-ice_") for arg in argv)


@pytest.mark.asyncio
async def test_http_encoder_argv_honours_ffmpeg_path():
    provider = HttpStreamOutput()
    config = _valid_config()
    config["ffmpeg_path"] = "/opt/bin/ffmpeg"
    await provider.validate_config(config)
    driver = HttpStreamDriver(config)
    assert driver._encoder_argv()[0] == "/opt/bin/ffmpeg"
    decoder_argv = driver._decoder_argv(
        AudioSource(kind="path", path=Path("/tmp/t.flac")),
        position=0.0,
    )
    assert decoder_argv[0] == "/opt/bin/ffmpeg"


class _FakeStream:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self._chunks = list(chunks or [])
        self.writes: list[bytes] = []

    async def read(self, _n: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None


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
        self.returncode = -9


class _FakeEncoderProc:
    def __init__(self, chunks: list[bytes]) -> None:
        self.returncode: int | None = None
        self.stdin = _FakeStream()
        self.stdout = _FakeStream(chunks)
        self.stderr = _FakeStream()

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.005)
        return self.returncode


def _patch_exec(monkeypatch, encoder: _FakeEncoderProc):
    async def fake_exec(*args, **_kwargs):
        if "pipe:0" in args:
            return encoder
        return _FakeDecoderProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


@pytest.mark.asyncio
async def test_http_driver_requires_redis():
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    driver = HttpStreamDriver(config)
    with pytest.raises(RuntimeError, match="_redis"):
        await driver.start()


@pytest.mark.asyncio
async def test_http_driver_publishes_audio_and_meta(monkeypatch, fake_redis):
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    encoder = _FakeEncoderProc([b"chunk-one", b"chunk-two"])
    _patch_exec(monkeypatch, encoder)

    await driver.start()
    try:
        for _ in range(100):
            entries = await fake_redis.xrange(stream_data_key("radio"))
            if len([e for e in entries if "d" in e[1]]) >= 2:
                break
            await asyncio.sleep(0.01)

        entries = await fake_redis.xrange(stream_data_key("radio"))
        payloads = [base64.b64decode(fields["d"]) for _id, fields in entries if fields.get("d")]
        assert b"chunk-one" in payloads
        assert b"chunk-two" in payloads

        meta_raw = await fake_redis.get(stream_meta_key("radio"))
        assert meta_raw is not None
        meta = json.loads(meta_raw)
        assert meta["content_type"] == "audio/mpeg"
        assert meta["name"] == "Songhive"
    finally:
        await driver.stop()

    entries = await fake_redis.xrange(stream_data_key("radio"))
    assert entries[-1][1].get("end") == "1"
    assert await fake_redis.get(stream_meta_key("radio")) is None


@pytest.mark.asyncio
async def test_http_driver_listener_count(monkeypatch, fake_redis):
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    assert await driver.listener_count() == 0
    await fake_redis.set(stream_listener_key("radio", "a"), "1", ex=30)
    await fake_redis.set(stream_listener_key("radio", "b"), "1", ex=30)
    # The driver caches the count for a few seconds like the Icecast driver.
    driver._listener_count_at = 0.0
    assert await driver.listener_count() == 2


@pytest.mark.asyncio
async def test_http_driver_pause_resume_via_silence(monkeypatch, fake_redis):
    """The inherited pause machinery works against the stdout encoder."""
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    encoder = _FakeEncoderProc([b""])
    _patch_exec(monkeypatch, encoder)
    await driver.start()
    try:
        # The pipeline starts feeding silence until the first source arrives.
        assert driver.is_paused is True
        await driver.set_source(
            AudioSource(kind="path", path=Path("/tmp/t.flac")),
            position=0.0,
            metadata=TrackMeta(track_id="t1", title="T", artist="A"),
        )
        assert driver.is_paused is False
        await driver.pause()
        assert driver.is_paused is True
    finally:
        await driver.stop()
