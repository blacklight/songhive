"""
Unit tests for the ``local`` external-library adapter.
"""

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from songhive.external._local import LocalExternalAdapter
from songhive.external.errors import ExternalConfigError, ExternalItemNotFound, ExternalPermissionDenied
from songhive.external.types import ExternalItemRef, ExternalTrackMetadata
from songhive.services.metadata import AudioMetadata, AudioMetadataWrite


@pytest.fixture
def local_adapter(monkeypatch):
    """Return a fresh ``LocalExternalAdapter`` for each test."""
    return LocalExternalAdapter()


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Create a temp root and allowlist it via ``local_roots``."""
    monkeypatch.setenv("SONGHIVE_EXTERNAL_LIBRARIES__LOCAL_ROOTS", str(tmp_path))
    return tmp_path


@pytest.fixture
def base_config(tmp_root):
    """Return a minimal valid local-adapter config for the temp root."""
    return {"root": str(tmp_root), "allow_hashing": False}


def _write_file(path: Path, content: bytes = b"fake audio bytes") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@contextmanager
def _metadata_modules_patched(monkeypatch):
    """Patch metadata and hashing services so tests do not need ffmpeg/mutagen."""
    from songhive.services import metadata as metadata_module
    from songhive.services import storage as storage_module

    def _extract_metadata(file_path: Path) -> AudioMetadata:
        return AudioMetadata(
            title=f"Title {file_path.name}",
            artist="Artist",
            album="Album",
            track_number=1,
            disc_number=1,
            duration=180.0,
            genre="Rock",
            year=2024,
            mimetype="audio/mpeg",
            raw_tags={"title": [f"Title {file_path.name}"]},
        )

    _metadata = {}

    def _write_metadata(file_path: Path, meta: AudioMetadataWrite) -> None:
        _metadata[file_path] = meta

    async def _audio_hash(file_path: Path) -> str:
        return "audio-hash-" + file_path.name

    monkeypatch.setattr(metadata_module, "extract_metadata", _extract_metadata)
    monkeypatch.setattr(metadata_module, "write_metadata", _write_metadata)
    monkeypatch.setattr(storage_module, "audio_hash", _audio_hash)

    yield _metadata


@pytest.mark.asyncio
async def test_validate_config_rejects_missing_root(local_adapter, monkeypatch, tmp_path):
    """A config without a root string is rejected."""
    monkeypatch.setenv("SONGHIVE_EXTERNAL_LIBRARIES__LOCAL_ROOTS", str(tmp_path))
    with pytest.raises(ExternalConfigError, match='config\\["root"\\] is required'):
        await local_adapter.validate_config({})


@pytest.mark.asyncio
async def test_validate_config_rejects_outside_allowlist(local_adapter, monkeypatch, tmp_path):
    """A root outside the configured allowlist is rejected."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("SONGHIVE_EXTERNAL_LIBRARIES__LOCAL_ROOTS", str(allowed))

    with pytest.raises(ExternalConfigError, match="not within any configured local_roots"):
        await local_adapter.validate_config({"root": str(outside)})


@pytest.mark.asyncio
async def test_validate_config_rejects_missing_directory(local_adapter, tmp_root, base_config):
    """A non-existent root is rejected."""
    base_config["root"] = str(tmp_root / "does-not-exist")
    with pytest.raises(ExternalConfigError, match="does not exist"):
        await local_adapter.validate_config(base_config)


@pytest.mark.asyncio
async def test_validate_config_rejects_non_directory(local_adapter, tmp_root, base_config):
    """A file path as root is rejected."""
    file_path = tmp_root / "not-a-dir.txt"
    file_path.write_text("no")
    base_config["root"] = str(file_path)
    with pytest.raises(ExternalConfigError, match="not a directory"):
        await local_adapter.validate_config(base_config)


@pytest.mark.asyncio
async def test_validate_config_caps_write_tags_when_read_only(local_adapter, monkeypatch, tmp_root, base_config):
    """write_tags is False when the root is not writable."""
    base_config["allow_write_tags"] = True
    monkeypatch.setattr("os.access", lambda path, mode: mode != os.W_OK)
    caps = await local_adapter.validate_config(base_config)
    assert caps.write_tags is False
    assert caps.delete_source is False


@pytest.mark.asyncio
async def test_validate_config_allows_write_tags_when_writable(local_adapter, tmp_root, base_config):
    """write_tags is True when the root is writable and the config allows it."""
    base_config["allow_write_tags"] = True
    caps = await local_adapter.validate_config(base_config)
    assert caps.write_tags is True


@pytest.mark.asyncio
async def test_iter_items_yields_expected_files(local_adapter, tmp_root, base_config):
    """iter_items returns audio files with correct provider keys and metadata."""
    _write_file(tmp_root / "track1.mp3")
    _write_file(tmp_root / "nested" / "track2.flac")
    _write_file(tmp_root / "ignore.txt")

    await local_adapter.validate_config(base_config)
    items = [item async for item in local_adapter.iter_items(base_config)]

    assert len(items) == 2
    keys = {item.provider_key for item in items}
    assert keys == {"track1.mp3", "nested/track2.flac"}
    for item in items:
        assert item.size is not None
        assert item.etag is not None
        assert item.mtime is not None
        assert item.mime_type is not None


@pytest.mark.asyncio
async def test_iter_items_honors_extensions(local_adapter, tmp_root, base_config):
    """iter_items restricts output to the configured extensions."""
    _write_file(tmp_root / "track.mp3")
    _write_file(tmp_root / "track.flac")
    _write_file(tmp_root / "track.ogg")

    base_config["extensions"] = [".mp3"]
    await local_adapter.validate_config(base_config)
    items = [item async for item in local_adapter.iter_items(base_config)]
    assert [item.provider_key for item in items] == ["track.mp3"]


@pytest.mark.asyncio
async def test_iter_items_honors_exclude(local_adapter, tmp_root, base_config):
    """iter_items skips paths matching an exclude pattern."""
    _write_file(tmp_root / "keep.mp3")
    _write_file(tmp_root / "skip.mp3")

    base_config["exclude"] = ["skip*"]
    await local_adapter.validate_config(base_config)
    items = [item async for item in local_adapter.iter_items(base_config)]
    assert [item.provider_key for item in items] == ["keep.mp3"]


@pytest.mark.asyncio
async def test_iter_items_honors_since(local_adapter, tmp_root, base_config):
    """iter_items only returns files with mtime newer than ``since``."""
    old = tmp_root / "old.mp3"
    new = tmp_root / "new.mp3"
    _write_file(old)
    _write_file(new)

    # Back-date ``old`` so it is clearly older than the cutoff.
    old_mtime = datetime.now(timezone.utc) - timedelta(hours=1)
    os.utime(old, (old_mtime.timestamp(), old_mtime.timestamp()))

    await local_adapter.validate_config(base_config)
    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    items = [item async for item in local_adapter.iter_items(base_config, since=since)]
    assert [item.provider_key for item in items] == ["new.mp3"]


@pytest.mark.asyncio
async def test_iter_items_honors_scope(local_adapter, tmp_root, base_config):
    """iter_items returns only files under the requested scope."""
    _write_file(tmp_root / "root.mp3")
    _write_file(tmp_root / "sub" / "scoped.mp3")
    _write_file(tmp_root / "other" / "other.mp3")

    await local_adapter.validate_config(base_config)
    items = [item async for item in local_adapter.iter_items(base_config, scope="sub")]
    assert [item.provider_key for item in items] == ["sub/scoped.mp3"]


@pytest.mark.asyncio
async def test_iter_items_scope_keeps_root_relative_provider_key(local_adapter, tmp_root, base_config):
    """The provider_key stays root-relative even when a scope is supplied."""
    _write_file(tmp_root / "a" / "b" / "track.mp3")

    await local_adapter.validate_config(base_config)
    items = [item async for item in local_adapter.iter_items(base_config, scope="a/b")]
    assert items[0].provider_key == "a/b/track.mp3"


@pytest.mark.asyncio
async def test_iter_items_rejects_scope_outside_root(local_adapter, tmp_root, base_config):
    """A scope that resolves outside the library root is rejected."""
    await local_adapter.validate_config(base_config)
    with pytest.raises(ExternalConfigError, match="outside the library root"):
        [item async for item in local_adapter.iter_items(base_config, scope="../..")]


@pytest.mark.asyncio
async def test_read_metadata_maps_tags(local_adapter, monkeypatch, tmp_root, base_config):
    """read_metadata maps extracted AudioMetadata to ExternalTrackMetadata."""
    _write_file(tmp_root / "track.mp3")

    with _metadata_modules_patched(monkeypatch):
        await local_adapter.validate_config(base_config)
        meta = await local_adapter.read_metadata(
            base_config,
            ExternalItemRef(provider_key="track.mp3", display_path="track.mp3"),
        )

    assert meta.title == "Title track.mp3"
    assert meta.artist == "Artist"
    assert meta.album == "Album"
    assert meta.release_year == 2024
    assert meta.raw_metadata is not None
    assert meta.raw_metadata.get("mimetype") is not None
    assert meta.raw_metadata.get("display_path") == "track.mp3"


@pytest.mark.asyncio
async def test_read_metadata_not_found(local_adapter, tmp_root, base_config):
    """read_metadata raises ``ExternalItemNotFound`` when the file is gone."""
    await local_adapter.validate_config(base_config)
    with pytest.raises(ExternalItemNotFound):
        await local_adapter.read_metadata(
            base_config,
            ExternalItemRef(provider_key="missing.mp3", display_path="missing.mp3"),
        )


@pytest.mark.asyncio
async def test_open_stream_returns_path_stream(local_adapter, tmp_root, base_config):
    """open_stream returns a path stream with range support."""
    _write_file(tmp_root / "track.mp3")
    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3", size=16)
    stream = await local_adapter.open_stream(base_config, item)
    assert stream.kind == "path"
    assert stream.path == tmp_root / "track.mp3"
    assert stream.supports_range is True
    assert stream.temporary is False


@pytest.mark.asyncio
async def test_open_stream_not_found(local_adapter, tmp_root, base_config):
    """open_stream raises ``ExternalItemNotFound`` when the file is gone."""
    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="missing.mp3", display_path="missing.mp3")
    with pytest.raises(ExternalItemNotFound):
        await local_adapter.open_stream(base_config, item)


@pytest.mark.asyncio
async def test_compute_sha256_uses_audio_hash(local_adapter, monkeypatch, tmp_root, base_config):
    """compute_sha256 returns the ffmpeg streamhash by default."""
    _write_file(tmp_root / "track.mp3", b"audio bytes")
    with _metadata_modules_patched(monkeypatch):
        await local_adapter.validate_config(base_config)
        item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3")
        sha = await local_adapter.compute_sha256(base_config, item)
    assert sha == "audio-hash-track.mp3"


@pytest.mark.asyncio
async def test_compute_sha256_fast_hash(local_adapter, tmp_root, base_config):
    """With ``fast_hash`` set, compute_sha256 returns the raw file SHA-256."""
    content = b"audio bytes"
    _write_file(tmp_root / "track.mp3", content)
    base_config["fast_hash"] = True
    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3")
    import hashlib

    sha = await local_adapter.compute_sha256(base_config, item)
    assert sha == hashlib.sha256(content).hexdigest()


@pytest.mark.asyncio
async def test_write_metadata_writes_and_returns_mtime(local_adapter, monkeypatch, tmp_root, base_config):
    """write_metadata calls the metadata service and returns the new mtime."""
    _write_file(tmp_root / "track.mp3")
    base_config["allow_write_tags"] = True

    with _metadata_modules_patched(monkeypatch) as written:
        await local_adapter.validate_config(base_config)
        new_meta = ExternalTrackMetadata(
            title="New Title",
            artist="New Artist",
            album="New Album",
            album_artist="New Artist",
            track_number=2,
            genre="Jazz",
            release_year=2025,
        )
        item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3")
        result = await local_adapter.write_metadata(base_config, item, new_meta)

    assert result.provider_key == "track.mp3"
    assert result.mtime is not None
    assert tmp_root / "track.mp3" in written


@pytest.mark.asyncio
async def test_write_metadata_disabled_when_not_allowed(local_adapter, monkeypatch, tmp_root, base_config):
    """write_metadata raises when the capability is not enabled."""
    _write_file(tmp_root / "track.mp3")
    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3")
    with pytest.raises(Exception):
        await local_adapter.write_metadata(
            base_config,
            item,
            ExternalTrackMetadata(title="X", artist="Y", album="Z", album_artist="Y"),
        )


@pytest.mark.asyncio
async def test_delete_source_removes_file(local_adapter, monkeypatch, tmp_root, base_config):
    """delete_source removes the file when the capability is enabled."""
    _write_file(tmp_root / "track.mp3")
    base_config["allow_delete_source"] = True
    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3")
    result = await local_adapter.delete_source(base_config, item)
    assert result.provider_key == "track.mp3"
    assert not (tmp_root / "track.mp3").exists()


@pytest.mark.asyncio
async def test_delete_source_disabled_when_not_allowed(local_adapter, tmp_root, base_config):
    """delete_source raises when the capability is not enabled."""
    _write_file(tmp_root / "track.mp3")
    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3")
    with pytest.raises(Exception):
        await local_adapter.delete_source(base_config, item)


@pytest.mark.asyncio
async def test_healthcheck_ok(local_adapter, tmp_root, base_config):
    """healthcheck returns ok for a present, readable directory."""
    await local_adapter.validate_config(base_config)
    health = await local_adapter.healthcheck(base_config)
    assert health.ok is True


@pytest.mark.asyncio
async def test_healthcheck_fails_for_missing_root(local_adapter, monkeypatch, tmp_root, base_config):
    """healthcheck returns not ok when the root is missing."""
    base_config["root"] = str(tmp_root / "missing")
    health = await local_adapter.healthcheck(base_config)
    assert health.ok is False


@pytest.mark.asyncio
async def test_open_stream_rejects_symlink_escape(local_adapter, tmp_root, base_config):
    """open_stream refuses to follow a symlink that points outside the root."""
    _write_file(tmp_root / "track.mp3")
    outside = tmp_root.parent / "outside.mp3"
    outside.write_bytes(b"outside audio")
    (tmp_root / "track.mp3").unlink()
    (tmp_root / "track.mp3").symlink_to(outside)

    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3", size=7)
    with pytest.raises(ExternalPermissionDenied):
        await local_adapter.open_stream(base_config, item)


@pytest.mark.asyncio
async def test_read_metadata_rejects_symlink_escape(local_adapter, tmp_root, base_config):
    """read_metadata refuses to follow a symlink that points outside the root."""
    _write_file(tmp_root / "track.mp3")
    outside = tmp_root.parent / "outside.mp3"
    outside.write_bytes(b"outside audio")
    (tmp_root / "track.mp3").unlink()
    (tmp_root / "track.mp3").symlink_to(outside)

    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3")
    with pytest.raises(ExternalPermissionDenied):
        await local_adapter.read_metadata(base_config, item)


@pytest.mark.asyncio
async def test_compute_sha256_rejects_symlink_escape(local_adapter, tmp_root, base_config):
    """compute_sha256 refuses to follow a symlink that points outside the root."""
    _write_file(tmp_root / "track.mp3")
    outside = tmp_root.parent / "outside.mp3"
    outside.write_bytes(b"outside audio")
    (tmp_root / "track.mp3").unlink()
    (tmp_root / "track.mp3").symlink_to(outside)

    base_config["fast_hash"] = True
    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3")
    with pytest.raises(ExternalPermissionDenied):
        await local_adapter.compute_sha256(base_config, item)


@pytest.mark.asyncio
async def test_delete_source_rejects_symlink_escape(local_adapter, tmp_root, base_config):
    """delete_source refuses to unlink a symlink that points outside the root."""
    _write_file(tmp_root / "track.mp3")
    outside = tmp_root.parent / "outside.mp3"
    outside.write_bytes(b"outside audio")
    (tmp_root / "track.mp3").unlink()
    (tmp_root / "track.mp3").symlink_to(outside)

    base_config["allow_delete_source"] = True
    await local_adapter.validate_config(base_config)
    item = ExternalItemRef(provider_key="track.mp3", display_path="track.mp3")
    with pytest.raises(ExternalPermissionDenied):
        await local_adapter.delete_source(base_config, item)
    assert outside.exists()


@pytest.mark.asyncio
async def test_delete_source_disabled_when_root_read_only(local_adapter, tmp_root, base_config, monkeypatch):
    """delete_source capability is False when the root is not writable."""
    base_config["allow_delete_source"] = True
    monkeypatch.setattr("os.access", lambda path, mode: mode != os.W_OK)
    caps = await local_adapter.validate_config(base_config)
    assert caps.delete_source is False
