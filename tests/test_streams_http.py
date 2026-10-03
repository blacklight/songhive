"""Tests for the native HTTP stream provider and driver."""

import asyncio
import base64
import json
from pathlib import Path
from typing import Optional

import pytest

from songhive.streams.http import (
    HttpStreamDriver,
    HttpStreamOutput,
    stream_data_key,
    stream_listener_key,
    stream_meta_key,
)
from songhive.streams.types import AudioSource, TrackMeta
from songhive.ws.events import WS_EVENTS_CHANNEL


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
    assert caps.metadata_updates is True


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


def _patch_exec_seq(monkeypatch, encoders: list[_FakeEncoderProc]):
    """Like ``_patch_exec`` but each encoder spawn gets the next proc."""
    procs = list(encoders)

    async def fake_exec(*args, **_kwargs):
        if "pipe:0" in args:
            return procs.pop(0)
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


@pytest.mark.asyncio
async def test_http_driver_publishes_now_playing(fake_redis):
    """update_metadata appends an ``m`` entry and refreshes the meta blob."""
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    await driver.update_metadata(TrackMeta(track_id="t1", title="Song", artist="Artist", album="LP"))

    entries = await fake_redis.xrange(stream_data_key("radio"))
    meta_entries = [fields for _id, fields in entries if fields.get("m")]
    assert len(meta_entries) == 1
    payload = json.loads(meta_entries[0]["m"])
    assert payload["song"] == "Artist - Song"
    assert payload["album"] == "LP"

    meta = json.loads(await fake_redis.get(stream_meta_key("radio")))
    assert meta["song"] == "Artist - Song"
    assert meta["title"] == "Song"
    assert meta["artist"] == "Artist"
    assert meta["album"] == "LP"

    # Identical metadata is deduplicated; a track change publishes again.
    await driver.update_metadata(TrackMeta(track_id="t1", title="Song", artist="Artist", album="LP"))
    await driver.update_metadata(TrackMeta(track_id="t2", title="Next", artist="Artist"))
    entries = await fake_redis.xrange(stream_data_key("radio"))
    meta_entries = [json.loads(fields["m"]) for _id, fields in entries if fields.get("m")]
    assert [m["song"] for m in meta_entries] == ["Artist - Song", "Artist - Next"]


@pytest.mark.asyncio
async def test_http_driver_meta_refresh_survives_dead_air(monkeypatch, fake_redis):
    """The liveness key is refreshed by a timer, not by chunk production.

    When the encoder is starved (e.g. a long track transition) the publish
    pump goes quiet; the meta key must stay alive regardless or every
    listener gets disconnected at the next read timeout.
    """
    monkeypatch.setattr("songhive.streams.http._META_REFRESH_INTERVAL", 0.05)
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    encoder = _FakeEncoderProc([b"chunk", b""])
    _patch_exec(monkeypatch, encoder)

    refreshes = 0
    original = driver._publish_meta

    async def _counting_publish_meta():
        nonlocal refreshes
        refreshes += 1
        await original()

    await driver.start()
    try:
        monkeypatch.setattr(driver, "_publish_meta", _counting_publish_meta)
        # The encoder exhausts its chunks immediately; from here on the
        # publish pump is idle and only the timer can refresh the key.
        await asyncio.sleep(0.4)
        assert refreshes >= 3
        assert await fake_redis.get(stream_meta_key("radio")) is not None
    finally:
        await driver.stop()


@pytest.mark.asyncio
async def test_http_driver_stop_cancels_meta_refresh(monkeypatch, fake_redis):
    """stop() cancels the refresh task and deletes the liveness key."""
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    encoder = _FakeEncoderProc([b"chunk"])
    _patch_exec(monkeypatch, encoder)

    await driver.start()
    assert driver._meta_task is not None
    await driver.stop()
    assert driver._meta_task is None
    assert await fake_redis.get(stream_meta_key("radio")) is None


@pytest.mark.asyncio
async def test_http_driver_encoder_restart_signals_end(monkeypatch, fake_redis):
    """An encoder restart appends ``end`` so listeners reconnect.

    A fresh encoder writes a brand-new container stream; splicing it into
    the old response breaks players (chained Ogg), so the driver signals
    listeners to reconnect for a clean stream instead.
    """
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    first = _FakeEncoderProc([b"first-chunk"])
    second = _FakeEncoderProc([b"second-chunk"])
    _patch_exec_seq(monkeypatch, [first, second])

    await driver.start()
    try:
        for _ in range(100):
            entries = await fake_redis.xrange(stream_data_key("radio"))
            if any(base64.b64decode(fields["d"]) == b"first-chunk" for _id, fields in entries if fields.get("d")):
                break
            await asyncio.sleep(0.01)

        # Kill the encoder: the watcher restarts the pipeline.
        first.returncode = 1

        for _ in range(300):
            entries = await fake_redis.xrange(stream_data_key("radio"))
            if any(fields.get("end") for _id, fields in entries):
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("no end sentinel written on encoder restart")

        # The replacement encoder's audio resumes after the sentinel.
        for _ in range(300):
            entries = await fake_redis.xrange(stream_data_key("radio"))
            payloads = [base64.b64decode(fields["d"]) for _id, fields in entries if fields.get("d")]
            if b"second-chunk" in payloads:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("restarted encoder never published audio")

        end_index = next(i for i, (_id, fields) in enumerate(entries) if fields.get("end"))
        audio_after_end = [base64.b64decode(fields["d"]) for _id, fields in entries[end_index + 1 :] if fields.get("d")]
        assert audio_after_end == [b"second-chunk"]
    finally:
        await driver.stop()


async def _read_ws_envelope(pubsub, attempts: int = 50) -> Optional[dict]:
    """Read the next envelope off the WS pub/sub channel, or None."""
    for _ in range(attempts):
        message = await pubsub.get_message(ignore_subscribe_messages=True)
        if message:
            return json.loads(message["data"])
        await asyncio.sleep(0.01)
    return None


@pytest.mark.asyncio
async def test_http_driver_broadcasts_stream_update(fake_redis):
    """update_metadata fans out a ``stream_update`` event on the WS channel."""
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(WS_EVENTS_CHANNEL)
    try:
        await driver.update_metadata(TrackMeta(track_id="t1", title="Song", artist="Artist", album="LP"))

        envelope = await _read_ws_envelope(pubsub)
        assert envelope is not None
        assert envelope["kind"] == "broadcast"
        assert envelope["type"] == "stream_update"
        assert envelope["topic"] == "streams"
        data = envelope["data"]
        assert data["mount"] == "radio"
        assert data["online"] is True
        assert data["now_playing"] == {
            "track_id": "t1",
            "title": "Song",
            "artist": "Artist",
            "album": "LP",
        }

        # An unchanged song is deduplicated and emits no second event.
        await driver.update_metadata(TrackMeta(track_id="t1", title="Song", artist="Artist", album="LP"))
        assert await _read_ws_envelope(pubsub) is None
    finally:
        await pubsub.aclose()


@pytest.mark.asyncio
async def test_http_driver_stop_broadcasts_offline(fake_redis):
    """stop() tells the directory page the mount went offline."""
    provider = HttpStreamOutput()
    config = _valid_config()
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(WS_EVENTS_CHANNEL)
    try:
        await driver.stop()

        envelope = await _read_ws_envelope(pubsub)
        assert envelope is not None
        assert envelope["type"] == "stream_update"
        assert envelope["data"]["mount"] == "radio"
        assert envelope["data"]["online"] is False
        assert envelope["data"]["now_playing"] is None
    finally:
        await pubsub.aclose()


@pytest.mark.asyncio
async def test_http_driver_private_mount_targets_owner(fake_redis):
    """listen_token mounts deliver ``stream_update`` only to the owner."""
    provider = HttpStreamOutput()
    config = _valid_config()
    config["listen_token"] = "s3cret"
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    config["_owner_user_id"] = "owner-1"
    driver = HttpStreamDriver(config)

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(WS_EVENTS_CHANNEL)
    try:
        await driver.update_metadata(TrackMeta(track_id="t1", title="Song", artist="Artist"))

        envelope = await _read_ws_envelope(pubsub)
        assert envelope is not None
        assert envelope["kind"] == "user"
        assert envelope["user_id"] == "owner-1"
        assert envelope["type"] == "stream_update"
        assert envelope["data"]["mount"] == "radio"
    finally:
        await pubsub.aclose()


@pytest.mark.asyncio
async def test_http_driver_private_mount_without_owner_stays_silent(fake_redis):
    """A private mount with no known owner must not leak into broadcasts."""
    provider = HttpStreamOutput()
    config = _valid_config()
    config["listen_token"] = "s3cret"
    await provider.validate_config(config)
    config["_redis"] = fake_redis
    driver = HttpStreamDriver(config)

    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(WS_EVENTS_CHANNEL)
    try:
        await driver.update_metadata(TrackMeta(track_id="t1", title="Song", artist="Artist"))
        assert await _read_ws_envelope(pubsub) is None
    finally:
        await pubsub.aclose()
