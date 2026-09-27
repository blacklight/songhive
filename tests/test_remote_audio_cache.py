"""Tests for the provider-agnostic remote-audio remux cache."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from songhive.services.remote_audio_cache import RemoteAudioCache


@pytest.mark.asyncio
async def test_get_or_build_builds_once(tmp_path):
    cache = RemoteAudioCache(tmp_path, retention_seconds=3600, max_bytes=0)
    builds = []

    async def build(tmp):
        builds.append(tmp)
        tmp.write_bytes(b"audio")

    first = await cache.get_or_build("provider:item:quality", build)
    second = await cache.get_or_build("provider:item:quality", build)

    assert len(builds) == 1
    assert first == second
    assert first.read_bytes() == b"audio"
    assert first.suffix == ".flac"


@pytest.mark.asyncio
async def test_concurrent_builds_deduped(tmp_path):
    cache = RemoteAudioCache(tmp_path, retention_seconds=3600, max_bytes=0)
    builds = []

    async def build(tmp):
        builds.append(tmp)
        await asyncio.sleep(0.05)
        tmp.write_bytes(b"audio")

    results = await asyncio.gather(*(cache.get_or_build("same-key", build) for _ in range(4)))
    assert len(builds) == 1
    assert len({str(r) for r in results}) == 1


@pytest.mark.asyncio
async def test_failed_build_leaves_no_artifact(tmp_path):
    cache = RemoteAudioCache(tmp_path, retention_seconds=3600, max_bytes=0)

    async def build(tmp):
        tmp.write_bytes(b"partial")
        raise RuntimeError("ffmpeg exploded")

    with pytest.raises(RuntimeError):
        await cache.get_or_build("k", build)
    assert not cache.path_for("k").exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_ttl_eviction(tmp_path):
    cache = RemoteAudioCache(tmp_path, retention_seconds=10, max_bytes=0)
    old = cache.path_for("old")
    old.write_bytes(b"x")
    stale = time.time() - 3600
    os.utime(old, (stale, stale))
    fresh = cache.path_for("fresh")
    fresh.write_bytes(b"y")

    await asyncio.to_thread(cache.evict)
    assert not old.exists()
    assert fresh.exists()


@pytest.mark.asyncio
async def test_byte_cap_evicts_oldest_first(tmp_path):
    cache = RemoteAudioCache(tmp_path, retention_seconds=0, max_bytes=10)
    paths = []
    for index, key in enumerate(("a", "b", "c")):
        path = cache.path_for(key)
        path.write_bytes(b"12345")  # 5 bytes each, 15 total
        stamp = time.time() - 100 + index
        os.utime(path, (stamp, stamp))
        paths.append(path)

    await asyncio.to_thread(cache.evict)
    assert not paths[0].exists()  # oldest evicted
    assert paths[1].exists()
    assert paths[2].exists()


def test_cache_key_names_are_hashed(tmp_path):
    cache = RemoteAudioCache(tmp_path, retention_seconds=0, max_bytes=0)
    a = cache.path_for("tidal:1:LOSSLESS")
    b = cache.path_for("tidal:2:LOSSLESS")
    assert a != b
    assert a.parent == tmp_path
    assert len(a.stem) == 64
