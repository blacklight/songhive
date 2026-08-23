"""
Storage backend tests.
"""

import io
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from songhive.config.schema import StorageConfig
from songhive.storage import get_storage
from songhive.storage.exc import FileSizeLimitExceededError
from songhive.storage.local import LocalStorage
from songhive.storage.s3 import S3Storage


class _FakeClientContext:
    """Async context manager that yields a pre-built client."""

    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_, **__):
        return None


class _NonSeekableStream:
    """File-like stream without tell/seek, to exercise the unknown-size path."""

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


class _FakeBody:
    """In-memory stand-in for an aiobotocore StreamingBody."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_, **__):
        return None

    async def read(self, amt: int = -1) -> bytes:
        if amt is None or amt < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + amt]
        self._pos += len(chunk)
        return chunk

    async def iter_chunks(self, chunk_size: int = 64 * 1024):
        while self._pos < len(self._data):
            chunk = self._data[self._pos : self._pos + chunk_size]
            self._pos += len(chunk)
            if not chunk:
                break
            yield chunk


class _FakeNoSuchKey(ClientError):
    """Local stand-in for S3 NoSuchKey service exceptions."""


class _FakeNoSuchBucket(ClientError):
    """Local stand-in for S3 NoSuchBucket service exceptions."""


class _FakeClientExceptions:
    NoSuchKey = _FakeNoSuchKey
    NoSuchBucket = _FakeNoSuchBucket


@pytest.fixture
def local_storage(tmp_path):
    """Create a local storage backend in a temp directory."""
    return LocalStorage(tmp_path / "media")


@pytest.fixture
def s3_storage(monkeypatch):
    """Create an S3 storage backend with a mocked client."""
    storage = S3Storage("my-bucket", endpoint_url="https://s3.example.com", region="us-east-1")
    client = AsyncMock()
    client.exceptions = _FakeClientExceptions()
    client.put_object.return_value = {}
    client.create_multipart_upload.return_value = {"UploadId": "upload-1"}
    client.upload_part.return_value = {"ETag": "etag"}
    client.complete_multipart_upload.return_value = {}
    client.get_object.return_value = {"Body": _FakeBody(b"")}
    client.delete_object.return_value = {}
    client.head_object.return_value = {}
    monkeypatch.setattr(storage, "_get_client", lambda: _FakeClientContext(client))
    return storage, client


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
async def test_local_retrieve_missing(local_storage):
    """retrieve() returns None when the requested file does not exist."""
    assert await local_storage.retrieve("nonexistent.mp3") is None


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


@pytest.mark.asyncio
async def test_s3_store_put_object(s3_storage):
    """Test that small files are uploaded via put_object."""
    storage, client = s3_storage
    content = b"fake audio data"

    path = await storage.store(io.BytesIO(content), "test/audio.mp3", "audio/mpeg")
    assert path == "test/audio.mp3"

    client.put_object.assert_called_once_with(
        Bucket="my-bucket",
        Key="test/audio.mp3",
        Body=content,
        ContentLength=len(content),
        ContentType="audio/mpeg",
    )
    client.create_multipart_upload.assert_not_called()


@pytest.mark.asyncio
async def test_s3_store_multipart(s3_storage, monkeypatch):
    """Test that files above the multipart threshold use create/complete/abort."""
    storage, client = s3_storage
    monkeypatch.setattr(S3Storage, "_MULTIPART_THRESHOLD", 1024)
    monkeypatch.setattr(S3Storage, "_MULTIPART_PART_SIZE", 64 * 1024)

    content = b"x" * (200 * 1024)
    path = await storage.store(io.BytesIO(content), "files/aa/bb/large.bin", "application/octet-stream")
    assert path == "files/aa/bb/large.bin"

    client.create_multipart_upload.assert_called_once_with(
        Bucket="my-bucket",
        Key=path,
        ContentType="application/octet-stream",
    )
    assert client.upload_part.call_count == 4
    assert client.upload_part.call_args_list[0].kwargs["Body"] == b"x" * (64 * 1024)
    assert client.upload_part.call_args_list[-1].kwargs["Body"] == b"x" * (8 * 1024)
    parts = client.complete_multipart_upload.call_args.kwargs["MultipartUpload"]["Parts"]
    assert len(parts) == 4
    for i, part in enumerate(parts, start=1):
        assert part == {"ETag": "etag", "PartNumber": i}


@pytest.mark.asyncio
async def test_s3_store_multipart_aborts_on_error(s3_storage, monkeypatch):
    """Test that a failed multipart upload is aborted."""
    storage, client = s3_storage
    monkeypatch.setattr(S3Storage, "_MULTIPART_THRESHOLD", 1024)
    client.upload_part.side_effect = RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        await storage.store(io.BytesIO(b"x" * 2048), "files/aa/bb/fail.bin")

    client.abort_multipart_upload.assert_called_once_with(
        Bucket="my-bucket",
        Key="files/aa/bb/fail.bin",
        UploadId="upload-1",
    )
    client.complete_multipart_upload.assert_not_called()


@pytest.mark.asyncio
async def test_s3_store_multipart_default_part_size(s3_storage):
    """Test that real multipart uploads use 5 MiB parts."""
    storage, client = s3_storage

    content = b"x" * (6 * 1024 * 1024)
    path = await storage.store(io.BytesIO(content), "files/aa/bb/large.bin")
    assert path == "files/aa/bb/large.bin"

    client.create_multipart_upload.assert_called_once()
    assert client.upload_part.call_count == 2
    assert len(client.upload_part.call_args_list[0].kwargs["Body"]) == 5 * 1024 * 1024
    assert len(client.upload_part.call_args_list[1].kwargs["Body"]) == 1024 * 1024


@pytest.mark.asyncio
async def test_s3_store_multipart_unknown_size(s3_storage, monkeypatch):
    """Test that unknown-size streams are uploaded without being buffered whole."""
    storage, client = s3_storage
    monkeypatch.setattr(S3Storage, "_MULTIPART_PART_SIZE", 64 * 1024)

    content = b"x" * (200 * 1024)
    path = await storage.store(_NonSeekableStream(content), "files/aa/bb/large.bin")
    assert path == "files/aa/bb/large.bin"

    client.put_object.assert_not_called()
    client.create_multipart_upload.assert_called_once()
    assert client.upload_part.call_count == 4


@pytest.mark.asyncio
async def test_s3_retrieve(s3_storage):
    """Test streaming retrieval from S3 into a temp file."""
    storage, client = s3_storage
    content = b"retrieved audio data"
    client.get_object.return_value = {"Body": _FakeBody(content)}

    path = await storage.retrieve("test/audio.mp3")
    assert path is not None
    assert isinstance(path, Path)
    assert path.read_bytes() == content
    assert path.suffix == ".mp3"
    client.get_object.assert_called_once_with(Bucket="my-bucket", Key="test/audio.mp3")


@pytest.mark.asyncio
async def test_s3_retrieve_missing(s3_storage):
    """Test that retrieving a missing object returns None."""
    storage, client = s3_storage
    client.get_object.side_effect = _FakeNoSuchKey(
        error_response={"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
        operation_name="GetObject",
    )

    assert await storage.retrieve("missing.mp3") is None


@pytest.mark.asyncio
async def test_s3_delete(s3_storage):
    """Test deleting an object from S3."""
    storage, client = s3_storage

    assert await storage.delete("test/audio.mp3") is True
    client.delete_object.assert_called_once_with(Bucket="my-bucket", Key="test/audio.mp3")


@pytest.mark.asyncio
async def test_s3_delete_missing_key(s3_storage):
    """delete() returns False when the S3 object is missing."""
    storage, client = s3_storage
    client.delete_object.side_effect = ClientError(
        error_response={"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
        operation_name="DeleteObject",
    )

    assert await storage.delete("missing.mp3") is False


@pytest.mark.asyncio
async def test_local_store_max_upload_size(tmp_path):
    """Test that LocalStorage enforces max_upload_size and cleans up partial files."""
    storage = LocalStorage(tmp_path / "media", max_upload_size=10)

    assert await storage.store(io.BytesIO(b"0123456789"), "allowed.txt") == "allowed.txt"
    assert await storage.exists("allowed.txt") is True

    with pytest.raises(ValueError, match="exceeds the maximum"):
        await storage.store(io.BytesIO(b"01234567890"), "rejected.txt")

    assert await storage.exists("rejected.txt") is False


@pytest.mark.asyncio
async def test_s3_store_max_upload_size(monkeypatch):
    """Test that S3Storage enforces max_upload_size for small uploads."""
    storage = S3Storage("my-bucket", max_upload_size=10)
    client = AsyncMock()
    client.exceptions = _FakeClientExceptions()
    client.put_object.return_value = {}
    monkeypatch.setattr(storage, "_get_client", lambda: _FakeClientContext(client))

    assert await storage.store(io.BytesIO(b"0123456789"), "allowed.txt") == "allowed.txt"
    client.put_object.assert_called_once()

    with pytest.raises(ValueError, match="exceeds the maximum"):
        await storage.store(io.BytesIO(b"01234567890"), "rejected.txt")

    assert client.put_object.call_count == 1


@pytest.mark.asyncio
async def test_s3_exists(s3_storage):
    """Test checking object existence in S3."""
    storage, client = s3_storage

    assert await storage.exists("test/audio.mp3") is True
    client.head_object.assert_called_once_with(Bucket="my-bucket", Key="test/audio.mp3")


@pytest.mark.asyncio
async def test_s3_exists_missing(s3_storage):
    """Test that missing objects are reported as nonexistent."""
    storage, client = s3_storage
    client.head_object.side_effect = ClientError(
        error_response={"Error": {"Code": "NotFound", "Message": "Not found"}},
        operation_name="HeadObject",
    )

    assert await storage.exists("missing.mp3") is False


@pytest.mark.asyncio
async def test_s3_delete_no_such_bucket(s3_storage):
    """Test that delete re-raises NoSuchBucket instead of treating it as missing."""
    storage, client = s3_storage
    client.delete_object.side_effect = _FakeNoSuchBucket(
        error_response={"Error": {"Code": "NoSuchBucket", "Message": "No such bucket"}},
        operation_name="DeleteObject",
    )

    with pytest.raises(_FakeNoSuchBucket):
        await storage.delete("test/audio.mp3")


@pytest.mark.asyncio
async def test_s3_exists_no_such_bucket(s3_storage):
    """Test that exists re-raises NoSuchBucket instead of treating it as missing."""
    storage, client = s3_storage
    client.head_object.side_effect = _FakeNoSuchBucket(
        error_response={"Error": {"Code": "NoSuchBucket", "Message": "No such bucket"}},
        operation_name="HeadObject",
    )

    with pytest.raises(_FakeNoSuchBucket):
        await storage.exists("test/audio.mp3")


@pytest.mark.asyncio
async def test_s3_retrieve_no_such_bucket(s3_storage):
    """Test that retrieve re-raises NoSuchBucket instead of returning None."""
    storage, client = s3_storage
    client.get_object.side_effect = _FakeNoSuchBucket(
        error_response={"Error": {"Code": "NoSuchBucket", "Message": "No such bucket"}},
        operation_name="GetObject",
    )

    with pytest.raises(_FakeNoSuchBucket):
        await storage.retrieve("test/audio.mp3")


@pytest.mark.skipif(
    os.environ.get("SONGHIVE_TEST_S3_ENDPOINT") is None,
    reason="Requires a real S3-compatible endpoint (e.g. MinIO)",
)
@pytest.mark.asyncio
async def test_s3_store_and_retrieve_minio():
    """Integration test against a real S3-compatible endpoint."""
    endpoint = os.environ["SONGHIVE_TEST_S3_ENDPOINT"]
    access_key = os.environ.get("SONGHIVE_TEST_S3_ACCESS_KEY", "minioadmin")
    secret_key = os.environ.get("SONGHIVE_TEST_S3_SECRET_KEY", "minioadmin")
    bucket = os.environ.get("SONGHIVE_TEST_S3_BUCKET", "test-bucket")
    storage = S3Storage(
        bucket=bucket,
        endpoint_url=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        region="us-east-1",
    )

    small = b"small content"
    path = await storage.store(io.BytesIO(small), "small.txt", "text/plain")
    retrieved = await storage.retrieve(path)
    assert retrieved is not None
    assert retrieved.read_bytes() == small

    large = b"x" * (6 * 1024 * 1024)
    large_path = await storage.store(io.BytesIO(large), "large.bin", "application/octet-stream")
    large_retrieved = await storage.retrieve(large_path)
    assert large_retrieved is not None
    assert large_retrieved.read_bytes() == large

    assert await storage.exists(large_path) is True
    assert await storage.delete(large_path) is True
    assert await storage.delete(path) is True


class _FakeS3Client:
    """Minimal stand-in for an aiobotocore S3 client."""

    def __init__(self, **kwargs):
        self._call_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_, **__):
        return None

    async def put_object(self, **__):
        return {}


@pytest.mark.asyncio
async def test_get_storage_s3_requires_bucket():
    """Selecting the s3 backend without a bucket raises a clear error."""
    config = StorageConfig(backend="s3")
    with pytest.raises(ValueError, match="s3_bucket"):
        get_storage(config)


@pytest.mark.asyncio
async def test_get_storage_s3():
    """get_storage returns a configured S3Storage when s3 is selected."""
    config = StorageConfig(
        backend="s3",
        s3_bucket="my-bucket",
        s3_endpoint="https://s3.example.com",
        s3_access_key="access",
        s3_secret_key="secret",
        s3_region="us-east-1",
        max_upload_size=100,
    )
    backend = get_storage(config)
    assert isinstance(backend, S3Storage)
    assert backend.bucket == "my-bucket"
    assert backend.endpoint_url == "https://s3.example.com"
    assert backend.access_key == "access"
    assert backend.secret_key == "secret"
    assert backend.region == "us-east-1"
    assert backend._max_upload_size == 100


@pytest.mark.asyncio
async def test_s3_get_client_passes_kwargs(monkeypatch):
    """_get_client builds an S3 client with the configured connection params."""
    storage = S3Storage(
        "my-bucket",
        endpoint_url="https://s3.example.com",
        region="us-east-1",
        access_key="access",
        secret_key="secret",
    )
    mock_session_class = MagicMock(return_value=MagicMock(client=MagicMock(return_value=_FakeS3Client())))
    monkeypatch.setattr("songhive.storage.s3.aioboto3.Session", mock_session_class)

    async with storage._get_client() as client:
        assert isinstance(client, _FakeS3Client)

    mock_session_class.return_value.client.assert_called_once_with(
        "s3",
        endpoint_url="https://s3.example.com",
        region_name="us-east-1",
        aws_access_key_id="access",
        aws_secret_access_key="secret",
    )


@pytest.mark.asyncio
async def test_s3_get_client_omits_optional_kwargs():
    """_get_client does not pass unset endpoint, region or credentials."""
    storage = S3Storage("my-bucket")

    class _RecordingSession:
        def __init__(self):
            self.client_call = None

        def client(self, *_, **kwargs):
            self.client_call = kwargs
            fake = _FakeS3Client()
            return fake

    recording = _RecordingSession()
    with patch("songhive.storage.s3.aioboto3.Session", return_value=recording):
        async with storage._get_client() as _:
            pass

    assert recording.client_call == {}


def test_s3_is_missing_error_uses_status_404():
    """_is_missing_error falls back to the HTTP status code 404."""
    exc = ClientError(
        error_response={
            "Error": {"Code": "AccessDenied"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        operation_name="GetObject",
    )
    assert S3Storage._is_missing_error(exc) is True


@pytest.mark.asyncio
async def test_s3_retrieve_client_error_status_404_returns_none():
    """retrieve returns None for a ClientError with a 404 status code."""
    storage = S3Storage("my-bucket")
    client = AsyncMock()
    client.exceptions = _FakeClientExceptions()
    client.get_object.side_effect = ClientError(
        error_response={
            "Error": {"Code": "SomeError", "Message": "Not found"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        operation_name="GetObject",
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(storage, "_get_client", lambda: _FakeClientContext(client))

    assert await storage.retrieve("missing.txt") is None
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_s3_retrieve_exception_cleans_temp_file(s3_storage, monkeypatch):
    """retrieve removes the temporary file when the body stream raises."""
    storage, client = s3_storage

    class _BadBody(_FakeBody):
        async def iter_chunks(self, chunk_size: int = 64 * 1024):
            yield self._data[:1]
            raise RuntimeError("stream failed")

    client.get_object.return_value = {"Body": _BadBody(b"abc")}

    with (
        patch("songhive.storage.s3.aiofiles.os.remove") as mock_remove,
        pytest.raises(RuntimeError, match="stream failed"),
    ):
        await storage.retrieve("fail.txt")

    mock_remove.assert_awaited_once()


@pytest.mark.asyncio
async def test_s3_delete_client_error_not_missing(s3_storage):
    """delete re-raises a ClientError that is not a missing-object error."""
    storage, client = s3_storage
    client.delete_object.side_effect = ClientError(
        error_response={
            "Error": {"Code": "AccessDenied"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        operation_name="DeleteObject",
    )

    with pytest.raises(ClientError, match="AccessDenied"):
        await storage.delete("denied.txt")


@pytest.mark.asyncio
async def test_s3_delete_boto_core_error(s3_storage):
    """delete re-raises a BotoCoreError."""
    storage, client = s3_storage
    client.delete_object.side_effect = BotoCoreError(msg="network down")

    with pytest.raises(BotoCoreError):
        await storage.delete("fail.txt")


@pytest.mark.asyncio
async def test_s3_exists_no_such_key(s3_storage):
    """exists returns False when head_object raises the NoSuchKey service exception."""
    storage, client = s3_storage
    client.head_object.side_effect = client.exceptions.NoSuchKey(
        error_response={"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
        operation_name="HeadObject",
    )

    assert await storage.exists("missing.txt") is False


@pytest.mark.asyncio
async def test_s3_exists_client_error_not_missing(s3_storage):
    """exists re-raises a ClientError that is not a missing-object error."""
    storage, client = s3_storage
    client.head_object.side_effect = ClientError(
        error_response={
            "Error": {"Code": "AccessDenied"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        operation_name="HeadObject",
    )

    with pytest.raises(ClientError, match="AccessDenied"):
        await storage.exists("denied.txt")


@pytest.mark.asyncio
async def test_s3_exists_boto_core_error(s3_storage):
    """exists re-raises a BotoCoreError."""
    storage, client = s3_storage
    client.head_object.side_effect = BotoCoreError(msg="network down")

    with pytest.raises(BotoCoreError):
        await storage.exists("fail.txt")


@pytest.mark.asyncio
async def test_local_path_escape_via_symlink(tmp_path):
    """A path that resolves outside base_path through a symlink is rejected."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")

    base = tmp_path / "media"
    base.mkdir()
    (base / "escape").symlink_to(outside)

    storage = LocalStorage(base)
    for method in ("retrieve", "exists", "delete"):
        with pytest.raises(ValueError, match="escapes"):
            await getattr(storage, method)("escape/secret.txt")


@pytest.mark.asyncio
async def test_local_store_cleans_up_partial_file(tmp_path):
    """store removes a partial file when writing is interrupted."""
    storage = LocalStorage(tmp_path / "media", max_upload_size=10)

    with pytest.raises(FileSizeLimitExceededError):
        await storage.store(_NonSeekableStream(b"x" * 20), "toolarge.bin")

    assert await storage.exists("toolarge.bin") is False
