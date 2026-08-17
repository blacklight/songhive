"""
Storage backend tests.
"""

import io

import pytest

from songhive.storage.local import LocalStorage


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
