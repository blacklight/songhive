"""Tests for the S3 external-library adapter.

The adapter is exercised against a small in-memory fake of the aioboto3
client so no network or credentials are needed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

import pytest
from botocore.exceptions import ClientError

import songhive.services.metadata as metadata_service
import songhive.services.storage as storage_service
from songhive.external import _s3 as s3_module
from songhive.external._s3 import S3ExternalAdapter
from songhive.external.errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from songhive.external.types import ExternalItemRef, ExternalTrackMetadata
from songhive.services.metadata import AudioMetadata


class _FakeBody:
    """Mimics botocore's StreamingBody for object reads."""

    def __init__(self, data: bytes):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunks(self, chunk_size: int) -> AsyncIterator[bytes]:
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]


def _etag(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _client_error(code: str, operation: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        },
        operation,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _FakeS3Client:
    """In-memory stand-in for the aioboto3 S3 client."""

    def __init__(self, store: dict):
        self._store = store
        self.client_kwargs: Optional[dict] = None
        self.deny_list = False
        self.deny_head_bucket = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def list_objects_v2(
        self,
        Bucket: str,
        Prefix: str = "",
        MaxKeys: int = 1000,
        ContinuationToken: Optional[str] = None,
        Delimiter: Optional[str] = None,
    ) -> dict:
        if self.deny_list:
            raise _client_error("AccessDenied", "ListObjectsV2")

        keys = sorted(k for k in self._store if k.startswith(Prefix))
        if Delimiter:
            collapsed = set()
            for key in keys:
                rest = key[len(Prefix) :]
                if "/" in rest:
                    collapsed.add(Prefix + rest.split("/", 1)[0] + "/")
                else:
                    collapsed.add(key)
            keys = sorted(collapsed)

        start = int(ContinuationToken or 0)
        page = keys[start : start + MaxKeys]
        contents = [
            {
                "Key": key,
                "ETag": f'"{self._store[key]["etag"]}"',
                "LastModified": self._store[key]["mtime"],
                "Size": len(self._store[key]["data"]),
            }
            for key in page
            if key in self._store
        ]
        truncated = start + MaxKeys < len(keys)
        response: dict = {"Contents": contents, "IsTruncated": truncated}
        if truncated:
            response["NextContinuationToken"] = str(start + MaxKeys)
        return response

    async def head_object(self, Bucket: str, Key: str, **kwargs) -> dict:
        if Key not in self._store:
            raise _client_error("NoSuchKey", "HeadObject")
        obj = self._store[Key]
        response = {
            "ContentLength": len(obj["data"]),
            "ETag": f'"{obj["etag"]}"',
            "LastModified": obj["mtime"],
            "ContentType": "audio/mpeg",
        }
        if obj.get("checksum_sha256") is not None:
            response["ChecksumSHA256"] = obj["checksum_sha256"]
        return response

    async def get_object(self, Bucket: str, Key: str, Range: Optional[str] = None) -> dict:
        if Key not in self._store:
            raise _client_error("NoSuchKey", "GetObject")
        data = self._store[Key]["data"]
        total = len(data)
        if Range:
            start_s, end_s = Range[len("bytes=") :].split("-", 1)
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else total - 1
            body = data[start : end + 1]
            return {
                "Body": _FakeBody(body),
                "ContentLength": len(body),
                "ContentRange": f"bytes {start}-{end}/{total}",
            }
        return {"Body": _FakeBody(data), "ContentLength": total}

    async def put_object(self, Bucket: str, Key: str, Body: bytes, **kwargs) -> dict:
        etag = _etag(Body)
        self._store[Key] = {"data": Body, "etag": etag, "mtime": _now()}
        return {"ETag": f'"{etag}"'}

    async def copy_object(self, Bucket: str, Key: str, CopySource: dict, **kwargs) -> dict:
        src = CopySource["Key"]
        if src not in self._store:
            raise _client_error("NoSuchKey", "CopyObject")
        self._store[Key] = dict(self._store[src])
        return {}

    async def delete_object(self, Bucket: str, Key: str) -> dict:
        self._store.pop(Key, None)
        return {}

    async def head_bucket(self, Bucket: str) -> dict:
        if self.deny_head_bucket:
            raise _client_error("AccessDenied", "HeadBucket")
        return {}

    async def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int) -> str:
        assert operation == "get_object"
        return f"https://s3.test/{Params['Bucket']}/{Params['Key']}" f"?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=fake"


def _put(store: dict, key: str, data: bytes, mtime: Optional[datetime] = None) -> None:
    store[key] = {
        "data": data,
        "etag": _etag(data),
        "mtime": mtime or _now(),
    }


async def _collect(aiter):
    return [item async for item in aiter]


def _config(**overrides) -> dict:
    cfg: dict = {"bucket": "music-bucket"}
    cfg.update(overrides)
    return cfg


def _item(provider_key: str, **overrides) -> ExternalItemRef:
    fields = {
        "provider_key": provider_key,
        "display_path": provider_key,
        "size": 3,
        "mime_type": "audio/mpeg",
    }
    fields.update(overrides)
    return ExternalItemRef(**fields)


@pytest.fixture
def s3_store() -> dict:
    return {}


@pytest.fixture
def s3_client(monkeypatch: pytest.MonkeyPatch, s3_store: dict) -> _FakeS3Client:
    client = _FakeS3Client(s3_store)

    class _Session:
        def client(self, service: str, **kwargs):
            assert service == "s3"
            client.client_kwargs = kwargs
            return client

    monkeypatch.setattr(s3_module.aioboto3, "Session", lambda: _Session())
    return client


@pytest.fixture
def metadata_mock(monkeypatch: pytest.MonkeyPatch):
    def _fake(path: Path) -> AudioMetadata:
        return AudioMetadata(title="song", duration=1.0, mimetype="audio/mpeg")

    monkeypatch.setattr(metadata_service, "extract_metadata", _fake)


class TestValidateConfig:
    async def test_returns_capabilities(self, s3_store: dict, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        caps = await adapter.validate_config(_config())
        assert caps.list_items is True
        assert caps.detect_changes is True
        assert caps.stream_url is True
        assert caps.compute_hash is True
        assert caps.write_tags is False
        assert adapter.capabilities() is caps

    async def test_missing_bucket_rejected(self, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(bucket="  "))

    async def test_endpoint_url_validated(self, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(endpoint_url="not-a-url"))

    async def test_partial_credentials_rejected(self, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(access_key="AKID"))

    async def test_bucket_inaccessible(self, s3_store: dict, s3_client: _FakeS3Client):
        s3_client.deny_list = True
        adapter = S3ExternalAdapter()
        with pytest.raises(ExternalConfigError, match="AccessDenied"):
            await adapter.validate_config(_config())

    async def test_client_kwargs_from_config(self, s3_store: dict, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        await adapter.validate_config(
            _config(
                endpoint_url="http://minio:9000",
                region="eu-west-1",
                access_key="AKID",
                secret_key="secret",
            )
        )
        assert s3_client.client_kwargs == {
            "endpoint_url": "http://minio:9000",
            "region_name": "eu-west-1",
            "aws_access_key_id": "AKID",
            "aws_secret_access_key": "secret",
        }

    async def test_capabilities_required_before_use(self, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        item = _item("music/a.mp3")
        with pytest.raises(ExternalLibraryError):
            await adapter.delete_source(_config(), item)


class TestIterItems:
    async def test_prefix_and_extension_filter(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/a.mp3", b"aaa")
        _put(s3_store, "music/b.txt", b"bbb")
        _put(s3_store, "music/nested/c.flac", b"ccc")
        _put(s3_store, "other/d.mp3", b"ddd")

        adapter = S3ExternalAdapter()
        items = await _collect(adapter.iter_items(_config(prefix="music/")))
        assert [i.provider_key for i in items] == ["a.mp3", "nested/c.flac"]
        assert items[0].etag == _etag(b"aaa")
        assert items[0].size == 3
        assert items[0].mime_type == "audio/mpeg"

    async def test_non_recursive_listing(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/a.mp3", b"aaa")
        _put(s3_store, "music/nested/c.flac", b"ccc")

        adapter = S3ExternalAdapter()
        items = await _collect(adapter.iter_items(_config(prefix="music/", recursive=False)))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_exclude_patterns(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/a.mp3", b"aaa")
        _put(s3_store, "music/dupes/b.mp3", b"bbb")

        adapter = S3ExternalAdapter()
        items = await _collect(adapter.iter_items(_config(prefix="music/", exclude=["dupes/*"])))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_since_filter(self, s3_store: dict, s3_client: _FakeS3Client):
        old = _now() - timedelta(days=10)
        recent = _now() - timedelta(hours=1)
        _put(s3_store, "music/old.mp3", b"o", mtime=old)
        _put(s3_store, "music/new.mp3", b"n", mtime=recent)

        adapter = S3ExternalAdapter()
        items = await _collect(adapter.iter_items(_config(prefix="music/"), since=_now() - timedelta(days=1)))
        assert [i.provider_key for i in items] == ["new.mp3"]

    async def test_scope_restricts_prefix(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/rock/a.mp3", b"a")
        _put(s3_store, "music/jazz/b.mp3", b"b")

        adapter = S3ExternalAdapter()
        items = await _collect(adapter.iter_items(_config(prefix="music/"), scope="rock"))
        assert [i.provider_key for i in items] == ["rock/a.mp3"]

    async def test_list_error_wrapped(self, s3_store: dict, s3_client: _FakeS3Client):
        s3_client.deny_list = True
        adapter = S3ExternalAdapter()
        with pytest.raises(ExternalConfigError, match="AccessDenied"):
            await _collect(adapter.iter_items(_config()))


class TestOpenStream:
    async def test_presigned_url_stream(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/a.mp3", b"aaa")
        adapter = S3ExternalAdapter()

        stream = await adapter.open_stream(
            _config(prefix="music/", presigned_urls=True),
            _item("a.mp3", size=3),
        )
        assert stream.kind == "url"
        assert stream.url is not None
        assert "s3.test/music-bucket/music/a.mp3" in stream.url
        assert stream.safe_to_redirect is True
        assert stream.supports_range is True
        assert stream.size == 3

    async def test_presigned_expiry_clamped(self, s3_store: dict, s3_client: _FakeS3Client, monkeypatch):
        captured = {}

        async def _fake_url(operation, Params, ExpiresIn):
            captured["expires"] = ExpiresIn
            return "https://s3.test/x"

        monkeypatch.setattr(s3_client, "generate_presigned_url", _fake_url)
        adapter = S3ExternalAdapter()
        await adapter.open_stream(
            _config(presigned_urls=True, presigned_expiry_seconds=10**9),
            _item("a.mp3"),
        )
        assert captured["expires"] == 7 * 24 * 3600

    async def test_proxy_stream(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/a.mp3", b"0123456789")
        adapter = S3ExternalAdapter()

        stream = await adapter.open_stream(_config(prefix="music/", presigned_urls=False), _item("a.mp3", size=10))
        assert stream.kind == "iterator"
        assert stream.safe_to_redirect is False
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"0123456789"

    async def test_proxy_stream_range(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/a.mp3", b"0123456789")
        adapter = S3ExternalAdapter()

        stream = await adapter.open_stream(
            _config(prefix="music/", presigned_urls=False),
            _item("a.mp3", size=10),
            range=(2, 5),
        )
        assert stream.size == 4
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"2345"

    async def test_traversal_key_rejected(self, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        with pytest.raises(ExternalPermissionDenied):
            await adapter.open_stream(_config(), _item("../etc/passwd", size=3))


class TestReadMetadata:
    async def test_reads_tags_from_downloaded_object(
        self,
        s3_store: dict,
        s3_client: _FakeS3Client,
        metadata_mock,
    ):
        _put(s3_store, "music/song.mp3", b"audio-bytes")
        adapter = S3ExternalAdapter()

        metadata = await adapter.read_metadata(_config(prefix="music/"), _item("song.mp3"))
        assert metadata.title == "song"
        assert metadata.raw_metadata is not None
        assert metadata.raw_metadata["bucket"] == "music-bucket"
        assert metadata.raw_metadata["key"] == "music/song.mp3"

    async def test_missing_object_raises_not_found(self, s3_store: dict, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        with pytest.raises(ExternalItemNotFound):
            await adapter.read_metadata(_config(), _item("gone.mp3"))


class TestComputeSha256:
    async def test_uses_s3_checksum_when_available(self, s3_store: dict, s3_client: _FakeS3Client, monkeypatch):
        import base64

        _put(s3_store, "music/a.mp3", b"data")
        s3_store["music/a.mp3"]["checksum_sha256"] = base64.b64encode(bytes.fromhex("ab" * 32)).decode()

        async def _boom(path):
            raise AssertionError("download must not happen when checksum exists")

        monkeypatch.setattr(s3_client, "get_object", _boom)
        adapter = S3ExternalAdapter()
        sha = await adapter.compute_sha256(_config(prefix="music/"), _item("a.mp3"))
        assert sha == "ab" * 32

    async def test_fast_hash_streams_object(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/a.mp3", b"hashme")
        adapter = S3ExternalAdapter()
        sha = await adapter.compute_sha256(_config(prefix="music/", fast_hash=True), _item("a.mp3"))
        assert sha == hashlib.sha256(b"hashme").hexdigest()

    async def test_ffmpeg_hash(self, s3_store: dict, s3_client: _FakeS3Client, monkeypatch):
        _put(s3_store, "music/a.mp3", b"hashme")

        async def _fake_hash(path: Path) -> str:
            assert path.read_bytes() == b"hashme"
            return "f" * 64

        monkeypatch.setattr(storage_service, "audio_hash", _fake_hash)
        adapter = S3ExternalAdapter()
        sha = await adapter.compute_sha256(_config(prefix="music/", allow_hashing=True), _item("a.mp3"))
        assert sha == "f" * 64


class TestWriteMetadata:
    async def test_reuploads_rewritten_object(
        self,
        s3_store: dict,
        s3_client: _FakeS3Client,
        monkeypatch,
    ):
        _put(s3_store, "music/a.mp3", b"old")

        def _fake_write(path: Path, meta) -> None:
            path.write_bytes(b"new")

        monkeypatch.setattr(metadata_service, "write_metadata", _fake_write)

        adapter = S3ExternalAdapter()
        await adapter.validate_config(_config(prefix="music/", allow_write_tags=True))
        result = await adapter.write_metadata(
            _config(prefix="music/", allow_write_tags=True),
            _item("a.mp3"),
            ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
        )
        assert s3_store["music/a.mp3"]["data"] == b"new"
        assert result.provider_key == "a.mp3"
        assert result.etag == _etag(b"new")

    async def test_disabled_raises(self, s3_store: dict, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        await adapter.validate_config(_config())
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.write_metadata(
                _config(),
                _item("a.mp3"),
                ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
            )


class TestRenameAndDelete:
    async def test_rename_copies_then_deletes(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/dir/old.mp3", b"data")
        adapter = S3ExternalAdapter()
        await adapter.validate_config(_config(prefix="music/", allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(prefix="music/", allow_rename_source=True),
            _item("dir/old.mp3"),
            "new.mp3",
        )
        assert "music/dir/old.mp3" not in s3_store
        assert s3_store["music/dir/new.mp3"]["data"] == b"data"
        assert new_ref.provider_key == "dir/new.mp3"
        assert new_ref.size == 4

    async def test_rename_existing_target_denied(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/old.mp3", b"a")
        _put(s3_store, "music/new.mp3", b"b")
        adapter = S3ExternalAdapter()
        await adapter.validate_config(_config(prefix="music/", allow_rename_source=True))

        with pytest.raises(ExternalPermissionDenied):
            await adapter.rename_source(_config(prefix="music/", allow_rename_source=True), _item("old.mp3"), "new.mp3")

    async def test_rename_same_name_is_noop(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/dir/song.mp3", b"data")
        adapter = S3ExternalAdapter()
        await adapter.validate_config(_config(prefix="music/", allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(prefix="music/", allow_rename_source=True),
            _item("dir/song.mp3"),
            "song.mp3",
        )
        assert new_ref.provider_key == "dir/song.mp3"
        assert s3_store["music/dir/song.mp3"]["data"] == b"data"

    async def test_rename_disabled(self, s3_store: dict, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        await adapter.validate_config(_config())
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.rename_source(_config(), _item("a.mp3"), "b.mp3")

    async def test_delete_source(self, s3_store: dict, s3_client: _FakeS3Client):
        _put(s3_store, "music/a.mp3", b"data")
        adapter = S3ExternalAdapter()
        await adapter.validate_config(_config(prefix="music/", allow_delete_source=True))

        await adapter.delete_source(_config(prefix="music/", allow_delete_source=True), _item("a.mp3"))
        assert "music/a.mp3" not in s3_store

    async def test_delete_missing_object(self, s3_store: dict, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        await adapter.validate_config(_config(prefix="music/", allow_delete_source=True))
        with pytest.raises(ExternalItemNotFound):
            await adapter.delete_source(_config(prefix="music/", allow_delete_source=True), _item("gone.mp3"))

    async def test_delete_disabled(self, s3_store: dict, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        await adapter.validate_config(_config())
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.delete_source(_config(), _item("a.mp3"))


class TestHealthcheck:
    async def test_healthy(self, s3_store: dict, s3_client: _FakeS3Client):
        adapter = S3ExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is True
        assert "music-bucket" in (health.message or "")

    async def test_unhealthy(self, s3_store: dict, s3_client: _FakeS3Client):
        s3_client.deny_head_bucket = True
        adapter = S3ExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is False
        assert "AccessDenied" in (health.message or "")
