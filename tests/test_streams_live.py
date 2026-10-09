"""Tests for the live-broadcast Redis plumbing (songhive.streams.live)."""

import asyncio
import base64
import json

import pytest

from songhive.streams import live as live_mod
from songhive.streams.live import (
    delete_live_key,
    demuxer_for_mime,
    drop_ingest_header,
    encode_live_state,
    find_ingest_start,
    ingest_header_key,
    ingest_iterator,
    ingest_key,
    live_key,
    read_live_state,
    refresh_live_key,
    store_ingest_header,
    worker_heartbeat_key,
    worker_heartbeat_present,
)


def test_key_naming():
    """Live keys are scoped by output id, not the mount slug."""
    assert live_key("out-1") == "songhive:stream:live:out-1"
    assert ingest_key("out-1") == "songhive:stream:ingest:out-1"
    assert ingest_header_key("out-1") == "songhive:stream:ingest:header:out-1"
    assert worker_heartbeat_key("w1") == "songhive:stream:worker:w1"


def test_demuxer_for_mime():
    """MediaRecorder MIME types map to allowlisted ffmpeg demuxers."""
    assert demuxer_for_mime("audio/webm") == "matroska"
    assert demuxer_for_mime("audio/webm;codecs=opus") == "matroska"
    assert demuxer_for_mime("audio/ogg;codecs=opus") == "ogg"
    assert demuxer_for_mime("audio/mp4") == "mp4"
    assert demuxer_for_mime("audio/mpeg") is None
    assert demuxer_for_mime("") is None
    assert demuxer_for_mime(None) is None


@pytest.mark.asyncio
async def test_live_state_roundtrip(fake_redis):
    """encode_live_state payloads parse back into a LiveState."""
    payload = encode_live_state(
        ingest_id="ing-1",
        user_id="user-1",
        mime="audio/webm;codecs=opus",
        title="My show",
        started_at=1234.5,
    )
    await fake_redis.set(live_key("out-1"), payload)

    state = await read_live_state(fake_redis, "out-1")
    assert state is not None
    assert state.ingest_id == "ing-1"
    assert state.user_id == "user-1"
    assert state.mime == "audio/webm;codecs=opus"
    assert state.title == "My show"
    assert state.started_at == 1234.5


@pytest.mark.asyncio
async def test_read_live_state_missing_and_malformed(fake_redis):
    """Missing or malformed live keys read as no broadcast."""
    assert await read_live_state(fake_redis, "out-1") is None

    await fake_redis.set(live_key("out-1"), "not-json")
    assert await read_live_state(fake_redis, "out-1") is None

    await fake_redis.set(live_key("out-1"), json.dumps({"mime": "audio/webm"}))
    assert await read_live_state(fake_redis, "out-1") is None


@pytest.mark.asyncio
async def test_refresh_live_key_compares_payload(fake_redis):
    """Refreshing extends the TTL only while our payload still owns the key."""
    mine = encode_live_state(ingest_id="a", user_id="u", mime="audio/webm", title="")
    other = encode_live_state(ingest_id="b", user_id="u", mime="audio/webm", title="")

    # Nothing to refresh when the key does not exist.
    assert await refresh_live_key(fake_redis, "out-1", mine) is False

    await fake_redis.set(live_key("out-1"), mine, ex=live_mod.LIVE_KEY_TTL_SECONDS)
    assert await refresh_live_key(fake_redis, "out-1", mine) is True
    assert 0 < await fake_redis.ttl(live_key("out-1")) <= live_mod.LIVE_KEY_TTL_SECONDS

    # A superseded broadcaster must not refresh the new owner's claim.
    assert await refresh_live_key(fake_redis, "out-1", other) is False
    assert await fake_redis.get(live_key("out-1")) == mine


@pytest.mark.asyncio
async def test_delete_live_key_compares_payload(fake_redis):
    """Compare-and-delete only drops our own claim."""
    mine = encode_live_state(ingest_id="a", user_id="u", mime="audio/webm", title="")
    other = encode_live_state(ingest_id="b", user_id="u", mime="audio/webm", title="")

    await fake_redis.set(live_key("out-1"), mine)
    assert await delete_live_key(fake_redis, "out-1", other) is False
    assert await fake_redis.get(live_key("out-1")) == mine

    assert await delete_live_key(fake_redis, "out-1", mine) is True
    assert await fake_redis.get(live_key("out-1")) is None


@pytest.mark.asyncio
async def test_ingest_header_store_and_drop(fake_redis):
    """The cached container header is scoped to its ingest id."""
    await store_ingest_header(fake_redis, "out-1", "ing-1", b"header-bytes")
    raw = await fake_redis.get(ingest_header_key("out-1"))
    assert raw is not None
    data = json.loads(raw)
    assert data["ingest_id"] == "ing-1"
    assert base64.b64decode(data["d"]) == b"header-bytes"

    # A stale ingest id must not delete the current broadcast's header.
    await drop_ingest_header(fake_redis, "out-1", "ing-2")
    assert await fake_redis.get(ingest_header_key("out-1")) is not None

    await drop_ingest_header(fake_redis, "out-1", "ing-1")
    assert await fake_redis.get(ingest_header_key("out-1")) is None


@pytest.mark.asyncio
async def test_find_ingest_start(fake_redis):
    """find_ingest_start locates a broadcast's start entry in the stream."""
    key = ingest_key("out-1")
    await fake_redis.xadd(key, {"start": "ing-1", "mime": "audio/webm"})
    await fake_redis.xadd(key, {"d": base64.b64encode(b"aa").decode()})

    start_id = await find_ingest_start(fake_redis, "out-1", "ing-1")
    assert start_id is not None
    assert await find_ingest_start(fake_redis, "out-1", "ing-2") is None


async def _collect(iterator, limit: int = 16) -> list[bytes]:
    out: list[bytes] = []
    async for chunk in iterator:
        out.append(chunk)
        if len(out) >= limit:
            break
    return out


@pytest.mark.asyncio
async def test_ingest_iterator_yields_chunks_until_end(fake_redis):
    """The iterator relays audio chunks in order until the end marker."""
    key = ingest_key("out-1")
    start_id = await fake_redis.xadd(key, {"start": "ing-1", "mime": "audio/webm"})
    await fake_redis.xadd(key, {"d": base64.b64encode(b"one").decode()})
    await fake_redis.xadd(key, {"d": base64.b64encode(b"two").decode()})
    # An end for another (stale) ingest is ignored.
    await fake_redis.xadd(key, {"end": "ing-0"})
    await fake_redis.xadd(key, {"d": base64.b64encode(b"three").decode()})
    await fake_redis.xadd(key, {"end": "ing-1"})

    chunks = await _collect(ingest_iterator(fake_redis, "out-1", "ing-1", start_id=start_id))
    assert chunks == [b"one", b"two", b"three"]


@pytest.mark.asyncio
async def test_ingest_iterator_stops_on_new_broadcast(fake_redis):
    """A start entry for a different ingest id terminates the feed."""
    key = ingest_key("out-1")
    start_id = await fake_redis.xadd(key, {"start": "ing-1", "mime": "audio/webm"})
    await fake_redis.xadd(key, {"d": base64.b64encode(b"one").decode()})
    await fake_redis.xadd(key, {"start": "ing-2", "mime": "audio/webm"})
    await fake_redis.xadd(key, {"d": base64.b64encode(b"late").decode()})

    chunks = await _collect(ingest_iterator(fake_redis, "out-1", "ing-1", start_id=start_id))
    assert chunks == [b"one"]


@pytest.mark.asyncio
async def test_ingest_iterator_replays_header_without_start(fake_redis):
    """A late-joining worker gets the cached header, then tails new entries."""
    await store_ingest_header(fake_redis, "out-1", "ing-1", b"HEADER")
    await fake_redis.set(
        live_key("out-1"),
        encode_live_state(ingest_id="ing-1", user_id="u", mime="audio/webm", title=""),
        ex=live_mod.LIVE_KEY_TTL_SECONDS,
    )

    async def _push():
        await asyncio.sleep(0.05)
        await fake_redis.xadd(ingest_key("out-1"), {"d": base64.b64encode(b"fresh").decode()})
        await fake_redis.xadd(ingest_key("out-1"), {"end": "ing-1"})

    pusher = asyncio.create_task(_push())
    chunks = await _collect(ingest_iterator(fake_redis, "out-1", "ing-1", start_id=None))
    await pusher
    assert chunks[0] == b"HEADER"
    assert b"fresh" in chunks


@pytest.mark.asyncio
async def test_ingest_iterator_ends_when_live_key_dies(fake_redis, monkeypatch):
    """A read timeout with an absent live key ends the feed silently."""
    monkeypatch.setattr(live_mod, "LIVE_KEY_REFRESH_SECONDS", 0.05)
    key = ingest_key("out-1")
    start_id = await fake_redis.xadd(key, {"start": "ing-1", "mime": "audio/webm"})

    chunks = await _collect(ingest_iterator(fake_redis, "out-1", "ing-1", start_id=start_id))
    assert chunks == []


@pytest.mark.asyncio
async def test_worker_heartbeat_present(fake_redis):
    """The heartbeat check reflects the worker key's presence."""
    assert await worker_heartbeat_present(fake_redis) is False
    await fake_redis.set(worker_heartbeat_key("w-1"), "1", ex=10)
    assert await worker_heartbeat_present(fake_redis) is True


def test_live_config_defaults():
    """StreamsConfig exposes the live feature knobs with sane defaults."""
    from songhive.config.schema import StreamsConfig

    cfg = StreamsConfig()
    assert cfg.live_enabled is True
    assert cfg.live_max_bitrate_kbps == 320
    assert cfg.live_max_duration_seconds == 0
    assert cfg.live_ingest_max_entries == 256
    assert cfg.live_start_timeout_seconds == 15.0
