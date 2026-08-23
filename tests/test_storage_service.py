"""
StorageService tests.
"""

import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from songhive.api.deps import get_storage_service
from songhive.config.schema import StorageConfig
from songhive.models._enums import Visibility
from songhive.models.stored_file import StoredFile
from songhive.services.storage import StorageService
from songhive.storage import get_storage


@pytest.fixture
def storage_service(tmp_path):
    """Create a StorageService backed by a local temp directory."""
    config = StorageConfig(backend="local", local_path=tmp_path / "media")
    backend = get_storage(config)
    return StorageService(backend, config)


@pytest.fixture
def cdn_storage_service(tmp_path):
    """Create a StorageService with a CDN prefix configured."""
    config = StorageConfig(
        backend="local",
        local_path=tmp_path / "cdn_media",
        cdn_prefix="https://cdn.example.com/",
    )
    backend = get_storage(config)
    return StorageService(backend, config)


@pytest.mark.asyncio
async def test_store_file_hashes_path_and_content_type(storage_service, db_session):
    """Test that store_file returns a StoredFile with the expected metadata."""
    content = b"hello world"
    file = io.BytesIO(content)

    stored_file = await storage_service.store_file(db_session, file, "text/plain", original_filename="hello.txt")
    expected_sha = hashlib.sha256(content).hexdigest()

    assert stored_file.sha256 == expected_sha
    assert stored_file.storage_path == (f"files/{expected_sha[:2]}/{expected_sha[2:4]}/{expected_sha}")
    assert stored_file.size == len(content)
    assert stored_file.content_type == "text/plain"
    assert stored_file.original_filename == "hello.txt"
    assert stored_file.storage_backend == "local"


@pytest.mark.asyncio
async def test_store_file_round_trip(storage_service, db_session):
    """Test that a stored file can be retrieved with identical content."""
    content = b"fake audio data"
    file = io.BytesIO(content)

    stored_file = await storage_service.store_file(db_session, file, "audio/mpeg")
    db_session.add(stored_file)
    await db_session.flush()

    local_path = await storage_service.backend.retrieve(stored_file.storage_path)
    assert local_path is not None
    assert local_path.read_bytes() == content


@pytest.mark.asyncio
async def test_store_file_dedup(storage_service, db_session):
    """Test that storing the same content twice returns the same StoredFile."""
    content = b"duplicate content"
    file1 = io.BytesIO(content)

    stored_file1 = await storage_service.store_file(db_session, file1, "text/plain", original_filename="a.txt")
    db_session.add(stored_file1)
    await db_session.flush()

    file2 = io.BytesIO(content)
    stored_file2 = await storage_service.store_file(db_session, file2, "text/plain", original_filename="b.txt")

    assert stored_file2 is stored_file1
    assert stored_file2.original_filename == "a.txt"

    # Only one backing file should exist.
    media_root = storage_service.config.local_path
    files = [p for p in media_root.rglob("*") if p.is_file()]
    assert len(files) == 1


@pytest.mark.asyncio
async def test_store_file_sets_owner_and_visibility(storage_service, db_session, regular_user):
    """store_file records owner_id and visibility on newly created files."""
    content = b"owned content"
    file = io.BytesIO(content)

    stored_file = await storage_service.store_file(
        db_session,
        file,
        "text/plain",
        original_filename="owned.txt",
        owner_id=str(regular_user.id),
        visibility=Visibility.LOCAL.value,
    )
    db_session.add(stored_file)
    await db_session.flush()

    assert stored_file.owner_id == str(regular_user.id)
    assert stored_file.visibility == Visibility.LOCAL.value


@pytest.mark.asyncio
async def test_store_file_dedup_preserves_existing_owner_and_visibility(storage_service, db_session, regular_user):
    """Storing duplicate content returns the existing file without applying new owner/visibility."""
    content = b"dedup content"
    file1 = io.BytesIO(content)

    stored_file1 = await storage_service.store_file(
        db_session,
        file1,
        "text/plain",
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(stored_file1)
    await db_session.flush()

    file2 = io.BytesIO(content)
    stored_file2 = await storage_service.store_file(
        db_session,
        file2,
        "text/plain",
        owner_id="someone-else",
        visibility=Visibility.LOCAL.value,
    )

    assert stored_file2 is stored_file1
    assert stored_file2.owner_id == str(regular_user.id)
    assert stored_file2.visibility == Visibility.PUBLIC.value


@pytest.mark.asyncio
async def test_store_file_large_streaming(storage_service, db_session):
    """Test that store_file streams files larger than a single chunk."""
    content = b"x" * (200 * 1024)
    file = io.BytesIO(content)

    stored_file = await storage_service.store_file(db_session, file, "application/octet-stream")
    db_session.add(stored_file)
    await db_session.flush()

    expected_sha = hashlib.sha256(content).hexdigest()
    assert stored_file.sha256 == expected_sha
    assert stored_file.size == len(content)

    local_path = await storage_service.backend.retrieve(stored_file.storage_path)
    assert local_path is not None
    assert local_path.read_bytes() == content


@pytest.mark.asyncio
async def test_get_url_no_cdn(storage_service, db_session):
    """Test get_url returns a stable API-relative download URL."""
    content = b"url test"
    file = io.BytesIO(content)

    stored_file = await storage_service.store_file(db_session, file, "text/plain")
    db_session.add(stored_file)
    await db_session.flush()

    url = await storage_service.get_url(stored_file)
    assert url == f"/api/v1/files/{stored_file.id}/download"


@pytest.mark.asyncio
async def test_get_url_with_cdn(cdn_storage_service, db_session):
    """Test get_url still returns an API-relative URL when a CDN is configured."""
    content = b"url test"
    file = io.BytesIO(content)

    stored_file = await cdn_storage_service.store_file(db_session, file, "text/plain")
    db_session.add(stored_file)
    await db_session.flush()
    url = await cdn_storage_service.get_url(stored_file)
    assert url == f"/api/v1/files/{stored_file.id}/download"


@pytest.mark.asyncio
async def test_delete_file(storage_service, db_session):
    """Test delete_file removes the backing file."""
    content = b"delete me"
    file = io.BytesIO(content)

    stored_file = await storage_service.store_file(db_session, file, "text/plain")
    db_session.add(stored_file)
    await db_session.flush()

    assert await storage_service.backend.exists(stored_file.storage_path) is True
    assert await storage_service.delete_file(stored_file) is True
    assert await storage_service.backend.exists(stored_file.storage_path) is False


def test_get_storage_service(tmp_path):
    """Test that get_storage_service is importable and returns a configured service."""
    config = StorageConfig(backend="local", local_path=tmp_path / "media")
    app_state = SimpleNamespace(config=SimpleNamespace(storage=config))
    app = SimpleNamespace(state=app_state)
    request = SimpleNamespace(app=app)

    service = get_storage_service(request)  # type: ignore
    assert isinstance(service, StorageService)
    assert service.config is config


def test_get_storage_service_uses_cache():
    """get_storage_service returns the cached service when config is unchanged."""
    config = StorageConfig(backend="local", local_path="/tmp/media")
    cached = StorageService(get_storage(config), config)
    app_state = SimpleNamespace(
        config=SimpleNamespace(storage=config),
        storage_service=cached,
        storage_service_config=config.model_copy(deep=True),
    )
    app = SimpleNamespace(state=app_state)
    request = SimpleNamespace(app=app)

    service = get_storage_service(request)  # type: ignore
    assert service is cached


@pytest.mark.asyncio
async def test_store_file_with_non_seekable_stream(storage_service, db_session):
    """store_file tolerates streams that do not support seek/tell."""

    class _NonSeekableStream:
        def __init__(self, data: bytes):
            self._data = data
            self._pos = 0

        def read(self, n: int = -1) -> bytes:
            if n is None or n < 0:
                chunk = self._data[self._pos :]
                self._pos = len(self._data)
                return chunk
            chunk = self._data[self._pos : self._pos + n]
            self._pos += len(chunk)
            return chunk

    content = b"unseekable content"
    stored_file = await storage_service.store_file(
        db_session,
        _NonSeekableStream(content),
        "text/plain",
    )
    assert stored_file.sha256 == hashlib.sha256(content).hexdigest()
    assert stored_file.size == len(content)


@pytest.mark.asyncio
async def test_storage_service_is_unique_constraint_error():
    """_is_unique_constraint_error checks the exception message/orig."""
    from sqlalchemy.exc import IntegrityError

    unique_exc = IntegrityError("stmt", None, Exception("UNIQUE constraint failed"))
    non_unique_exc = IntegrityError("stmt", None, Exception("FOREIGN KEY constraint failed"))
    no_orig_exc = IntegrityError("stmt", None, None)
    message_exc = IntegrityError("UNIQUE", None, None)

    assert StorageService._is_unique_constraint_error(unique_exc) is True
    assert StorageService._is_unique_constraint_error(non_unique_exc) is False
    assert StorageService._is_unique_constraint_error(no_orig_exc) is False
    assert StorageService._is_unique_constraint_error(message_exc) is True


@pytest.mark.asyncio
async def test_store_file_integrity_error_returns_existing(storage_service, db_session, monkeypatch):
    """A unique conflict during store_file falls back to the existing row."""
    from sqlalchemy.exc import IntegrityError

    content = b"duplicate race"
    file = io.BytesIO(content)
    hash_hex = hashlib.sha256(content).hexdigest()

    existing = StoredFile(
        storage_path=f"files/{hash_hex[:2]}/{hash_hex[2:4]}/{hash_hex}",
        storage_backend="local",
        content_type="text/plain",
        size=len(content),
        sha256=hash_hex,
    )
    db_session.add(existing)
    await db_session.flush()

    calls = 0

    async def _fake_scalar(stmt):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return existing

    async def _fake_flush():
        raise IntegrityError("unique", None, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(db_session, "scalar", _fake_scalar)
    monkeypatch.setattr(db_session, "flush", _fake_flush)

    returned = await storage_service.store_file(db_session, file, "text/plain")
    assert returned is existing


@pytest.mark.asyncio
async def test_store_file_non_unique_integrity_error(storage_service, db_session, monkeypatch):
    """A non-unique IntegrityError during store_file is re-raised."""
    from sqlalchemy.exc import IntegrityError

    content = b"non unique error"
    file = io.BytesIO(content)

    monkeypatch.setattr(db_session, "scalar", AsyncMock(return_value=None))
    monkeypatch.setattr(
        db_session,
        "flush",
        AsyncMock(side_effect=IntegrityError("stmt", None, Exception("NOT NULL constraint failed"))),
    )

    with pytest.raises(IntegrityError):
        await storage_service.store_file(db_session, file, "text/plain")


@pytest.mark.asyncio
async def test_store_file_integrity_error_returns_existing_with_duplicate_flag(
    storage_service, db_session, monkeypatch
):
    """A unique conflict with return_duplicate=True returns (existing, True)."""
    from sqlalchemy.exc import IntegrityError

    content = b"return duplicate flag"
    file = io.BytesIO(content)
    hash_hex = hashlib.sha256(content).hexdigest()

    existing = StoredFile(
        storage_path=f"files/{hash_hex[:2]}/{hash_hex[2:4]}/{hash_hex}",
        storage_backend="local",
        content_type="text/plain",
        size=len(content),
        sha256=hash_hex,
    )
    db_session.add(existing)
    await db_session.flush()

    monkeypatch.setattr(
        db_session,
        "scalar",
        AsyncMock(side_effect=[None, existing]),
    )
    monkeypatch.setattr(
        db_session,
        "flush",
        AsyncMock(side_effect=IntegrityError("unique", None, Exception("UNIQUE constraint failed"))),
    )

    returned, is_duplicate = await storage_service.store_file(db_session, file, "text/plain", return_duplicate=True)
    assert returned is existing
    assert is_duplicate is True


@pytest.mark.asyncio
async def test_store_file_integrity_error_raises_when_no_existing(storage_service, db_session, monkeypatch):
    """A unique conflict with no retrievable existing row is re-raised."""
    from sqlalchemy.exc import IntegrityError

    content = b"no existing row"
    file = io.BytesIO(content)

    monkeypatch.setattr(db_session, "scalar", AsyncMock(return_value=None))
    monkeypatch.setattr(
        db_session,
        "flush",
        AsyncMock(side_effect=IntegrityError("unique", None, Exception("UNIQUE constraint failed"))),
    )

    with pytest.raises(IntegrityError):
        await storage_service.store_file(db_session, file, "text/plain")
