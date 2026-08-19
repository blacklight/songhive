"""
Storage backend tests.
"""

import io

import pytest

from songhive.storage.local import LocalStorage
from songhive.storage.s3 import S3Storage


@pytest.fixture
def local_storage(tmp_path):
    """Create a local storage backend in a temp directory."""
    return LocalStorage(tmp_path / "media")


@pytest.mark.asyncio
async def test_local_store_and_retrieve(local_storage):
    """Test storing and retrieving a file."""
    content = b"fake audio data"
    file = io.BytesIO(content)

    path = await local_storage.store(file, "test/audio.mp3", "audio/mpeg")
    assert path == "test/audio.mp3"

    retrieved = await local_storage.retrieve("test/audio.mp3")
    assert retrieved is not None
    assert retrieved.read_bytes() == content


@pytest.mark.asyncio
async def test_local_exists(local_storage):
    """Test file existence check."""
    assert await local_storage.exists("nonexistent.mp3") is False

    file = io.BytesIO(b"data")
    await local_storage.store(file, "exists.mp3")
    assert await local_storage.exists("exists.mp3") is True


@pytest.mark.asyncio
async def test_local_delete(local_storage):
    """Test file deletion."""
    file = io.BytesIO(b"data")
    await local_storage.store(file, "to_delete.mp3")

    assert await local_storage.delete("to_delete.mp3") is True
    assert await local_storage.exists("to_delete.mp3") is False
    assert await local_storage.delete("nonexistent.mp3") is False


@pytest.mark.asyncio
async def test_local_url(local_storage):
    """Test that url() returns an absolute path without a CDN prefix."""
    url = await local_storage.url("test/audio.mp3")
    assert url.endswith("/test/audio.mp3")
    assert url.startswith("/")


@pytest.mark.asyncio
async def test_local_url_with_cdn(local_storage):
    """Test that url() returns a CDN-prefixed URL when cdn_prefix is set."""
    url = await local_storage.url("test/audio.mp3", cdn_prefix="https://cdn.example.com/")
    assert url == "https://cdn.example.com/test/audio.mp3"


@pytest.mark.asyncio
async def test_s3_url():
    """Test that S3 url() returns the configured endpoint URL or a fallback."""
    storage = S3Storage("my-bucket", endpoint_url="https://s3.example.com")
    url = await storage.url("test/audio.mp3")
    assert url == "https://s3.example.com/my-bucket/test/audio.mp3"

    cdn_url = await storage.url("test/audio.mp3", cdn_prefix="https://cdn.example.com/")
    assert cdn_url == "https://cdn.example.com/test/audio.mp3"


@pytest.mark.asyncio
async def test_s3_url_default():
    """Test that S3 url() falls back to the AWS-style URL when endpoint_url is absent."""
    storage = S3Storage("my-bucket", region="eu-west-1")
    url = await storage.url("test/audio.mp3")
    assert url == "https://my-bucket.s3.eu-west-1.amazonaws.com/test/audio.mp3"

    storage_no_region = S3Storage("my-bucket")
    url_no_region = await storage_no_region.url("test/audio.mp3")
    assert url_no_region == "https://my-bucket.s3.us-east-1.amazonaws.com/test/audio.mp3"


@pytest.mark.asyncio
async def test_local_store_large_streaming(local_storage):
    """Test that store streams files larger than one 64 KB chunk."""
    content = b"x" * (200 * 1024)
    file = io.BytesIO(content)

    path = await local_storage.store(file, "files/aa/bb/large.bin")
    assert path == "files/aa/bb/large.bin"

    retrieved = await local_storage.retrieve("files/aa/bb/large.bin")
    assert retrieved is not None
    assert retrieved.read_bytes() == content


@pytest.mark.asyncio
async def test_local_path_traversal(local_storage, tmp_path):
    """Test that path traversal attempts are rejected and never escape base_path."""
    bad_paths = [
        ("../evil", tmp_path / "evil"),
        (str(tmp_path / "abs_evil.bin"), tmp_path / "abs_evil.bin"),
    ]
    content = b"evil"

    for bad_path, outside_file in bad_paths:
        file = io.BytesIO(content)

        with pytest.raises(ValueError):
            await local_storage.store(file, bad_path)

        with pytest.raises(ValueError):
            await local_storage.retrieve(bad_path)

        with pytest.raises(ValueError):
            await local_storage.delete(bad_path)

        with pytest.raises(ValueError):
            await local_storage.exists(bad_path)

        with pytest.raises(ValueError):
            await local_storage.url(bad_path)

        assert not outside_file.exists()
