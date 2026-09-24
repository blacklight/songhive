"""Tests for the WebDAV external-library adapter.

The adapter is exercised against a small in-memory fake httpx client so no
network or credentials are needed.
"""

from __future__ import annotations

import hashlib
import posixpath
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
import pytest

import songhive.services.metadata as metadata_service
import songhive.services.storage as storage_service
from songhive.external import _webdav as webdav_module
from songhive.external._webdav import WebDAVExternalAdapter
from songhive.external.errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from songhive.external.types import ExternalItemRef, ExternalTrackMetadata
from songhive.services.metadata import AudioMetadata


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _etag(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _format_mtime(mtime: datetime) -> str:
    return format_datetime(mtime, usegmt=True)


def _xml_escape(value: str) -> str:
    """Minimal XML-escaping for text inserted into our canned responses."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _file_response(path: str, record: dict) -> str:
    """Return a single PROPFIND ``response`` element for a file."""
    name = _xml_escape(urllib.parse.unquote(posixpath.basename(path.rstrip("/"))))
    size = record.get("size", len(record.get("data", b"")))
    mtime = record.get("mtime") or _now()
    etag = record.get("etag") or _etag(record.get("data", b""))
    href = urllib.parse.quote(path, safe="/")
    return f"""<D:response>
  <D:href>{href}</D:href>
  <D:propstat>
    <D:prop>
      <D:displayname>{name}</D:displayname>
      <D:getcontentlength>{size}</D:getcontentlength>
      <D:getlastmodified>{_format_mtime(mtime)}</D:getlastmodified>
      <D:getetag>"{etag}"</D:getetag>
      <D:resourcetype/>
    </D:prop>
    <D:status>HTTP/1.1 200 OK</D:status>
  </D:propstat>
</D:response>"""


def _dir_response(path: str) -> str:
    """Return a single PROPFIND ``response`` element for a collection."""
    name = _xml_escape(urllib.parse.unquote(posixpath.basename(path.rstrip("/")))) or "/"
    href = urllib.parse.quote(path.rstrip("/") + "/", safe="/")
    return f"""<D:response>
  <D:href>{href}</D:href>
  <D:propstat>
    <D:prop>
      <D:displayname>{name}</D:displayname>
      <D:resourcetype><D:collection/></D:resourcetype>
    </D:prop>
    <D:status>HTTP/1.1 200 OK</D:status>
  </D:propstat>
</D:response>"""


def _propfind_xml(dir_path: str, records: list[tuple[str, dict]]) -> bytes:
    """Build a PROPFIND 207 Multi-Status response for ``dir_path`` and its children."""
    dir_path = dir_path.rstrip("/")
    children: list[str] = []

    for path, record in records:
        if record.get("kind") == "dir":
            children.append(_dir_response(path))
        else:
            children.append(_file_response(path, record))

    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<D:multistatus xmlns:D="DAV:">\n' + _dir_response(dir_path) + "\n" + "\n".join(children) + "\n</D:multistatus>"
    )
    return body.encode("utf-8")


def _propfind_file_xml(path: str, record: dict) -> bytes:
    """Build a PROPFIND 207 Multi-Status response for a single file."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<D:multistatus xmlns:D="DAV:">\n' + _file_response(path, record) + "\n</D:multistatus>"
    )
    return body.encode("utf-8")


def _put(store: dict, path: str, data: bytes, mtime: Optional[datetime] = None, etag: Optional[str] = None) -> None:
    store[path.rstrip("/")] = {
        "kind": "file",
        "data": data,
        "mtime": mtime or _now(),
        "etag": etag or _etag(data),
        "size": len(data),
    }


def _mkdir(store: dict, path: str, mtime: Optional[datetime] = None) -> None:
    store[path.rstrip("/")] = {
        "kind": "dir",
        "data": None,
        "mtime": mtime or _now(),
    }


def _config(**overrides) -> dict:
    cfg: dict = {
        "url": "http://nas.local/Music",
        "username": "music",
        "password": "hunter2",
        "verify_ssl": False,
    }
    cfg.update(overrides)
    return cfg


def _item(provider_key: str, **overrides: Any) -> ExternalItemRef:
    fields: dict[str, Any] = {
        "provider_key": provider_key,
        "display_path": provider_key,
        "size": 3,
        "mime_type": "audio/mpeg",
    }
    fields.update(overrides)
    return ExternalItemRef(**fields)


async def _collect(aiter):
    return [item async for item in aiter]


def _direct_children(store: dict, dir_path: str) -> list[tuple[str, dict]]:
    """Return store entries that are direct children of ``dir_path``."""
    dir_path = dir_path.rstrip("/")
    prefix = dir_path + "/"
    children = []
    for path, record in store.items():
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix) :]
        if not rest or "/" in rest:
            continue
        children.append((path, record))
    return sorted(children, key=lambda t: t[0])


class _FakeStream:
    """Mimics an httpx ``Response`` returned by ``client.stream``."""

    def __init__(self, status: int, data: bytes = b""):
        self.status_code = status
        self.is_error = status >= 400
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aread(self) -> bytes:
        return self._data

    async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]


class _FakeResponse:
    """Mimics an httpx ``Response`` returned by ``client.request``."""

    def __init__(self, status: int, content: bytes = b""):
        self.status_code = status
        self.is_error = status >= 400
        self.content = content


class _FakeClient:
    """In-memory stand-in for the httpx AsyncClient."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.last_kwargs = kwargs
        self._store = _FAKE_STORE

    def _path(self, url: str) -> str:
        return urllib.parse.urlparse(url).path.rstrip("/")

    def _full_path(self, url: str) -> str:
        return urllib.parse.urlparse(url).path

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _find(self, path: str) -> Optional[dict]:
        return self._store.get(path) or self._store.get(path.rstrip("/"))

    async def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        path = self._path(url)

        if method == "PROPFIND":
            record = self._find(path)
            if record is None:
                return _FakeResponse(404)

            if record.get("kind") == "dir":
                depth = (kwargs.get("headers") or {}).get("Depth", "1")
                if depth == "0":
                    records = []
                else:
                    records = _direct_children(self._store, path)
                return _FakeResponse(207, _propfind_xml(path, records))

            return _FakeResponse(207, _propfind_file_xml(path, record))

        if method == "GET":
            record = self._find(path)
            if record is None or record.get("kind") != "file":
                return _FakeResponse(404)
            return _FakeResponse(200, record["data"])

        if method == "PUT":
            data = kwargs.get("content", b"")
            self._store[path] = {
                "kind": "file",
                "data": data,
                "mtime": _now(),
                "etag": _etag(data),
                "size": len(data),
            }
            return _FakeResponse(201)

        if method == "MOVE":
            record = self._find(path)
            if record is None:
                return _FakeResponse(404)

            dest = (kwargs.get("headers") or {}).get("Destination", "")
            dest_path = self._path(dest)
            if self._find(dest_path) is not None:
                return _FakeResponse(412)

            self._store[dest_path] = self._store.pop(path)
            self._store[dest_path]["mtime"] = _now()
            return _FakeResponse(201)

        if method == "DELETE":
            record = self._find(path)
            if record is None:
                return _FakeResponse(404)
            self._store.pop(path, None)
            self._store.pop(path.rstrip("/"), None)
            return _FakeResponse(204)

        return _FakeResponse(405)

    def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStream:
        path = self._path(url)
        record = self._find(path)
        if record is None or record.get("kind") != "file":
            return _FakeStream(404)

        data = record["data"]
        status = 200
        headers = kwargs.get("headers") or {}
        range_header = headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            start_s, end_s = range_header[len("bytes=") :].split("-", 1)
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else len(data) - 1
            data = data[start : end + 1]
            status = 206

        return _FakeStream(status, data)


# Module-level store that is reset per test by the fixture below.
_FAKE_STORE: dict[str, dict] = {}


@pytest.fixture
def webdav_store() -> dict[str, dict]:
    store: dict[str, dict] = {}
    _FAKE_STORE.clear()
    _FAKE_STORE.update(store)
    return _FAKE_STORE


@pytest.fixture
def webdav_client(monkeypatch: pytest.MonkeyPatch, webdav_store: dict[str, dict]) -> type[_FakeClient]:
    def _client(**kwargs):
        client = _FakeClient(**kwargs)
        return client

    monkeypatch.setattr(webdav_module.httpx, "AsyncClient", _client)
    return _FakeClient


@pytest.fixture
def metadata_mock(monkeypatch: pytest.MonkeyPatch):
    def _fake(path: Path) -> AudioMetadata:
        return AudioMetadata(title="song", duration=1.0, mimetype="audio/mpeg")

    monkeypatch.setattr(metadata_service, "extract_metadata", _fake)


class TestValidateConfig:
    async def test_returns_capabilities(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        adapter = WebDAVExternalAdapter()
        caps = await adapter.validate_config(_config())
        assert caps.list_items is True
        assert caps.detect_changes is True
        assert caps.range_read is True
        assert caps.stream_url is False
        assert caps.compute_hash is True
        assert caps.write_tags is False
        assert adapter.capabilities() is caps

    async def test_missing_url_rejected(self, webdav_client: type[_FakeClient]):
        adapter = WebDAVExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(url="  "))

    async def test_invalid_url_rejected(self, webdav_client: type[_FakeClient]):
        adapter = WebDAVExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(url="not-a-url"))

    async def test_missing_root_rejected(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        adapter = WebDAVExternalAdapter()
        with pytest.raises(ExternalConfigError, match="not found"):
            await adapter.validate_config(_config(url="http://nas.local/Gone"))

    async def test_root_must_be_directory(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _put(webdav_store, "/Music.txt", b"x")
        adapter = WebDAVExternalAdapter()
        with pytest.raises(ExternalConfigError, match="not a directory"):
            await adapter.validate_config(_config(url="http://nas.local/Music.txt"))

    async def test_client_kwargs_from_config(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        adapter = WebDAVExternalAdapter()
        await adapter.validate_config(
            _config(
                url="http://nas.local/Music",
                username="admin",
                password="secret",
                timeout=60,
            )
        )
        assert isinstance(webdav_client.last_kwargs.get("auth"), httpx.BasicAuth)
        assert webdav_client.last_kwargs["timeout"].connect == 60.0
        assert webdav_client.last_kwargs["verify"] is False
        assert webdav_client.last_kwargs["follow_redirects"] is True

    async def test_capabilities_required_before_use(self, webdav_client: type[_FakeClient]):
        adapter = WebDAVExternalAdapter()
        item = _item("a.mp3")
        with pytest.raises(ExternalLibraryError):
            await adapter.delete_source(_config(), item)


class TestIterItems:
    async def test_root_and_extension_filter(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/a.mp3", b"aaa")
        _put(webdav_store, "/Music/b.txt", b"bbb")
        _mkdir(webdav_store, "/Music/nested")
        _put(webdav_store, "/Music/nested/c.flac", b"ccc")
        _put(webdav_store, "/Other/d.mp3", b"ddd")

        adapter = WebDAVExternalAdapter()
        items = await _collect(adapter.iter_items(_config()))
        assert [i.provider_key for i in items] == ["a.mp3", "nested/c.flac"]
        item = next(i for i in items if i.provider_key == "a.mp3")
        assert item.size == 3
        assert item.mime_type == "audio/mpeg"
        assert item.etag is not None

    async def test_non_recursive_listing(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/a.mp3", b"aaa")
        _mkdir(webdav_store, "/Music/nested")
        _put(webdav_store, "/Music/nested/c.flac", b"ccc")

        adapter = WebDAVExternalAdapter()
        items = await _collect(adapter.iter_items(_config(recursive=False)))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_exclude_patterns(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/a.mp3", b"aaa")
        _mkdir(webdav_store, "/Music/dupes")
        _put(webdav_store, "/Music/dupes/b.mp3", b"bbb")

        adapter = WebDAVExternalAdapter()
        items = await _collect(adapter.iter_items(_config(exclude=["dupes/*"])))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_since_filter(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        old = _now() - timedelta(days=10)
        recent = _now() - timedelta(hours=1)
        _put(webdav_store, "/Music/old.mp3", b"o", mtime=old)
        _put(webdav_store, "/Music/new.mp3", b"n", mtime=recent)

        adapter = WebDAVExternalAdapter()
        items = await _collect(adapter.iter_items(_config(), since=_now() - timedelta(days=1)))
        assert [i.provider_key for i in items] == ["new.mp3"]

    async def test_scope_restricts_root(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _mkdir(webdav_store, "/Music/rock")
        _mkdir(webdav_store, "/Music/jazz")
        _put(webdav_store, "/Music/rock/a.mp3", b"a")
        _put(webdav_store, "/Music/jazz/b.mp3", b"b")

        adapter = WebDAVExternalAdapter()
        items = await _collect(adapter.iter_items(_config(), scope="rock"))
        assert [i.provider_key for i in items] == ["rock/a.mp3"]

    async def test_list_error_wrapped(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        adapter = WebDAVExternalAdapter()
        with pytest.raises(ExternalConfigError, match="not found"):
            await _collect(adapter.iter_items(_config()))


class TestOpenStream:
    async def test_iterator_stream(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/a.mp3", b"0123456789")
        adapter = WebDAVExternalAdapter()

        stream = await adapter.open_stream(_config(), _item("a.mp3", size=10))
        assert stream.kind == "iterator"
        assert stream.safe_to_redirect is False
        assert stream.supports_range is True
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"0123456789"

    async def test_range_stream(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/a.mp3", b"0123456789")
        adapter = WebDAVExternalAdapter()

        stream = await adapter.open_stream(_config(), _item("a.mp3", size=10), range=(2, 5))
        assert stream.size == 4
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"2345"

    async def test_traversal_key_rejected(self, webdav_client: type[_FakeClient]):
        adapter = WebDAVExternalAdapter()
        with pytest.raises(ExternalPermissionDenied):
            await adapter.open_stream(_config(), _item("../etc/passwd", size=3))


class TestReadMetadata:
    async def test_reads_tags_from_downloaded_file(
        self,
        webdav_store: dict,
        webdav_client: type[_FakeClient],
        metadata_mock,
    ):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/song.mp3", b"audio-bytes")
        adapter = WebDAVExternalAdapter()

        metadata = await adapter.read_metadata(_config(), _item("song.mp3"))
        assert metadata.title == "song"
        assert metadata.raw_metadata is not None
        assert metadata.raw_metadata["url"] == "http://nas.local/Music/song.mp3"

    async def test_missing_file_raises_not_found(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        adapter = WebDAVExternalAdapter()
        with pytest.raises(ExternalItemNotFound):
            await adapter.read_metadata(_config(), _item("gone.mp3"))


class TestComputeSha256:
    async def test_fast_hash_streams_file(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/a.mp3", b"hashme")
        adapter = WebDAVExternalAdapter()
        sha = await adapter.compute_sha256(_config(fast_hash=True), _item("a.mp3"))
        assert sha == hashlib.sha256(b"hashme").hexdigest()

    async def test_ffmpeg_hash(self, webdav_store: dict, webdav_client: type[_FakeClient], monkeypatch):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/a.mp3", b"hashme")

        async def _fake_hash(path: Path) -> str:
            assert path.read_bytes() == b"hashme"
            return "f" * 64

        monkeypatch.setattr(storage_service, "audio_hash", _fake_hash)
        adapter = WebDAVExternalAdapter()
        sha = await adapter.compute_sha256(_config(), _item("a.mp3"))
        assert sha == "f" * 64


class TestWriteMetadata:
    async def test_reuploads_rewritten_file(
        self,
        webdav_store: dict,
        webdav_client: type[_FakeClient],
        monkeypatch,
    ):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/a.mp3", b"old")

        def _fake_write(path: Path, meta) -> None:
            path.write_bytes(b"new")

        monkeypatch.setattr(metadata_service, "write_metadata", _fake_write)

        adapter = WebDAVExternalAdapter()
        await adapter.validate_config(_config(allow_write_tags=True))
        result = await adapter.write_metadata(
            _config(allow_write_tags=True),
            _item("a.mp3"),
            ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
        )
        assert webdav_store["/Music/a.mp3"]["data"] == b"new"
        assert result.provider_key == "a.mp3"

    async def test_disabled_raises(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        adapter = WebDAVExternalAdapter()
        await adapter.validate_config(_config())
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.write_metadata(
                _config(),
                _item("a.mp3"),
                ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
            )


class TestRenameAndDelete:
    async def test_rename_moves_file(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _mkdir(webdav_store, "/Music/dir")
        _put(webdav_store, "/Music/dir/old.mp3", b"data")
        adapter = WebDAVExternalAdapter()
        await adapter.validate_config(_config(allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(allow_rename_source=True),
            _item("dir/old.mp3"),
            "new.mp3",
        )
        assert "/Music/dir/old.mp3" not in webdav_store
        assert webdav_store["/Music/dir/new.mp3"]["data"] == b"data"
        assert new_ref.provider_key == "dir/new.mp3"
        assert new_ref.size == 4

    async def test_rename_existing_target_denied(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/old.mp3", b"a")
        _put(webdav_store, "/Music/new.mp3", b"b")
        adapter = WebDAVExternalAdapter()
        await adapter.validate_config(_config(allow_rename_source=True))

        with pytest.raises(ExternalPermissionDenied):
            await adapter.rename_source(_config(allow_rename_source=True), _item("old.mp3"), "new.mp3")

    async def test_rename_same_name_is_noop(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/dir/song.mp3", b"data")
        adapter = WebDAVExternalAdapter()
        await adapter.validate_config(_config(allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(allow_rename_source=True),
            _item("dir/song.mp3"),
            "song.mp3",
        )
        assert new_ref.provider_key == "dir/song.mp3"

    async def test_delete_removes_file(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        _put(webdav_store, "/Music/a.mp3", b"data")
        adapter = WebDAVExternalAdapter()
        await adapter.validate_config(_config(allow_delete_source=True))

        result = await adapter.delete_source(_config(allow_delete_source=True), _item("a.mp3"))
        assert "/Music/a.mp3" not in webdav_store
        assert result.provider_key == "a.mp3"

    async def test_delete_missing_raises_not_found(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        adapter = WebDAVExternalAdapter()
        await adapter.validate_config(_config(allow_delete_source=True))

        with pytest.raises(ExternalItemNotFound):
            await adapter.delete_source(_config(allow_delete_source=True), _item("gone.mp3"))

    async def test_delete_disabled_raises(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        adapter = WebDAVExternalAdapter()
        await adapter.validate_config(_config())
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.delete_source(_config(), _item("a.mp3"))


class TestHealthcheck:
    async def test_healthy(self, webdav_store: dict, webdav_client: type[_FakeClient]):
        _mkdir(webdav_store, "/Music")
        adapter = WebDAVExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is True
        assert "Music" in (health.message or "")

    async def test_unreachable(self, webdav_client: type[_FakeClient]):
        # Empty store means the PROPFIND returns 404.
        adapter = WebDAVExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is False
