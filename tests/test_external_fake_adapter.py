"""
End-to-end tests for the fake external library adapter.
"""

import hashlib
from datetime import datetime, timezone

import pytest

from songhive.external._fake import FakeExternalAdapter
from songhive.external.errors import ExternalItemNotFound
from songhive.external.registry import register_external_adapter
from songhive.external.types import ExternalItemRef, ExternalTrackMetadata


@pytest.fixture
def config():
    return {
        "items": {
            "track1.flac": {
                "data": b"fake audio bytes for track 1",
                "metadata": {
                    "title": "Track One",
                    "artist": "Artist",
                    "album": "Album",
                    "album_artist": "Artist",
                    "track_number": 2,
                    "duration": 123.4,
                    "genre": "Rock",
                },
                "mimetype": "audio/flac",
                "mtime": datetime(2024, 1, 1, tzinfo=timezone.utc),
            },
            "track2.mp3": b"second track bytes",
        }
    }


@pytest.fixture
async def adapter(config):
    register_external_adapter("fake", FakeExternalAdapter)
    adapter = FakeExternalAdapter()
    await adapter.validate_config(config)
    return adapter


async def _read_stream(stream) -> bytes:
    assert stream.iterator is not None
    chunks = [chunk async for chunk in stream.iterator]
    return b"".join(chunks)


async def test_capabilities(adapter):
    caps = adapter.capabilities()
    assert caps.list_items
    assert caps.read_bytes
    assert caps.stream_url
    assert caps.range_read
    assert caps.download
    assert caps.compute_hash
    assert caps.read_tags
    assert caps.write_tags
    assert caps.delete_source
    assert caps.detect_changes
    assert caps.validate_config
    assert caps.limits.get("checksum_algorithm") == "sha256"


async def test_iter_items(adapter, config):
    items = [item async for item in adapter.iter_items(config)]
    assert len(items) == 2
    keys = {item.provider_key for item in items}
    assert keys == {"track1.flac", "track2.mp3"}
    track1 = next(item for item in items if item.provider_key == "track1.flac")
    assert track1.size == len(config["items"]["track1.flac"]["data"])
    assert track1.mime_type == "audio/flac"


async def test_iter_items_with_since(adapter, config):
    since = datetime(2024, 6, 1, tzinfo=timezone.utc)
    items = [item async for item in adapter.iter_items(config, since=since)]
    assert len(items) == 1
    assert items[0].provider_key == "track2.mp3"


async def test_read_metadata(adapter, config):
    item = ExternalItemRef(provider_key="track1.flac", display_path="track1.flac")
    meta = await adapter.read_metadata(config, item)
    assert meta.title == "Track One"
    assert meta.artist == "Artist"
    assert meta.album == "Album"
    assert meta.album_artist == "Artist"
    assert meta.track_number == 2
    assert meta.duration == 123.4
    assert meta.genre == "Rock"
    assert meta.raw_metadata is not None
    assert meta.raw_metadata["title"] == "Track One"


async def test_read_metadata_defaults(adapter, config):
    item = ExternalItemRef(provider_key="track2.mp3", display_path="track2.mp3")
    meta = await adapter.read_metadata(config, item)
    assert meta.title == "track2.mp3"
    assert meta.artist == ""
    assert meta.album == ""


async def test_read_metadata_not_found(adapter, config):
    item = ExternalItemRef(provider_key="missing", display_path="missing")
    with pytest.raises(ExternalItemNotFound):
        await adapter.read_metadata(config, item)


async def test_open_stream(adapter, config):
    item = ExternalItemRef(provider_key="track1.flac", display_path="track1.flac")
    stream = await adapter.open_stream(config, item)
    assert stream.kind == "iterator"
    assert stream.size == len(config["items"]["track1.flac"]["data"])
    assert stream.supports_range
    data = await _read_stream(stream)
    assert data == config["items"]["track1.flac"]["data"]


async def test_open_stream_range(adapter, config):
    item = ExternalItemRef(provider_key="track2.mp3", display_path="track2.mp3")
    stream = await adapter.open_stream(config, item, range=(1, 5))
    data = await _read_stream(stream)
    assert data == b"econd"


async def test_open_stream_url(config):
    config["prefer_url"] = True
    adapter = FakeExternalAdapter()
    await adapter.validate_config(config)
    item = ExternalItemRef(provider_key="track1.flac", display_path="track1.flac")
    stream = await adapter.open_stream(config, item)
    assert stream.kind == "url"
    assert stream.url == "https://songhive.invalid/fake/track1.flac"
    assert "X-Fake-Auth" in stream.headers


async def test_download(adapter, config):
    item = ExternalItemRef(provider_key="track1.flac", display_path="track1.flac")
    stream = await adapter.download(config, item)
    assert stream.kind == "iterator"
    data = await _read_stream(stream)
    assert data == config["items"]["track1.flac"]["data"]


async def test_compute_sha256(adapter, config):
    item = ExternalItemRef(provider_key="track1.flac", display_path="track1.flac")
    sha = await adapter.compute_sha256(config, item)
    assert sha == hashlib.sha256(config["items"]["track1.flac"]["data"]).hexdigest()


async def test_write_metadata(adapter, config):
    item = ExternalItemRef(provider_key="track1.flac", display_path="track1.flac")
    new_meta = ExternalTrackMetadata(
        title="New Title",
        artist="New Artist",
        album="New Album",
        album_artist="New Artist",
        track_number=5,
        genre="Jazz",
    )
    result = await adapter.write_metadata(config, item, new_meta)
    assert result.provider_key == "track1.flac"
    assert result.etag is not None
    assert result.mtime is not None

    updated = await adapter.read_metadata(config, item)
    assert updated.title == "New Title"
    assert updated.track_number == 5
    assert updated.genre == "Jazz"


async def test_delete_source(adapter, config):
    item = ExternalItemRef(provider_key="track1.flac", display_path="track1.flac")
    result = await adapter.delete_source(config, item)
    assert result.provider_key == "track1.flac"
    with pytest.raises(ExternalItemNotFound):
        await adapter.read_metadata(config, item)


async def test_healthcheck(adapter, config):
    health = await adapter.healthcheck(config)
    assert health.ok


async def test_rename_source_renames_item_and_preserves_sha256(adapter, config):
    """rename_source moves the in-memory item and keeps its content hash."""
    item = ExternalItemRef(provider_key="track1.flac", display_path="track1.flac")
    new_item = await adapter.rename_source(config, item, "renamed.flac")

    assert new_item.provider_key == "renamed.flac"
    assert new_item.sha256 == await adapter.compute_sha256(config, new_item)
    assert "track1.flac" not in config["items"]
    assert "renamed.flac" in config["items"]
    assert config["items"]["renamed.flac"]["metadata"]["title"] == "Track One"


async def test_rename_source_preserves_parent_path(adapter, config):
    """rename_source keeps parent directories from the original provider key."""
    config["items"]["albums/track1.flac"] = config["items"].pop("track1.flac")
    item = ExternalItemRef(provider_key="albums/track1.flac", display_path="albums/track1.flac")
    new_item = await adapter.rename_source(config, item, "renamed.flac")

    assert new_item.provider_key == "albums/renamed.flac"
    assert "albums/renamed.flac" in config["items"]


async def test_rename_source_rejects_target_collision(adapter, config):
    """rename_source fails when the target provider key already exists."""
    item = ExternalItemRef(provider_key="track1.flac", display_path="track1.flac")
    with pytest.raises(Exception):
        await adapter.rename_source(config, item, "track2.mp3")


def test_sanitize_config():
    adapter = FakeExternalAdapter()
    redacted = adapter.sanitize_config_for_response({"api_key": "secret", "items": {}})
    assert redacted["api_key"] == "<redacted>"
    assert redacted["items"] == {}
