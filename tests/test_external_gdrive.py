"""Tests for the Google Drive external-library adapter.

The adapter is exercised against a small in-memory fake of the Drive API v3
and the OAuth token endpoint, so no network or credentials are needed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import pytest

import songhive.services.metadata as metadata_service
import songhive.services.storage as storage_service
from songhive.external import _gdrive as gdrive_module
from songhive.external._gdrive import GoogleDriveExternalAdapter
from songhive.external.errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from songhive.external.types import ExternalItemRef, ExternalTrackMetadata
from songhive.services.metadata import AudioMetadata

_API_BASE = "https://www.googleapis.com/drive/v3"
_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_FOLDER_MIME = "application/vnd.google-apps.folder"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _put(
    store: dict,
    file_id: str,
    name: str,
    data: bytes,
    *,
    parent: str = "root",
    mime: str = "audio/mpeg",
    mtime: Optional[datetime] = None,
    md5: Optional[str] = None,
) -> None:
    store[file_id] = {
        "id": file_id,
        "name": name,
        "mimeType": mime,
        "data": data,
        "parents": [parent],
        "trashed": False,
        "modifiedTime": _rfc3339(mtime or _now()),
        "md5Checksum": md5 if md5 is not None else _md5(data),
    }


def _mkdir(store: dict, file_id: str, name: str, *, parent: str = "root") -> None:
    store[file_id] = {
        "id": file_id,
        "name": name,
        "mimeType": _FOLDER_MIME,
        "data": None,
        "parents": [parent],
        "trashed": False,
        "modifiedTime": _rfc3339(_now()),
        "md5Checksum": None,
    }


def _file_meta(record: dict) -> dict:
    """Return the file resource JSON shape the adapter consumes."""
    meta = {
        "id": record["id"],
        "name": record["name"],
        "mimeType": record["mimeType"],
        "modifiedTime": record["modifiedTime"],
    }
    if record["data"] is not None:
        meta["size"] = str(len(record["data"]))
    if record["md5Checksum"] is not None:
        meta["md5Checksum"] = record["md5Checksum"]
    return meta


def _config(**overrides) -> dict:
    cfg: dict = {
        "access_token": "ya29.test-token",
        "verify_ssl": False,
    }
    cfg.update(overrides)
    return cfg


def _oauth_config(**overrides) -> dict:
    cfg: dict = {
        "client_id": "test-client.apps.googleusercontent.com",
        "client_secret": "test-secret",
        "refresh_token": "test-refresh-token",
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


async def _collect(aiter) -> list:
    return [item async for item in aiter]


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

    def __init__(self, status: int, payload: Any = None):
        self.status_code = status
        self.is_error = status >= 400
        self._payload = payload
        self.content = json.dumps(payload).encode("utf-8") if payload is not None else b""

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeClient:
    """In-memory stand-in for the httpx AsyncClient."""

    last_kwargs: dict[str, Any] = {}
    requests: list[dict[str, Any]] = []
    token_requests: list[dict[str, Any]] = []
    page_size: int = 1000
    token_error: Optional[dict] = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.last_kwargs = kwargs
        self._store = _FAKE_STORE

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _record(self, method: str, url: str, kwargs: dict) -> None:
        self.__class__.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(kwargs.get("headers") or {}),
                "params": dict(kwargs.get("params") or {}),
            }
        )

    def _file_id_from_url(self, url: str, base: str) -> str:
        path = urllib.parse.urlparse(url).path
        prefix = urllib.parse.urlparse(base).path + "/files/"
        return urllib.parse.unquote(path[len(prefix) :])

    def _handle_token_request(self, kwargs: dict) -> _FakeResponse:
        data = dict(kwargs.get("data") or {})
        self.__class__.token_requests.append(data)
        if self.__class__.token_error is not None:
            return _FakeResponse(400, self.__class__.token_error)
        if data.get("refresh_token") == "bad":
            return _FakeResponse(400, {"error": "invalid_grant", "error_description": "Token has been revoked."})
        token = f"fake-access-token-{len(self.__class__.token_requests)}"
        return _FakeResponse(200, {"access_token": token, "expires_in": 3600, "token_type": "Bearer"})

    def _handle_list(self, kwargs: dict) -> _FakeResponse:
        params = kwargs.get("params") or {}
        q = params.get("q") or ""
        # Extract the parent folder ID from "'<id>' in parents".
        parent = ""
        if " in parents" in q:
            parent = q.split("'", 2)[1] if q.startswith("'") else ""
        children = [
            _file_meta(record)
            for record in self._store.values()
            if parent in record["parents"] and not record["trashed"]
        ]
        children.sort(key=lambda meta: meta["id"])

        page_size = min(int(params.get("pageSize") or 1000), self.__class__.page_size)
        start = int(params.get("pageToken") or 0)
        page = children[start : start + page_size]
        payload: dict[str, Any] = {"files": page}
        if start + page_size < len(children):
            payload["nextPageToken"] = str(start + page_size)
        return _FakeResponse(200, payload)

    async def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self._record(method, url, kwargs)
        parsed = urllib.parse.urlparse(url)

        if method == "POST" and parsed.netloc == "oauth2.googleapis.com":
            return self._handle_token_request(kwargs)

        if url.startswith(_UPLOAD_BASE):
            file_id = self._file_id_from_url(url, _UPLOAD_BASE)
            record = self._store.get(file_id)
            if record is None or record["trashed"]:
                return _FakeResponse(404, {"error": {"code": 404, "message": "File not found."}})
            if method == "PATCH":
                record["data"] = kwargs.get("content") or b""
                record["modifiedTime"] = _rfc3339(_now())
                record["md5Checksum"] = _md5(record["data"])
                return _FakeResponse(200, _file_meta(record))
            return _FakeResponse(405)

        if url.startswith(_API_BASE):
            path = parsed.path
            if path.endswith("/files") or path.endswith("/files/"):
                if method == "GET":
                    return self._handle_list(kwargs)
                return _FakeResponse(405)

            file_id = self._file_id_from_url(url, _API_BASE)
            record = self._store.get(file_id)
            if record is None or record["trashed"]:
                return _FakeResponse(404, {"error": {"code": 404, "message": "File not found."}})

            if method == "GET":
                return _FakeResponse(200, _file_meta(record))

            if method == "PATCH":
                body = kwargs.get("json") or {}
                if "name" in body:
                    record["name"] = body["name"]
                if "trashed" in body:
                    record["trashed"] = bool(body["trashed"])
                record["modifiedTime"] = _rfc3339(_now())
                return _FakeResponse(200, _file_meta(record))

            if method == "DELETE":
                self._store.pop(file_id, None)
                return _FakeResponse(204)

            return _FakeResponse(405)

        return _FakeResponse(404, {"error": {"code": 404, "message": "Not found."}})

    def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStream:
        self._record(method, url, kwargs)
        parsed = urllib.parse.urlparse(url)
        if not url.startswith(_API_BASE) or "alt=media" not in parsed.query:
            return _FakeStream(404)

        file_id = self._file_id_from_url(url, _API_BASE)
        record = self._store.get(file_id)
        if record is None or record["trashed"] or record["data"] is None:
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
def drive_store() -> dict[str, dict]:
    store: dict[str, dict] = {}
    _FAKE_STORE.clear()
    _FAKE_STORE.update(store)
    _FakeClient.requests.clear()
    _FakeClient.token_requests.clear()
    _FakeClient.page_size = 1000
    _FakeClient.token_error = None
    return _FAKE_STORE


@pytest.fixture
def drive_client(monkeypatch: pytest.MonkeyPatch, drive_store: dict[str, dict]) -> type[_FakeClient]:
    def _client(**kwargs):
        return _FakeClient(**kwargs)

    monkeypatch.setattr(gdrive_module.httpx, "AsyncClient", _client)
    return _FakeClient


@pytest.fixture
def metadata_mock(monkeypatch: pytest.MonkeyPatch):
    def _fake(path: Path) -> AudioMetadata:
        return AudioMetadata(title="song", duration=1.0, mimetype="audio/mpeg")

    monkeypatch.setattr(metadata_service, "extract_metadata", _fake)


def _service_account_key() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return json.dumps(
        {
            "client_email": "songhive@test.iam.gserviceaccount.com",
            "private_key": pem,
            "token_uri": _TOKEN_URI,
        }
    )


class TestValidateConfig:
    async def test_returns_capabilities(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        adapter = GoogleDriveExternalAdapter()
        caps = await adapter.validate_config(_config())
        assert caps.list_items is True
        assert caps.detect_changes is True
        assert caps.range_read is True
        assert caps.stream_url is False
        assert caps.compute_hash is True
        assert caps.write_tags is False
        assert adapter.capabilities() is caps

    async def test_missing_credentials_rejected(self, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config({"verify_ssl": False})

    async def test_refresh_token_requires_client_pair(self, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(access_token=None, refresh_token="abc"))

    async def test_missing_root_rejected(self, drive_store: dict, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalConfigError, match="not found"):
            await adapter.validate_config(_config(root_folder_id="gone"))

    async def test_root_must_be_folder(self, drive_store: dict, drive_client: type[_FakeClient]):
        _put(drive_store, "file1", "song.mp3", b"x", parent="root")
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalConfigError, match="not a folder"):
            await adapter.validate_config(_config(root_folder_id="file1"))

    async def test_shared_drive_defaults_to_drive_root(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "drive1", "Team Drive")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config(drive_id="drive1"))
        last = _FakeClient.requests[-1]
        assert last["url"].endswith("/files/drive1")
        assert last["params"]["supportsAllDrives"] == "true"

    async def test_invalid_root_id_rejected(self, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalConfigError, match="Invalid folder ID") as excinfo:
            await adapter.validate_config(_config(root_folder_id="../etc/passwd"))
        assert excinfo.value.field == "root_folder_id"

    async def test_capabilities_required_before_use(self, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        item = _item("file1")
        with pytest.raises(ExternalLibraryError):
            await adapter.delete_source(_config(), item)


class TestAuth:
    async def test_static_access_token_sent(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config(access_token="ya29.static"))
        assert _FakeClient.token_requests == []
        auth = _FakeClient.requests[-1]["headers"].get("Authorization")
        assert auth == "Bearer ya29.static"

    async def test_oauth_refresh_flow(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_oauth_config())

        assert len(_FakeClient.token_requests) == 1
        grant = _FakeClient.token_requests[0]
        assert grant["grant_type"] == "refresh_token"
        assert grant["refresh_token"] == "test-refresh-token"
        assert grant["client_id"] == "test-client.apps.googleusercontent.com"
        auth = _FakeClient.requests[-1]["headers"].get("Authorization")
        assert auth == "Bearer fake-access-token-1"

    async def test_token_cached_across_requests(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"aaa", parent="root")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_oauth_config())
        await _collect(adapter.iter_items(_oauth_config()))
        # validate_config + iter_items share one token mint.
        assert len(_FakeClient.token_requests) == 1

    async def test_invalid_grant_raises_permission_denied(self, drive_store: dict, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalPermissionDenied, match="invalid_grant"):
            await adapter.validate_config(_oauth_config(refresh_token="bad"))

    async def test_service_account_jwt_flow(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config(access_token=None, service_account_key=_service_account_key()))

        assert len(_FakeClient.token_requests) == 1
        grant = _FakeClient.token_requests[0]
        assert grant["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
        assertion = grant["assertion"]
        header_b64, claims_b64, signature_b64 = assertion.split(".")
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(claims_b64))
        assert header["alg"] == "RS256"
        assert claims["iss"] == "songhive@test.iam.gserviceaccount.com"
        assert claims["aud"] == _TOKEN_URI
        assert claims["scope"] == "https://www.googleapis.com/auth/drive.readonly"
        assert signature_b64

    async def test_service_account_requests_full_scope_for_writes(
        self, drive_store: dict, drive_client: type[_FakeClient]
    ):
        _mkdir(drive_store, "root", "My Drive")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(
            _config(access_token=None, service_account_key=_service_account_key(), allow_write_tags=True)
        )
        claims = json.loads(_b64url_decode(_FakeClient.token_requests[0]["assertion"].split(".")[1]))
        assert claims["scope"] == "https://www.googleapis.com/auth/drive"

    async def test_service_account_bad_json_rejected(self, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(access_token=None, service_account_key="{not json"))


class TestIterItems:
    async def test_walks_tree_and_filters_extensions(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _mkdir(drive_store, "nested", "nested", parent="root")
        _put(drive_store, "a", "a.mp3", b"aaa", parent="root")
        _put(drive_store, "b", "b.txt", b"bbb", parent="root")
        _put(drive_store, "c", "c.flac", b"ccc", parent="nested", mime="audio/flac")

        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config()))
        assert [i.provider_key for i in items] == ["a", "c"]
        by_key = {i.provider_key: i for i in items}
        assert by_key["a"].display_path == "a.mp3"
        assert by_key["c"].display_path == "nested/c.flac"
        assert by_key["a"].size == 3
        assert by_key["a"].etag == _md5(b"aaa")
        assert by_key["a"].checksum == _md5(b"aaa")

    async def test_non_recursive_listing(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _mkdir(drive_store, "nested", "nested", parent="root")
        _put(drive_store, "a", "a.mp3", b"aaa", parent="root")
        _put(drive_store, "c", "c.flac", b"ccc", parent="nested")

        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config(recursive=False)))
        assert [i.provider_key for i in items] == ["a"]

    async def test_exclude_patterns(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _mkdir(drive_store, "dupes", "dupes", parent="root")
        _put(drive_store, "a", "a.mp3", b"aaa", parent="root")
        _put(drive_store, "b", "b.mp3", b"bbb", parent="dupes")

        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config(exclude=["dupes/*"])))
        assert [i.provider_key for i in items] == ["a"]

    async def test_since_filter(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        old = _now() - timedelta(days=10)
        recent = _now() - timedelta(hours=1)
        _put(drive_store, "old", "old.mp3", b"o", parent="root", mtime=old)
        _put(drive_store, "new", "new.mp3", b"n", parent="root", mtime=recent)

        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config(), since=_now() - timedelta(days=1)))
        assert [i.provider_key for i in items] == ["new"]

    async def test_scope_restricts_to_folder(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _mkdir(drive_store, "rock", "rock", parent="root")
        _mkdir(drive_store, "jazz", "jazz", parent="root")
        _put(drive_store, "a", "a.mp3", b"a", parent="rock")
        _put(drive_store, "b", "b.mp3", b"b", parent="jazz")

        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config(), scope="rock"))
        assert [i.provider_key for i in items] == ["a"]

    async def test_scope_must_be_folder_id(self, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await _collect(adapter.iter_items(_config(), scope="../bad"))

    async def test_google_native_files_skipped(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"aaa", parent="root")
        _put(
            drive_store,
            "doc",
            "doc.mp3",
            b"",
            parent="root",
            mime="application/vnd.google-apps.document",
        )

        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config()))
        assert [i.provider_key for i in items] == ["a"]

    async def test_trashed_files_skipped(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"aaa", parent="root")
        _put(drive_store, "b", "b.mp3", b"bbb", parent="root")
        drive_store["b"]["trashed"] = True

        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config()))
        assert [i.provider_key for i in items] == ["a"]

    async def test_pagination(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _FakeClient.page_size = 2
        for i in range(5):
            _put(drive_store, f"f{i}", f"f{i}.mp3", b"x", parent="root")

        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config()))
        assert sorted(i.provider_key for i in items) == [f"f{i}" for i in range(5)]

    async def test_shared_drive_params(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "drive1", "Team Drive")
        _put(drive_store, "a", "a.mp3", b"aaa", parent="drive1")

        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config(drive_id="drive1")))
        assert [i.provider_key for i in items] == ["a"]
        list_requests = [r for r in _FakeClient.requests if r["params"].get("q")]
        assert list_requests
        assert all(r["params"]["corpora"] == "drive" for r in list_requests)
        assert all(r["params"]["driveId"] == "drive1" for r in list_requests)
        assert all(r["params"]["includeItemsFromAllDrives"] == "true" for r in list_requests)

    async def test_missing_folder_lists_empty(self, drive_store: dict, drive_client: type[_FakeClient]):
        # Drive ``q`` queries against a missing/empty folder simply match
        # nothing, mirroring the real API.
        adapter = GoogleDriveExternalAdapter()
        items = await _collect(adapter.iter_items(_config(root_folder_id="gone")))
        assert items == []


class TestOpenStream:
    async def test_iterator_stream(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"0123456789", parent="root")
        adapter = GoogleDriveExternalAdapter()

        stream = await adapter.open_stream(_config(), _item("a", size=10))
        assert stream.kind == "iterator"
        assert stream.safe_to_redirect is False
        assert stream.supports_range is True
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"0123456789"

    async def test_range_stream(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"0123456789", parent="root")
        adapter = GoogleDriveExternalAdapter()

        stream = await adapter.open_stream(_config(), _item("a", size=10), range=(2, 5))
        assert stream.size == 4
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"2345"

    async def test_stream_url_carries_auth(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"0123456789", parent="root")
        adapter = GoogleDriveExternalAdapter()

        stream = await adapter.open_stream(_oauth_config(), _item("a", size=10))
        assert stream.iterator is not None
        _ = [c async for c in stream.iterator]
        stream_requests = [r for r in _FakeClient.requests if "alt=media" in r["url"]]
        assert stream_requests
        assert stream_requests[-1]["headers"]["Authorization"].startswith("Bearer fake-access-token-")

    async def test_missing_file_raises_not_found(self, drive_store: dict, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        stream = await adapter.open_stream(_config(), _item("gone", size=3))
        assert stream.iterator is not None
        with pytest.raises(ExternalItemNotFound):
            _ = [c async for c in stream.iterator]

    async def test_invalid_key_rejected(self, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalPermissionDenied):
            await adapter.open_stream(_config(), _item("../etc/passwd", size=3))


class TestReadMetadata:
    async def test_reads_tags_from_downloaded_file(
        self,
        drive_store: dict,
        drive_client: type[_FakeClient],
        metadata_mock,
    ):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "song1", "song.mp3", b"audio-bytes", parent="root")
        adapter = GoogleDriveExternalAdapter()

        metadata = await adapter.read_metadata(_config(), _item("song1", display_path="dir/song.mp3"))
        assert metadata.title == "song"
        assert metadata.raw_metadata is not None
        assert metadata.raw_metadata["file_id"] == "song1"
        assert metadata.raw_metadata["display_path"] == "dir/song.mp3"

    async def test_missing_file_raises_not_found(self, drive_store: dict, drive_client: type[_FakeClient]):
        adapter = GoogleDriveExternalAdapter()
        with pytest.raises(ExternalItemNotFound):
            await adapter.read_metadata(_config(), _item("gone"))


class TestComputeSha256:
    async def test_fast_hash_streams_file(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"hashme", parent="root")
        adapter = GoogleDriveExternalAdapter()
        sha = await adapter.compute_sha256(_config(fast_hash=True), _item("a"))
        assert sha == hashlib.sha256(b"hashme").hexdigest()

    async def test_ffmpeg_hash(self, drive_store: dict, drive_client: type[_FakeClient], monkeypatch):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"hashme", parent="root")

        async def _fake_hash(path: Path) -> str:
            assert path.read_bytes() == b"hashme"
            return "f" * 64

        monkeypatch.setattr(storage_service, "audio_hash", _fake_hash)
        adapter = GoogleDriveExternalAdapter()
        sha = await adapter.compute_sha256(_config(), _item("a"))
        assert sha == "f" * 64


class TestWriteMetadata:
    async def test_reuploads_rewritten_file(
        self,
        drive_store: dict,
        drive_client: type[_FakeClient],
        monkeypatch,
    ):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"old", parent="root")

        def _fake_write(path: Path, meta) -> None:
            path.write_bytes(b"new")

        monkeypatch.setattr(metadata_service, "write_metadata", _fake_write)

        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config(allow_write_tags=True))
        result = await adapter.write_metadata(
            _config(allow_write_tags=True),
            _item("a"),
            ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
        )
        assert drive_store["a"]["data"] == b"new"
        assert result.provider_key == "a"
        assert result.etag == _md5(b"new")
        upload_requests = [r for r in _FakeClient.requests if r["url"].startswith(_UPLOAD_BASE)]
        assert upload_requests

    async def test_disabled_raises(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config())
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.write_metadata(
                _config(),
                _item("a"),
                ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
            )


class TestRenameAndDelete:
    async def test_rename_keeps_file_id_and_updates_display_path(
        self, drive_store: dict, drive_client: type[_FakeClient]
    ):
        _mkdir(drive_store, "root", "My Drive")
        _mkdir(drive_store, "dir", "dir", parent="root")
        _put(drive_store, "f1", "old.mp3", b"data", parent="dir")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config(allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(allow_rename_source=True),
            _item("f1", display_path="dir/old.mp3"),
            "new.mp3",
        )
        assert drive_store["f1"]["name"] == "new.mp3"
        assert new_ref.provider_key == "f1"
        assert new_ref.display_path == "dir/new.mp3"

    async def test_rename_same_name_is_noop(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "f1", "song.mp3", b"data", parent="root")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config(allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(allow_rename_source=True),
            _item("f1", display_path="song.mp3"),
            "song.mp3",
        )
        assert new_ref.provider_key == "f1"

    async def test_delete_trashes_by_default(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"data", parent="root")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config(allow_delete_source=True))

        result = await adapter.delete_source(_config(allow_delete_source=True), _item("a"))
        assert "a" in drive_store
        assert drive_store["a"]["trashed"] is True
        assert result.provider_key == "a"

    async def test_delete_permanent_when_trash_disabled(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        _put(drive_store, "a", "a.mp3", b"data", parent="root")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config(allow_delete_source=True))

        await adapter.delete_source(
            _config(allow_delete_source=True, trash_on_delete=False),
            _item("a"),
        )
        assert "a" not in drive_store

    async def test_delete_missing_raises_not_found(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config(allow_delete_source=True))

        with pytest.raises(ExternalItemNotFound):
            await adapter.delete_source(_config(allow_delete_source=True), _item("gone"))

    async def test_delete_disabled_raises(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        adapter = GoogleDriveExternalAdapter()
        await adapter.validate_config(_config())
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.delete_source(_config(), _item("a"))


class TestHealthcheck:
    async def test_healthy(self, drive_store: dict, drive_client: type[_FakeClient]):
        _mkdir(drive_store, "root", "My Drive")
        adapter = GoogleDriveExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is True
        assert "My Drive" in (health.message or "")

    async def test_unreachable(self, drive_client: type[_FakeClient]):
        # Empty store means files.get returns 404.
        adapter = GoogleDriveExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is False
