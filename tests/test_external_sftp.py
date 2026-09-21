"""Tests for the SFTP external-library adapter.

The adapter is exercised against a small in-memory fake of the asyncssh
connection/SFTP client so no network or credentials are needed.
"""

from __future__ import annotations

import hashlib
import posixpath
import stat as stat_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import asyncssh
import pytest

import songhive.services.metadata as metadata_service
import songhive.services.storage as storage_service
from songhive.external import _sftp as sftp_module
from songhive.external._sftp import SFTPExternalAdapter
from songhive.external.errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from songhive.external.types import ExternalItemRef, ExternalTrackMetadata
from songhive.services.metadata import AudioMetadata

_FILE_MODE = stat_module.S_IFREG | 0o644
_DIR_MODE = stat_module.S_IFDIR | 0o755
_LINK_MODE = stat_module.S_IFLNK | 0o777


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _attrs(record: dict) -> asyncssh.SFTPAttrs:
    """Build SFTP attrs for a fake filesystem record."""
    kind = record["kind"]
    mode = {"file": _FILE_MODE, "dir": _DIR_MODE, "link": _LINK_MODE}[kind]
    data = record.get("data")
    return asyncssh.SFTPAttrs(
        size=len(data) if data is not None else None,
        permissions=mode,
        mtime=record["mtime"].timestamp(),
    )


class _FakeSFTPFile:
    """Mimics asyncssh's SFTPClientFile for remote reads."""

    def __init__(self, store: dict, path: str):
        self._store = store
        self._path = path
        self.closed = False

    async def read(self, size: int = -1, offset: Optional[int] = None) -> bytes:
        data = self._store[self._path]["data"]
        pos = offset if offset is not None else 0
        return data[pos:] if size < 0 else data[pos : pos + size]

    async def close(self) -> None:
        self.closed = True


class _FakeSFTPClient:
    """In-memory stand-in for the asyncssh SFTP client."""

    def __init__(self, store: dict):
        self._store = store
        self.exited = False

    def _resolve(self, path: str, *, follow_symlinks: bool = True) -> str:
        path = posixpath.normpath(path)
        record = self._store.get(path)
        while follow_symlinks and record is not None and record["kind"] == "link":
            path = posixpath.normpath(posixpath.join(posixpath.dirname(path), record["target"]))
            record = self._store.get(path)
        return path

    def _record(self, path: str, *, follow_symlinks: bool = True) -> dict:
        resolved = self._resolve(path, follow_symlinks=follow_symlinks)
        record = self._store.get(resolved)
        if record is None:
            raise asyncssh.SFTPNoSuchFile(f"No such file: {path}")
        return record

    async def stat(self, path: str, *, follow_symlinks: bool = True) -> asyncssh.SFTPAttrs:
        return _attrs(self._record(path, follow_symlinks=follow_symlinks))

    async def readdir(self, path: str) -> list:
        path = posixpath.normpath(path)
        record = self._record(path)
        if record["kind"] != "dir":
            raise asyncssh.SFTPNoSuchFile(f"No such directory: {path}")

        entries = []
        prefix = "" if path in (".", "/") else path.rstrip("/") + "/"
        for candidate, rec in self._store.items():
            if not candidate.startswith(prefix):
                continue
            rest = candidate[len(prefix) :]
            if "/" in rest or not rest:
                continue
            # Listing attributes follow lstat semantics: links stay links.
            entries.append(asyncssh.SFTPName(rest, rest, _attrs(rec)))
        return entries

    async def open(self, path: str, mode: str = "r") -> _FakeSFTPFile:
        resolved = self._resolve(path)
        record = self._store.get(resolved)
        if record is None or record["kind"] != "file":
            raise asyncssh.SFTPNoSuchFile(f"No such file: {path}")
        return _FakeSFTPFile(self._store, resolved)

    async def get(self, remotepath: str, localpath: str) -> None:
        handle = await self.open(remotepath, "rb")
        Path(localpath).write_bytes(handle._store[handle._path]["data"])

    async def put(self, localpath: str, remotepath: str) -> None:
        data = Path(localpath).read_bytes()
        self._store[posixpath.normpath(remotepath)] = {
            "kind": "file",
            "data": data,
            "mtime": _now(),
        }

    async def rename(self, old: str, new: str) -> None:
        old = posixpath.normpath(old)
        new = posixpath.normpath(new)
        if old not in self._store:
            raise asyncssh.SFTPNoSuchFile(f"No such file: {old}")
        self._store[new] = self._store.pop(old)

    async def remove(self, path: str) -> None:
        path = posixpath.normpath(path)
        if self._store.pop(path, None) is None:
            raise asyncssh.SFTPNoSuchFile(f"No such file: {path}")

    async def exit(self) -> None:
        self.exited = True


class _FakeSSHConnection:
    """Mimics asyncssh's SSHClientConnection."""

    def __init__(self, sftp: _FakeSFTPClient):
        self._sftp = sftp

    async def start_sftp_client(self) -> _FakeSFTPClient:
        return self._sftp


class _FakeConnectContext:
    """Mimics the awaitable/async-context-manager returned by asyncssh.connect."""

    def __init__(self, conn: _FakeSSHConnection):
        self._conn = conn

    async def __aenter__(self) -> _FakeSSHConnection:
        return self._conn

    async def __aexit__(self, *args) -> bool:
        return False

    def __await__(self):
        async def _conn():
            return self._conn

        return _conn().__await__()


def _put(store: dict, path: str, data: bytes, mtime: Optional[datetime] = None) -> None:
    store[posixpath.normpath(path)] = {"kind": "file", "data": data, "mtime": mtime or _now()}


def _mkdir(store: dict, path: str, mtime: Optional[datetime] = None) -> None:
    store[posixpath.normpath(path)] = {"kind": "dir", "data": None, "mtime": mtime or _now()}


def _symlink(store: dict, path: str, target: str) -> None:
    store[posixpath.normpath(path)] = {"kind": "link", "target": target, "mtime": _now()}


async def _collect(aiter):
    return [item async for item in aiter]


def _config(**overrides) -> dict:
    cfg: dict = {
        "host": "nas.local",
        "username": "music",
        "password": "hunter2",
        # Explicit opt-out: the fake connect has no real host key to verify.
        "verify_host_key": False,
    }
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
def sftp_store() -> dict:
    return {"/music": {"kind": "dir", "data": None, "mtime": _now()}}


@pytest.fixture
def sftp_client(monkeypatch: pytest.MonkeyPatch, sftp_store: dict) -> _FakeSFTPClient:
    client = _FakeSFTPClient(sftp_store)
    conn = _FakeSSHConnection(client)

    def _connect(**kwargs):
        client.connect_kwargs = kwargs
        if getattr(client, "deny_auth", False):
            raise asyncssh.PermissionDenied("auth", "permission denied")
        if getattr(client, "deny_connect", False):
            raise OSError("connection refused")
        return _FakeConnectContext(conn)

    monkeypatch.setattr(sftp_module.asyncssh, "connect", _connect)
    return client


@pytest.fixture
def metadata_mock(monkeypatch: pytest.MonkeyPatch):
    def _fake(path: Path) -> AudioMetadata:
        return AudioMetadata(title="song", duration=1.0, mimetype="audio/mpeg")

    monkeypatch.setattr(metadata_service, "extract_metadata", _fake)


class TestValidateConfig:
    async def test_returns_capabilities(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        caps = await adapter.validate_config(_config(root="/music"))
        assert caps.list_items is True
        assert caps.detect_changes is True
        assert caps.range_read is True
        assert caps.stream_url is False
        assert caps.compute_hash is True
        assert caps.write_tags is False
        assert adapter.capabilities() is caps

    async def test_missing_host_rejected(self, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(host="  "))

    async def test_missing_username_rejected(self, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(username=""))

    async def test_invalid_port_rejected(self, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalConfigError):
            await adapter.validate_config(_config(port="not-a-port"))

    async def test_auth_failure_wrapped(self, sftp_client: _FakeSFTPClient):
        sftp_client.deny_auth = True
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalConfigError, match="Authentication failed"):
            await adapter.validate_config(_config())

    async def test_connect_failure_wrapped(self, sftp_client: _FakeSFTPClient):
        sftp_client.deny_connect = True
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalConfigError, match="Cannot connect"):
            await adapter.validate_config(_config())

    async def test_root_must_be_directory(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music.txt", b"x")
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalConfigError, match="not a directory"):
            await adapter.validate_config(_config(root="/music.txt"))

    async def test_missing_root_rejected(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalConfigError, match="Cannot access remote root"):
            await adapter.validate_config(_config(root="/gone"))

    async def test_connect_kwargs_from_config(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music", port=2222))
        assert sftp_client.connect_kwargs["host"] == "nas.local"
        assert sftp_client.connect_kwargs["port"] == 2222
        assert sftp_client.connect_kwargs["username"] == "music"
        assert sftp_client.connect_kwargs["password"] == "hunter2"
        assert sftp_client.connect_kwargs["known_hosts"] is None

    async def test_private_key_auth(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        key = asyncssh.generate_private_key("ssh-ed25519")
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music", password=None, private_key=key.export_private_key()))
        assert "client_keys" in sftp_client.connect_kwargs
        assert "password" not in sftp_client.connect_kwargs

    async def test_invalid_private_key_rejected(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalConfigError, match="private_key"):
            await adapter.validate_config(_config(private_key="not a key"))

    async def test_verify_host_key_parses_known_hosts(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        host_key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music", verify_host_key=True, known_hosts=f"nas.local {host_key}"))
        assert isinstance(sftp_client.connect_kwargs["known_hosts"], asyncssh.SSHKnownHosts)

    async def test_host_key_verification_on_by_default(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        """Without an explicit opt-out the adapter must not disable host-key checks."""
        adapter = SFTPExternalAdapter()
        config = _config(root="/music")
        del config["verify_host_key"]
        await adapter.validate_config(config)
        # asyncssh falls back to its default known-hosts files rather than None.
        assert "known_hosts" not in sftp_client.connect_kwargs

    async def test_capabilities_required_before_use(self, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        item = _item("a.mp3")
        with pytest.raises(ExternalLibraryError):
            await adapter.delete_source(_config(), item)


class TestIterItems:
    async def test_root_and_extension_filter(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/a.mp3", b"aaa")
        _put(sftp_store, "/music/b.txt", b"bbb")
        _put(sftp_store, "/music/nested/c.flac", b"ccc")
        _mkdir(sftp_store, "/music/nested")
        _put(sftp_store, "/other/d.mp3", b"ddd")
        _mkdir(sftp_store, "/other")

        adapter = SFTPExternalAdapter()
        items = await _collect(adapter.iter_items(_config(root="/music")))
        assert sorted(i.provider_key for i in items) == ["a.mp3", "nested/c.flac"]
        item = next(i for i in items if i.provider_key == "a.mp3")
        assert item.size == 3
        assert item.mime_type == "audio/mpeg"
        assert item.etag is not None

    async def test_non_recursive_listing(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/a.mp3", b"aaa")
        _mkdir(sftp_store, "/music/nested")
        _put(sftp_store, "/music/nested/c.flac", b"ccc")

        adapter = SFTPExternalAdapter()
        items = await _collect(adapter.iter_items(_config(root="/music", recursive=False)))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_exclude_patterns(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/a.mp3", b"aaa")
        _mkdir(sftp_store, "/music/dupes")
        _put(sftp_store, "/music/dupes/b.mp3", b"bbb")

        adapter = SFTPExternalAdapter()
        items = await _collect(adapter.iter_items(_config(root="/music", exclude=["dupes/*"])))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_since_filter(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        old = _now() - timedelta(days=10)
        recent = _now() - timedelta(hours=1)
        _put(sftp_store, "/music/old.mp3", b"o", mtime=old)
        _put(sftp_store, "/music/new.mp3", b"n", mtime=recent)

        adapter = SFTPExternalAdapter()
        items = await _collect(adapter.iter_items(_config(root="/music"), since=_now() - timedelta(days=1)))
        assert [i.provider_key for i in items] == ["new.mp3"]

    async def test_scope_restricts_root(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _mkdir(sftp_store, "/music/rock")
        _mkdir(sftp_store, "/music/jazz")
        _put(sftp_store, "/music/rock/a.mp3", b"a")
        _put(sftp_store, "/music/jazz/b.mp3", b"b")

        adapter = SFTPExternalAdapter()
        items = await _collect(adapter.iter_items(_config(root="/music"), scope="rock"))
        assert [i.provider_key for i in items] == ["rock/a.mp3"]

    async def test_symlinks_skipped_by_default(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/a.mp3", b"aaa")
        _symlink(sftp_store, "/music/link.mp3", "a.mp3")

        adapter = SFTPExternalAdapter()
        items = await _collect(adapter.iter_items(_config(root="/music")))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_symlinks_followed_when_enabled(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/a.mp3", b"aaa")
        _symlink(sftp_store, "/music/link.mp3", "a.mp3")

        adapter = SFTPExternalAdapter()
        items = await _collect(adapter.iter_items(_config(root="/music", follow_symlinks=True)))
        assert sorted(i.provider_key for i in items) == ["a.mp3", "link.mp3"]

    async def test_relative_root_supported(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _mkdir(sftp_store, "music")
        _put(sftp_store, "music/a.mp3", b"aaa")

        adapter = SFTPExternalAdapter()
        items = await _collect(adapter.iter_items(_config(root="music")))
        assert [i.provider_key for i in items] == ["a.mp3"]

    async def test_missing_root_wrapped(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalItemNotFound):
            await _collect(adapter.iter_items(_config(root="/gone")))


class TestOpenStream:
    async def test_iterator_stream(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/a.mp3", b"0123456789")
        adapter = SFTPExternalAdapter()

        stream = await adapter.open_stream(_config(root="/music"), _item("a.mp3", size=10))
        assert stream.kind == "iterator"
        assert stream.safe_to_redirect is False
        assert stream.supports_range is True
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"0123456789"

    async def test_range_stream(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/a.mp3", b"0123456789")
        adapter = SFTPExternalAdapter()

        stream = await adapter.open_stream(
            _config(root="/music"),
            _item("a.mp3", size=10),
            range=(2, 5),
        )
        assert stream.size == 4
        assert stream.iterator is not None
        chunks = [c async for c in stream.iterator]
        assert b"".join(chunks) == b"2345"

    async def test_missing_file_raises_not_found(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        stream = await adapter.open_stream(_config(root="/music"), _item("gone.mp3"))
        assert stream.iterator is not None
        with pytest.raises(ExternalItemNotFound):
            async for _ in stream.iterator:
                pass

    async def test_traversal_key_rejected(self, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalPermissionDenied):
            await adapter.open_stream(_config(), _item("../etc/passwd", size=3))


class TestReadMetadata:
    async def test_reads_tags_from_downloaded_file(
        self,
        sftp_store: dict,
        sftp_client: _FakeSFTPClient,
        metadata_mock,
    ):
        _put(sftp_store, "/music/song.mp3", b"audio-bytes")
        adapter = SFTPExternalAdapter()

        metadata = await adapter.read_metadata(_config(root="/music"), _item("song.mp3"))
        assert metadata.title == "song"
        assert metadata.raw_metadata is not None
        assert metadata.raw_metadata["path"] == "/music/song.mp3"

    async def test_missing_file_raises_not_found(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        with pytest.raises(ExternalItemNotFound):
            await adapter.read_metadata(_config(), _item("gone.mp3"))


class TestComputeSha256:
    async def test_fast_hash_streams_file(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/a.mp3", b"hashme")
        adapter = SFTPExternalAdapter()
        sha = await adapter.compute_sha256(_config(root="/music", fast_hash=True), _item("a.mp3"))
        assert sha == hashlib.sha256(b"hashme").hexdigest()

    async def test_ffmpeg_hash(self, sftp_store: dict, sftp_client: _FakeSFTPClient, monkeypatch):
        _put(sftp_store, "/music/a.mp3", b"hashme")

        async def _fake_hash(path: Path) -> str:
            assert path.read_bytes() == b"hashme"
            return "f" * 64

        monkeypatch.setattr(storage_service, "audio_hash", _fake_hash)
        adapter = SFTPExternalAdapter()
        sha = await adapter.compute_sha256(_config(root="/music", allow_hashing=True), _item("a.mp3"))
        assert sha == "f" * 64


class TestWriteMetadata:
    async def test_reuploads_rewritten_file(
        self,
        sftp_store: dict,
        sftp_client: _FakeSFTPClient,
        monkeypatch,
    ):
        _put(sftp_store, "/music/a.mp3", b"old")

        def _fake_write(path: Path, meta) -> None:
            path.write_bytes(b"new")

        monkeypatch.setattr(metadata_service, "write_metadata", _fake_write)

        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music", allow_write_tags=True))
        result = await adapter.write_metadata(
            _config(root="/music", allow_write_tags=True),
            _item("a.mp3"),
            ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
        )
        assert sftp_store["/music/a.mp3"]["data"] == b"new"
        assert result.provider_key == "a.mp3"

    async def test_disabled_raises(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music"))
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.write_metadata(
                _config(),
                _item("a.mp3"),
                ExternalTrackMetadata(title="T", artist="", album="", album_artist=""),
            )


class TestRenameAndDelete:
    async def test_rename_moves_file(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/dir/old.mp3", b"data")
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music", allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(root="/music", allow_rename_source=True),
            _item("dir/old.mp3"),
            "new.mp3",
        )
        assert "/music/dir/old.mp3" not in sftp_store
        assert sftp_store["/music/dir/new.mp3"]["data"] == b"data"
        assert new_ref.provider_key == "dir/new.mp3"
        assert new_ref.size == 4

    async def test_rename_existing_target_denied(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/old.mp3", b"a")
        _put(sftp_store, "/music/new.mp3", b"b")
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music", allow_rename_source=True))

        with pytest.raises(ExternalPermissionDenied):
            await adapter.rename_source(_config(root="/music", allow_rename_source=True), _item("old.mp3"), "new.mp3")

    async def test_rename_same_name_is_noop(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/dir/song.mp3", b"data")
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music", allow_rename_source=True))

        new_ref = await adapter.rename_source(
            _config(root="/music", allow_rename_source=True),
            _item("dir/song.mp3"),
            "song.mp3",
        )
        assert new_ref.provider_key == "dir/song.mp3"

    async def test_delete_removes_file(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        _put(sftp_store, "/music/a.mp3", b"data")
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music", allow_delete_source=True))

        result = await adapter.delete_source(_config(root="/music", allow_delete_source=True), _item("a.mp3"))
        assert "/music/a.mp3" not in sftp_store
        assert result.provider_key == "a.mp3"

    async def test_delete_missing_raises_not_found(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music", allow_delete_source=True))

        with pytest.raises(ExternalItemNotFound):
            await adapter.delete_source(_config(root="/music", allow_delete_source=True), _item("gone.mp3"))

    async def test_delete_disabled_raises(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        await adapter.validate_config(_config(root="/music"))
        with pytest.raises(UnsupportedExternalOperation):
            await adapter.delete_source(_config(), _item("a.mp3"))


class TestHealthcheck:
    async def test_healthy(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        health = await adapter.healthcheck(_config(root="/music"))
        assert health.ok is True
        assert "nas.local" in (health.message or "")

    async def test_unreachable(self, sftp_client: _FakeSFTPClient):
        sftp_client.deny_connect = True
        adapter = SFTPExternalAdapter()
        health = await adapter.healthcheck(_config())
        assert health.ok is False

    async def test_missing_root_unhealthy(self, sftp_store: dict, sftp_client: _FakeSFTPClient):
        adapter = SFTPExternalAdapter()
        health = await adapter.healthcheck(_config(root="/gone"))
        assert health.ok is False
