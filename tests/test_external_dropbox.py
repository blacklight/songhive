"""Tests for the Dropbox external-library adapter.

The adapter is exercised against a small in-memory fake of the Dropbox HTTP
API so no network or credentials are needed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import pytest

import songhive.services.metadata as metadata_service
import songhive.services.storage as storage_service
from songhive.external import _dropbox as dropbox_module
from songhive.external._dropbox import DropboxExternalAdapter
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


def _content_hash(data: bytes) -> str:
    """Emulate Dropbox's content_hash (SHA-256 of 4 MiB block SHA-256s)."""
    blocks = [data[i : i + 4 * 1024 * 1024] for i in range(0, len(data), 4 * 1024 * 1024)] or [b""]
    digests = b"".join(hashlib.sha256(block).digest() for block in blocks)
    return hashlib.sha256(digests).hexdigest()


def _fmt_mtime(mtime: datetime) -> str:
    return mtime.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _put(store: dict, path: str, data: bytes, mtime: Optional[datetime] = None, rev: Optional[str] = None) -> None:
    store[path.lower()] = {
        "kind": "file",
        "path_display": path,
        "data": data,
        "mtime": mtime or _now(),
        "rev": rev or hashlib.sha256(data).hexdigest()[:16],
        "size": len(data),
    }


def _mkdir(store: dict, path: str) -> None:
    store[path.lower()] = {
        "kind": "dir",
        "path_display": path,
    }


def _config(**overrides) -> dict:
    cfg: dict = {
        "access_token": "test-token",
        "root": "Music",
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


def _children(store: dict, dir_key: str, recursive: bool) -> list[tuple[str, dict]]:
    """Return store entries under ``dir_key`` (empty string = account root)."""
    prefix = dir_key + "/" if dir_key else "/"
    children = []
    for key, record in store.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        if not rest or (not recursive and "/" in rest):
            continue
        children.append((key, record))
    return sorted(children, key=lambda t: t[0])


def _entry_for(key: str, record: dict) -> dict:
    """Return a Dropbox API metadata entry for a store record."""
    path_display = record["path_display"]
    name = path_display.rsplit("/", 1)[-1] or "/"
    entry = {
        "name": name,
        "path_lower": path_display.lower(),
        "path_display": path_display,
        "id": f"id:{key}",
    }
    if record["kind"] == "dir":
        entry[".tag"] = "folder"
    else:
        entry[".tag"] = "file"
        entry["server_modified"] = _fmt_mtime(record["mtime"])
        entry["rev"] = record["rev"]
        entry["size"] = record["size"]
        entry["content_hash"] = _content_hash(record["data"])
    return entry


class _FakeResponse:
    """Mimics an httpx ``Response`` returned by ``client.post``."""

    def __init__(self, status: int, payload: Optional[dict] = None, text: str = ""):
        self.status_code = status
        self.is_error = status >= 400
        self._payload = payload
        self.content = json.dumps(payload).encode() if payload is not None else b""
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no JSON payload")
        return self._payload


class _FakeStream:
    """Mimics an httpx ``Response`` returned by ``client.stream``."""

    def __init__(
        self,
        status: int,
        data: bytes = b"",
        error: Optional[dict] = None,
        text: str = "",
    ):
        self.status_code = status
        self.is_error = status >= 400
        self._data = data
        self._error = error
        self.text = text or (json.dumps(error) if error is not None else "")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aread(self) -> bytes:
        return self._data

    async def aiter_bytes(self, chunk_size: int) -> AsyncIterator[bytes]:
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]

    def json(self) -> dict:
        if self._error is None:
            raise ValueError("no JSON payload")
        return self._error


class _FakeClient:
    """In-memory stand-in for the httpx AsyncClient."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.__class__.last_kwargs = kwargs
        self._store = _FAKE_STORE
        self._last_list: dict[str, Any] = {}
        self._sessions: dict[str, bytearray] = {}
        self._session_seq = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bearer(headers: Optional[dict]) -> str:
        auth = (headers or {}).get("Authorization", "")
        return auth.removeprefix("Bearer ").strip()

    def _check_auth(self, headers: Optional[dict]) -> Optional[_FakeResponse]:
        global _REVOKE_NEXT
        token = self._bearer(headers)
        if _REVOKE_NEXT and token in _VALID_TOKENS:
            _REVOKE_NEXT = False
            _VALID_TOKENS.discard(token)
            return _FakeResponse(401, {"error_summary": "invalid_access_token/."})
        if token not in _VALID_TOKENS:
            return _FakeResponse(401, {"error_summary": "invalid_access_token/."})
        return None

    def _check_stream_auth(self, headers: Optional[dict]) -> Optional[_FakeStream]:
        global _REVOKE_NEXT
        token = self._bearer(headers)
        if _REVOKE_NEXT and token in _VALID_TOKENS:
            _REVOKE_NEXT = False
            _VALID_TOKENS.discard(token)
            return _FakeStream(401, error={"error_summary": "invalid_access_token/."})
        if token not in _VALID_TOKENS:
            return _FakeStream(401, error={"error_summary": "invalid_access_token/."})
        return None

    @staticmethod
    def _arg(headers: dict) -> dict:
        return json.loads(headers.get("Dropbox-API-Arg") or "{}")

    def _list_entries(self, body: dict) -> list[dict]:
        key = (body.get("path") or "").lower()
        recursive = bool(body.get("recursive"))
        self._last_list = {
            "key": key,
            "recursive": recursive,
            "limit": int(body.get("limit") or 2000),
        }
        return [_entry_for(k, r) for k, r in _children(self._store, key, recursive)]

    def _page(self, entries: list[dict], offset: int, limit: int) -> _FakeResponse:
        page = entries[offset : offset + limit]
        end = offset + limit
        return _FakeResponse(
            200,
            {
                "entries": page,
                "cursor": str(end),
                "has_more": end < len(entries),
            },
        )

    # ------------------------------------------------------------------
    # Endpoint handlers
    # ------------------------------------------------------------------

    def _oauth_token(self, data: dict) -> _FakeResponse:
        _OAUTH_CALLS.append(dict(data))
        if data.get("grant_type") != "refresh_token" or data.get("refresh_token") == "bad-refresh":
            return _FakeResponse(400, {"error": "invalid_grant"})
        token = f"issued-token-{len(_OAUTH_CALLS)}"
        _VALID_TOKENS.add(token)
        return _FakeResponse(
            200,
            {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": 14400,
                "refresh_token": data.get("refresh_token"),
            },
        )

    def _list_folder(self, body: dict) -> _FakeResponse:
        key = (body.get("path") or "").lower()
        if key:
            record = self._store.get(key)
            if record is None:
                return _FakeResponse(409, {"error_summary": "path/not_found/."})
            if record["kind"] != "dir":
                return _FakeResponse(409, {"error_summary": "path/not_folder/."})
        entries = self._list_entries(body)
        return self._page(entries, 0, int(body.get("limit") or 2000))

    def _list_continue(self, body: dict) -> _FakeResponse:
        try:
            offset = int(body.get("cursor") or 0)
        except (TypeError, ValueError):
            return _FakeResponse(409, {"error_summary": "invalid_cursor/."})
        last = self._last_list
        entries = [_entry_for(k, r) for k, r in _children(self._store, last["key"], last["recursive"])]
        return self._page(entries, offset, last["limit"])

    def _get_metadata(self, body: dict) -> _FakeResponse:
        key = (body.get("path") or "").lower()
        record = self._store.get(key)
        if record is None:
            return _FakeResponse(409, {"error_summary": "path/not_found/."})
        return _FakeResponse(200, _entry_for(key, record))

    def _temporary_link(self, body: dict) -> _FakeResponse:
        key = (body.get("path") or "").lower()
        record = self._store.get(key)
        if record is None or record["kind"] != "file":
            return _FakeResponse(409, {"error_summary": "path/not_found/."})
        return _FakeResponse(
            200,
            {
                "metadata": _entry_for(key, record),
                "link": f"https://dl.dropboxusercontent.com/apitl/1/{record['rev']}",
            },
        )

    def _move(self, body: dict) -> _FakeResponse:
        src = (body.get("from_path") or "").lower()
        dst = (body.get("to_path") or "").lower()
        record = self._store.get(src)
        if record is None:
            return _FakeResponse(409, {"error_summary": "from_lookup/not_found/."})
        if dst in self._store:
            return _FakeResponse(409, {"error_summary": "to/conflict/file/."})
        self._store[dst] = self._store.pop(src)
        record["path_display"] = body["to_path"]
        if record["kind"] == "file":
            record["rev"] = f"{record['rev']}m"
            record["mtime"] = _now()
        return _FakeResponse(200, {"metadata": _entry_for(dst, record)})

    def _delete(self, body: dict) -> _FakeResponse:
        key = (body.get("path") or "").lower()
        record = self._store.pop(key, None)
        if record is None:
            return _FakeResponse(409, {"error_summary": "path/not_found/."})
        return _FakeResponse(200, {"metadata": _entry_for(key, record)})

    def _upload_file(self, headers: dict, data: bytes) -> _FakeResponse:
        arg = self._arg(headers)
        path = arg.get("path") or ""
        if not path:
            return _FakeResponse(400, {"error_summary": "path/malformed_path/."})
        self._store[path.lower()] = {
            "kind": "file",
            "path_display": path,
            "data": data,
            "mtime": _now(),
            "rev": hashlib.sha256(data + b"rev").hexdigest()[:16],
            "size": len(data),
        }
        return _FakeResponse(200, _entry_for(path.lower(), self._store[path.lower()]))

    def _upload_start(self, headers: dict, data: bytes) -> _FakeResponse:
        self._session_seq += 1
        session_id = f"sess-{self._session_seq}"
        self._sessions[session_id] = bytearray(data)
        return _FakeResponse(200, {"session_id": session_id})

    def _upload_append(self, headers: dict, data: bytes) -> _FakeResponse:
        arg = self._arg(headers)
        cursor = arg.get("cursor") or {}
        session = self._sessions.get(cursor.get("session_id") or "")
        if session is None:
            return _FakeResponse(409, {"error_summary": "upload_session_lookup_failed/not_found/."})
        if cursor.get("offset") != len(session):
            return _FakeResponse(409, {"error_summary": "upload_session_offset_error/."})
        session.extend(data)
        return _FakeResponse(200, {})

    def _upload_finish(self, headers: dict, data: bytes) -> _FakeResponse:
        arg = self._arg(headers)
        cursor = arg.get("cursor") or {}
        session = self._sessions.get(cursor.get("session_id") or "")
        if session is None:
            return _FakeResponse(409, {"error_summary": "upload_session_lookup_failed/not_found/."})
        if cursor.get("offset") != len(session):
            return _FakeResponse(409, {"error_summary": "upload_session_offset_error/."})
        session.extend(data)
        commit = arg.get("commit") or {}
        return self._upload_file({"Dropbox-API-Arg": json.dumps(commit)}, bytes(session))

    # ------------------------------------------------------------------
    # httpx surface
    # ------------------------------------------------------------------

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        headers = kwargs.get("headers") or {}

        if url.endswith("/oauth2/token"):
            return self._oauth_token(kwargs.get("data") or {})

        auth_error = self._check_auth(headers)
        if auth_error is not None:
            return auth_error

        if url.endswith("/2/users/get_current_account"):
            return _FakeResponse(
                200,
                {
                    "account_id": "dbid:TEST",
                    "email": "music@example.com",
                    "name": {"display_name": "Music Owner"},
                },
            )
        if url.endswith("/2/files/list_folder/continue"):
            return self._list_continue(kwargs.get("json") or {})
        if url.endswith("/2/files/list_folder"):
            return self._list_folder(kwargs.get("json") or {})
        if url.endswith("/2/files/get_metadata"):
            return self._get_metadata(kwargs.get("json") or {})
        if url.endswith("/2/files/get_temporary_link"):
            return self._temporary_link(kwargs.get("json") or {})
        if url.endswith("/2/files/move_v2"):
            return self._move(kwargs.get("json") or {})
        if url.endswith("/2/files/delete_v2"):
            return self._delete(kwargs.get("json") or {})
        if url.endswith("/2/files/upload_session/start"):
            return self._upload_start(headers, kwargs.get("content") or b"")
        if url.endswith("/2/files/upload_session/append_v2"):
            return self._upload_append(headers, kwargs.get("content") or b"")
        if url.endswith("/2/files/upload_session/finish"):
            return self._upload_finish(headers, kwargs.get("content") or b"")
        if url.endswith("/2/files/upload"):
            return self._upload_file(headers, kwargs.get("content") or b"")

        return _FakeResponse(404, {"error_summary": "unknown_endpoint/."})

    def stream(self, method: str, url: str, **kwargs: Any) -> _FakeStream:
        headers = kwargs.get("headers") or {}

        auth_error = self._check_stream_auth(headers)
        if auth_error is not None:
            return auth_error

        if url.endswith("/2/files/download"):
            arg = self._arg(headers)
            key = (arg.get("path") or "").lower()
            if "noscope" in key:
                return _FakeStream(
                    401,
                    error={
                        "error_summary": "missing_scope/...",
                        "error": {
                            ".tag": "missing_scope",
                            "required_scope": "files.content.read",
                        },
                    },
                )
            if "badarg" in key:
                return _FakeStream(
                    400,
                    text='Error in call to API function "files/download": '
                    'HTTP header "Dropbox-API-Arg" could not be decoded as JSON.',
                )
            record = self._store.get(key)
            if record is None or record["kind"] != "file":
                return _FakeStream(409, error={"error_summary": "path/not_found/."})

            data = record["data"]
            status = 200
            range_header = headers.get("Range")
            if range_header and range_header.startswith("bytes="):
                start_s, end_s = range_header[len("bytes=") :].split("-", 1)
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else len(data) - 1
                data = data[start : end + 1]
                status = 206
            return _FakeStream(status, data)

        return _FakeStream(404, error={"error_summary": "unknown_endpoint/."})


# Module-level state reset per test by the fixture below.
_FAKE_STORE: dict[str, dict] = {}
_VALID_TOKENS: set[str] = set()
_OAUTH_CALLS: list[dict] = []
_REVOKE_NEXT = False


@pytest.fixture
def dropbox_store() -> dict[str, dict]:
    global _REVOKE_NEXT
    _FAKE_STORE.clear()
    _VALID_TOKENS.clear()
    _VALID_TOKENS.add("test-token")
    _OAUTH_CALLS.clear()
    _REVOKE_NEXT = False
    dropbox_module._TOKEN_CACHE.clear()
    return _FAKE_STORE


@pytest.fixture
def dropbox_client(monkeypatch: pytest.MonkeyPatch, dropbox_store: dict[str, dict]) -> type[_FakeClient]:
    def _client(**kwargs):
        return _FakeClient(**kwargs)

    monkeypatch.setattr(dropbox_module.httpx, "AsyncClient", _client)
    return _FakeClient


@pytest.fixture
def metadata_mock(monkeypatch: pytest.MonkeyPatch):
    def _fake(path: Path) -> AudioMetadata:
        return AudioMetadata(title="song", duration=1.0, mimetype="audio/mpeg")

    monkeypatch.setattr(metadata_service, "extract_metadata", _fake)


class TestValidateConfig:
    async def test_returns_capabilities(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        adapter = DropboxExternalAdapter()
        caps = await adapter.validate_config(_config(temporary_links=True))
        assert caps.list_items is True
        assert caps.detect_changes is True
        assert caps.range_read is True
        assert caps.stream_url is True
        assert caps.compute_hash is True
        assert caps.write_tags is False
        assert adapter.capabilities() is caps

    async def test_missing_auth_rejected(self, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(access_token=""))

    async def test_bad_token_rejected(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalPermissionDenied):
            await adapter.validate_config(_config(access_token="wrong-token"))

    async def test_missing_root_rejected(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalConfigError, match="not found"):
            await adapter.validate_config(_config())

    async def test_root_must_be_folder(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _put(dropbox_store, "/Music", b"x")
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalConfigError, match="not a folder"):
            await adapter.validate_config(_config())

    async def test_account_root_needs_no_folder(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        caps = await adapter.validate_config(_config(root=""))
        assert caps.list_items is True

    async def test_client_kwargs_from_config(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config(timeout=60))
        assert dropbox_client.last_kwargs["timeout"].connect == 60.0
        assert dropbox_client.last_kwargs["follow_redirects"] is True

    async def test_capabilities_required_before_use(self, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalLibraryError):
            await adapter.delete_source(_config(), _item("a.mp3"))


class TestRefreshTokenAuth:
    async def test_refresh_grant_exchanges_token(
        self,
        dropbox_store: dict,
        dropbox_client: type[_FakeClient],
    ):
        _mkdir(dropbox_store, "/Music")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(
            _config(access_token=None, refresh_token="refresh-1", app_key="appkey", app_secret="appsecret")
        )
        assert len(_OAUTH_CALLS) == 1
        assert _OAUTH_CALLS[0]["grant_type"] == "refresh_token"
        assert _OAUTH_CALLS[0]["refresh_token"] == "refresh-1"
        assert _OAUTH_CALLS[0]["client_id"] == "appkey"
        assert _OAUTH_CALLS[0]["client_secret"] == "appsecret"
        assert "issued-token-1" in _VALID_TOKENS

    async def test_refresh_token_cached_across_clients(
        self,
        dropbox_store: dict,
        dropbox_client: type[_FakeClient],
    ):
        _mkdir(dropbox_store, "/Music")
        cfg = _config(access_token=None, refresh_token="refresh-1", app_key="appkey")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(cfg)
        await adapter.healthcheck(cfg)
        # healthcheck builds a fresh client/adapter path but reuses the cache.
        assert len(_OAUTH_CALLS) == 1

    async def test_refresh_requires_app_key(self, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(access_token=None, refresh_token="refresh-1"))

    async def test_revoked_cached_token_is_refreshed_and_retried(
        self,
        dropbox_store: dict,
        dropbox_client: type[_FakeClient],
    ):
        global _REVOKE_NEXT
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"aaa")
        cfg = _config(access_token=None, refresh_token="refresh-1", app_key="appkey")

        adapter = DropboxExternalAdapter()
        await adapter.validate_config(cfg)
        assert len(_OAUTH_CALLS) == 1

        # The next authenticated request revokes the cached access token; the
        # adapter must evict it, refresh again, and retry transparently.
        _REVOKE_NEXT = True
        health = await adapter.healthcheck(cfg)
        assert health.ok is True
        assert len(_OAUTH_CALLS) == 2


class TestIterItems:
    async def test_root_and_extension_filter(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"aaa")
        _put(dropbox_store, "/Music/b.txt", b"bbb")
        _mkdir(dropbox_store, "/Music/nested")
        _put(dropbox_store, "/Music/nested/c.flac", b"ccc")
        _put(dropbox_store, "/Other/d.mp3", b"ddd")

        adapter = DropboxExternalAdapter()
        items = await _collect(adapter.iter_items(_config()))
        assert sorted(i.provider_key for i in items) == ["a.mp3", "nested/c.flac"]
        item = next(i for i in items if i.provider_key == "a.mp3")
        assert item.size == 3
        assert item.mime_type == "audio/mpeg"
        assert item.etag is not None
        assert item.checksum == _content_hash(b"aaa")

    async def test_non_recursive_listing(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"aaa")
        _mkdir(dropbox_store, "/Music/nested")
        _put(dropbox_store, "/Music/nested/c.flac", b"ccc")

        adapter = DropboxExternalAdapter()
        items = await _collect(adapter.iter_items(_config(recursive=False)))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_exclude_patterns(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"aaa")
        _mkdir(dropbox_store, "/Music/dupes")
        _put(dropbox_store, "/Music/dupes/b.mp3", b"bbb")

        adapter = DropboxExternalAdapter()
        items = await _collect(adapter.iter_items(_config(exclude=["dupes/*"])))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_since_filter(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        old = _now() - timedelta(days=10)
        recent = _now() - timedelta(hours=1)
        _put(dropbox_store, "/Music/old.mp3", b"o", mtime=old)
        _put(dropbox_store, "/Music/new.mp3", b"n", mtime=recent)

        adapter = DropboxExternalAdapter()
        items = await _collect(adapter.iter_items(_config(), since=_now() - timedelta(days=1)))
        assert [i.provider_key for i in items] == ["new.mp3"]

    async def test_scope_restricts_root(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _mkdir(dropbox_store, "/Music/rock")
        _mkdir(dropbox_store, "/Music/jazz")
        _put(dropbox_store, "/Music/rock/a.mp3", b"a")
        _put(dropbox_store, "/Music/jazz/b.mp3", b"b")

        adapter = DropboxExternalAdapter()
        items = await _collect(adapter.iter_items(_config(), scope="rock"))
        assert [i.provider_key for i in items] == ["rock/a.mp3"]

    async def test_pagination_follows_cursor(
        self,
        dropbox_store: dict,
        dropbox_client: type[_FakeClient],
        monkeypatch: pytest.MonkeyPatch,
    ):
        _mkdir(dropbox_store, "/Music")
        for i in range(5):
            _put(dropbox_store, f"/Music/track{i}.mp3", b"x")

        monkeypatch.setattr(dropbox_module, "_LIST_PAGE_SIZE", 2)
        adapter = DropboxExternalAdapter()
        items = await _collect(adapter.iter_items(_config()))
        assert sorted(i.provider_key for i in items) == [f"track{i}.mp3" for i in range(5)]

    async def test_list_error_wrapped(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalConfigError, match="not found"):
            await _collect(adapter.iter_items(_config()))


class TestOpenStream:
    async def test_temporary_link_stream(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"0123456789")
        adapter = DropboxExternalAdapter()

        stream = await adapter.open_stream(_config(temporary_links=True), _item("a.mp3", size=10))
        assert stream.kind == "url"
        assert stream.safe_to_redirect is True
        assert stream.temporary is True
        assert stream.supports_range is True
        assert stream.url is not None
        assert stream.url.startswith("https://dl.dropboxusercontent.com/")
        assert stream.size == 10

    async def test_temporary_link_missing_file(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalItemNotFound):
            await adapter.open_stream(_config(temporary_links=True), _item("gone.mp3"))

    async def test_iterator_stream(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"0123456789")
        adapter = DropboxExternalAdapter()

        stream = await adapter.open_stream(_config(temporary_links=False), _item("a.mp3", size=10))
        assert stream.kind == "iterator"
        assert stream.safe_to_redirect is False
        assert stream.supports_range is True
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"0123456789"

    async def test_range_stream(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"0123456789")
        adapter = DropboxExternalAdapter()

        stream = await adapter.open_stream(_config(temporary_links=False), _item("a.mp3", size=10), range=(2, 5))
        assert stream.size == 4
        assert stream.content_range == "bytes 2-5/10"
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"2345"

    async def test_traversal_key_rejected(self, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalPermissionDenied):
            await adapter.open_stream(_config(), _item("../etc/passwd", size=3))


class TestReadMetadata:
    async def test_reads_tags_from_downloaded_file(
        self,
        dropbox_store: dict,
        dropbox_client: type[_FakeClient],
        metadata_mock,
    ):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/song.mp3", b"audio-bytes")
        adapter = DropboxExternalAdapter()

        metadata = await adapter.read_metadata(_config(), _item("song.mp3", etag="rev1"))
        assert metadata.title == "song"
        assert metadata.raw_metadata is not None
        assert metadata.raw_metadata["path"] == "/Music/song.mp3"
        assert metadata.raw_metadata["rev"] == "rev1"

    async def test_missing_file_raises_not_found(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalItemNotFound):
            await adapter.read_metadata(_config(), _item("gone.mp3"))

    async def test_bad_request_surfaces_plaintext_reason(self, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalConfigError, match="could not be decoded as JSON"):
            await adapter.read_metadata(_config(), _item("badarg.mp3"))

    async def test_missing_scope_surfaces_required_scope(self, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        with pytest.raises(ExternalPermissionDenied, match="files.content.read"):
            await adapter.read_metadata(_config(), _item("noscope.mp3"))


class TestComputeSha256:
    async def test_fast_hash_streams_file(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"hashme")
        adapter = DropboxExternalAdapter()
        sha = await adapter.compute_sha256(_config(fast_hash=True), _item("a.mp3"))
        assert sha == hashlib.sha256(b"hashme").hexdigest()

    async def test_ffmpeg_hash(self, dropbox_store: dict, dropbox_client: type[_FakeClient], monkeypatch):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"hashme")

        async def _fake_hash(path: Path) -> str:
            assert path.read_bytes() == b"hashme"
            return "f" * 64

        monkeypatch.setattr(storage_service, "audio_hash", _fake_hash)
        adapter = DropboxExternalAdapter()
        sha = await adapter.compute_sha256(_config(), _item("a.mp3"))
        assert sha == "f" * 64


class TestWriteMetadata:
    async def test_reuploads_rewritten_file(
        self,
        dropbox_store: dict,
        dropbox_client: type[_FakeClient],
        monkeypatch,
    ):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"old")

        def _fake_write(path: Path, meta) -> None:
            path.write_bytes(b"new")

        monkeypatch.setattr(metadata_service, "write_metadata", _fake_write)

        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config(allow_write_tags=True))
        result = await adapter.write_metadata(
            _config(allow_write_tags=True),
            _item("a.mp3"),
            ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
        )
        assert dropbox_store["/music/a.mp3"]["data"] == b"new"
        assert result.provider_key == "a.mp3"
        assert result.etag is not None
        assert result.checksum == _content_hash(b"new")

    async def test_disabled_raises(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config())
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.write_metadata(
                _config(),
                _item("a.mp3"),
                ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
            )

    async def test_large_file_uses_upload_session(
        self,
        dropbox_store: dict,
        dropbox_client: type[_FakeClient],
        monkeypatch,
    ):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/big.flac", b"old")

        def _fake_write(path: Path, meta) -> None:
            path.write_bytes(b"n" * 35)

        monkeypatch.setattr(metadata_service, "write_metadata", _fake_write)
        monkeypatch.setattr(dropbox_module, "_UPLOAD_SESSION_THRESHOLD", 20)
        monkeypatch.setattr(dropbox_module, "_UPLOAD_CHUNK", 10)

        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config(allow_write_tags=True))
        result = await adapter.write_metadata(
            _config(allow_write_tags=True),
            _item("big.flac"),
            ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
        )
        # 35 bytes > 20-byte threshold -> session start + 2 appends + finish.
        assert dropbox_store["/music/big.flac"]["data"] == b"n" * 35
        assert result.provider_key == "big.flac"


class TestRenameAndDelete:
    async def test_rename_moves_file(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _mkdir(dropbox_store, "/Music/dir")
        _put(dropbox_store, "/Music/dir/old.mp3", b"data")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config(allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(allow_rename_source=True),
            _item("dir/old.mp3"),
            "new.mp3",
        )
        assert "/music/dir/old.mp3" not in dropbox_store
        assert dropbox_store["/music/dir/new.mp3"]["data"] == b"data"
        assert new_ref.provider_key == "dir/new.mp3"
        assert new_ref.size == 4

    async def test_rename_existing_target_denied(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/old.mp3", b"a")
        _put(dropbox_store, "/Music/new.mp3", b"b")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config(allow_rename_source=True))

        with pytest.raises(ExternalPermissionDenied):
            await adapter.rename_source(_config(allow_rename_source=True), _item("old.mp3"), "new.mp3")

    async def test_rename_same_name_is_noop(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/dir/song.mp3", b"data")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config(allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(allow_rename_source=True),
            _item("dir/song.mp3"),
            "song.mp3",
        )
        assert new_ref.provider_key == "dir/song.mp3"

    async def test_delete_removes_file(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        _put(dropbox_store, "/Music/a.mp3", b"data")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config(allow_delete_source=True))

        result = await adapter.delete_source(_config(allow_delete_source=True), _item("a.mp3"))
        assert "/music/a.mp3" not in dropbox_store
        assert result.provider_key == "a.mp3"

    async def test_delete_missing_raises_not_found(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config(allow_delete_source=True))

        with pytest.raises(ExternalItemNotFound):
            await adapter.delete_source(_config(allow_delete_source=True), _item("gone.mp3"))

    async def test_delete_disabled_raises(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        adapter = DropboxExternalAdapter()
        await adapter.validate_config(_config())
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.delete_source(_config(), _item("a.mp3"))


class TestHealthcheck:
    async def test_healthy(self, dropbox_store: dict, dropbox_client: type[_FakeClient]):
        _mkdir(dropbox_store, "/Music")
        adapter = DropboxExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is True
        assert "Music" in (health.message or "")
        assert "music@example.com" in (health.message or "")

    async def test_unreachable(self, dropbox_client: type[_FakeClient]):
        # Empty store means get_metadata on the root returns path/not_found.
        adapter = DropboxExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is False

    async def test_bad_token_unhealthy(self, dropbox_client: type[_FakeClient]):
        adapter = DropboxExternalAdapter()
        health = await adapter.healthcheck(_config(access_token="wrong-token"))
        assert health.ok is False
