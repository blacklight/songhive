"""
SFTP/SSH external-library adapter.

Indexes audio files stored on a remote host reached over SFTP and serves
them through proxied streams. The remote host must be reachable from the
Songhive instance itself — all listing, download, and mutation traffic
originates server-side.

Because a remote filesystem cannot be watched like a local directory, change
detection relies on the ``mtime``/``size`` pair reported by directory
listings: scheduled syncs walk the configured root (a cheap metadata-only
operation) and only download files whose identity changed, so unchanged
trees never incur per-file data transfer.
"""

import asyncio
import dataclasses
import fnmatch
import hashlib
import logging
import mimetypes
import os
import posixpath
import stat as stat_module
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Optional, cast

import aiofiles
import aiofiles.os
import asyncssh

from ..config.constants import AUDIO_EXTENSIONS
from ..config.loader import load_config
from .base import ExternalLibraryAdapter
from .errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from .types import (
    ExternalHealth,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalMutationResult,
    ExternalStream,
    ExternalTrackMetadata,
)

logger = logging.getLogger(__name__)

_DEFAULT_EXTENSIONS = frozenset(AUDIO_EXTENSIONS)
_CHUNK_SIZE = 64 * 1024
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 15

# Cap concurrent ffmpeg invocations across all SFTP library syncs.
_FFMPEG_SEMAPHORE = asyncio.Semaphore(2)


class SFTPExternalAdapter(ExternalLibraryAdapter):
    """Adapter that indexes audio files from a remote SFTP/SSH host."""

    provider_type = "sftp"
    user_configurable = True

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _host(config: dict) -> str:
        host = config.get("host")
        if not isinstance(host, str) or not host.strip():
            raise ExternalConfigError(
                'config["host"] is required and must be a non-empty string',
                field="host",
            )
        return host.strip()

    @staticmethod
    def _port(config: dict) -> int:
        raw = config.get("port", 22)
        try:
            port = int(raw)
        except (TypeError, ValueError) as exc:
            raise ExternalConfigError(
                'config["port"] must be an integer',
                field="port",
            ) from exc
        if not 1 <= port <= 65535:
            raise ExternalConfigError(
                'config["port"] must be between 1 and 65535',
                field="port",
            )
        return port

    @staticmethod
    def _username(config: dict) -> str:
        username = config.get("username")
        if not isinstance(username, str) or not username.strip():
            raise ExternalConfigError(
                'config["username"] is required and must be a non-empty string',
                field="username",
            )
        return username.strip()

    @staticmethod
    def _root(config: dict) -> str:
        """Return the normalized remote root directory (POSIX semantics)."""
        raw = config.get("root") or "."
        if not isinstance(raw, str):
            raise ExternalConfigError(
                'config["root"] must be a string',
                field="root",
            )
        root = posixpath.normpath(raw.strip() or ".")
        return root

    @staticmethod
    def _connect_timeout(config: dict) -> int:
        raw = config.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT_SECONDS)
        try:
            timeout = int(raw)
        except (TypeError, ValueError) as exc:
            raise ExternalConfigError(
                'config["connect_timeout"] must be an integer',
                field="connect_timeout",
            ) from exc
        if timeout <= 0:
            raise ExternalConfigError(
                'config["connect_timeout"] must be positive',
                field="connect_timeout",
            )
        return timeout

    def _connect_kwargs(self, config: dict) -> dict:
        """Build keyword arguments for ``asyncssh.connect``."""
        timeout = self._connect_timeout(config)
        kwargs: dict[str, Any] = {
            "host": self._host(config),
            "port": self._port(config),
            "username": self._username(config),
            "connect_timeout": timeout,
            "login_timeout": timeout,
        }

        private_key = config.get("private_key") or None
        passphrase = config.get("private_key_passphrase") or None
        if private_key is not None:
            if not isinstance(private_key, (str, bytes)):
                raise ExternalConfigError(
                    'config["private_key"] must be a PEM/OpenSSH private key string',
                    field="private_key",
                )
            try:
                kwargs["client_keys"] = [
                    asyncssh.import_private_key(
                        private_key,
                        passphrase=passphrase if passphrase else None,
                    )
                ]
            except (asyncssh.KeyImportError, ValueError) as exc:
                raise ExternalConfigError(
                    f'config["private_key"] is not a usable private key: {exc}',
                    field="private_key",
                ) from exc

        password = config.get("password") or None
        if password is not None:
            kwargs["password"] = str(password)

        # With neither private_key nor password, asyncssh falls back to the
        # default client keys and agent of the Songhive process, mirroring
        # the S3 adapter's ambient-credentials behaviour.

        # Host-key verification stays on unless explicitly disabled: without
        # it the SSH connection (and any password sent over it) is open to
        # MITM impersonation.
        if bool(config.get("verify_host_key", True)):
            known_hosts = config.get("known_hosts")
            if known_hosts:
                if not isinstance(known_hosts, str):
                    raise ExternalConfigError(
                        'config["known_hosts"] must be a string',
                        field="known_hosts",
                    )
                try:
                    kwargs["known_hosts"] = asyncssh.import_known_hosts(known_hosts)
                except (ValueError, TypeError) as exc:
                    raise ExternalConfigError(
                        f'config["known_hosts"] could not be parsed: {exc}',
                        field="known_hosts",
                    ) from exc
            # Without explicit known_hosts data, asyncssh reads the default
            # known-hosts files of the Songhive process.
        else:
            kwargs["known_hosts"] = None

        return kwargs

    @asynccontextmanager
    async def _connect(self, config: dict) -> AsyncIterator[asyncssh.SFTPClient]:
        """Open an SSH connection and yield a started SFTP client."""
        async with asyncssh.connect(**self._connect_kwargs(config)) as conn:
            sftp = await conn.start_sftp_client()
            try:
                yield sftp
            finally:
                try:
                    sftp.exit()
                except (OSError, asyncssh.Error):
                    pass

    @staticmethod
    def _scope_root(config: dict, scope: Optional[str]) -> tuple[str, str]:
        """Return ``(remote_dir, root_relative_scope)`` for a scoped sync."""
        root = SFTPExternalAdapter._root(config)
        if not scope:
            return root, ""
        clean = scope.strip().strip("/")
        parts = PurePosixPath(clean).parts if clean else ()
        if ".." in parts:
            raise ExternalConfigError(
                f"Scope {scope!r} is invalid",
                field="scope",
            )
        return (posixpath.join(root, clean), clean) if clean else (root, "")

    def _full_path(self, config: dict, provider_key: str) -> str:
        """Join a provider-relative key with the configured remote root."""
        if not provider_key:
            raise ExternalItemNotFound("Empty provider key", provider_key=provider_key)
        path = PurePosixPath(provider_key)
        if path.is_absolute() or ".." in path.parts:
            raise ExternalPermissionDenied(
                f"Invalid provider key: {provider_key}",
                operation="resolve_item_path",
            )
        return posixpath.join(self._root(config), provider_key)

    @staticmethod
    def _entry_name(entry: asyncssh.SFTPName) -> Optional[str]:
        """Return the entry's filename decoded to ``str`` (or None if undecodable)."""
        name = entry.filename
        if isinstance(name, bytes):
            try:
                return name.decode("utf-8")
            except UnicodeDecodeError:
                return None
        return name

    @staticmethod
    def _is_excluded(provider_key: str, exclude: list[str]) -> bool:
        """Return True when the provider key matches any exclude pattern."""
        return any(
            fnmatch.fnmatch(provider_key, pattern) or fnmatch.fnmatch(PurePosixPath(provider_key).name, pattern)
            for pattern in exclude
        )

    @staticmethod
    def _mime_for_key(key: str) -> str:
        mime_type = mimetypes.guess_type(key)[0]
        if mime_type is None:
            suffix = PurePosixPath(key).suffix.lstrip(".").lower()
            mime_type = f"audio/{suffix}" if suffix else "application/octet-stream"
        return mime_type

    @staticmethod
    def _etag_for_attrs(attrs: asyncssh.SFTPAttrs) -> Optional[str]:
        """Return an mtime+size change token for a directory-listing entry."""
        mtime_ns = attrs.mtime_ns
        if mtime_ns is None and attrs.mtime is not None:
            mtime_ns = int(attrs.mtime * 1_000_000_000)
        if mtime_ns is None or attrs.size is None:
            return None
        return f"{mtime_ns}:{attrs.size}"

    @staticmethod
    def _mtime_for_attrs(attrs: asyncssh.SFTPAttrs) -> Optional[datetime]:
        if attrs.mtime is None:
            return None
        return datetime.fromtimestamp(attrs.mtime, tz=timezone.utc)

    @staticmethod
    def _map_sftp_error(exc: asyncssh.SFTPError, path: str, provider_key: Optional[str] = None) -> Exception:
        """Translate an SFTP status error into an external-library error."""
        if isinstance(exc, asyncssh.SFTPNoSuchFile):
            return ExternalItemNotFound(
                f"Remote file not found: {path}",
                provider_key=provider_key or path,
            )
        if isinstance(exc, asyncssh.SFTPPermissionDenied):
            return ExternalPermissionDenied(
                f"Remote permission denied: {path}",
                operation="sftp",
            )
        return exc

    @staticmethod
    async def _stream_temp_dir() -> Optional[Path]:
        """Return the configured temp dir for downloads, creating it if needed."""
        temp_dir = load_config([]).external_libraries.stream_temp_dir
        if temp_dir is None:
            return None
        path = Path(temp_dir)
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
        return path

    async def _download_to_temp(self, sftp: asyncssh.SFTPClient, remote_path: str, provider_key: str) -> Path:
        """Download a remote file to a local temp file and return its path."""
        temp_dir = await self._stream_temp_dir()
        fd, tmp_name = tempfile.mkstemp(
            dir=str(temp_dir) if temp_dir else None,
            prefix="sftp-ext-",
            suffix=PurePosixPath(provider_key).suffix,
        )
        os.close(fd)
        os.chmod(tmp_name, 0o600)
        tmp_path = Path(tmp_name)

        try:
            await sftp.get(remote_path, str(tmp_path))
        except asyncssh.SFTPError as exc:
            await aiofiles.os.remove(tmp_path)
            raise self._map_sftp_error(exc, remote_path, provider_key) from exc
        except Exception:
            await aiofiles.os.remove(tmp_path)
            raise

        return tmp_path

    # ------------------------------------------------------------------
    # Adapter interface
    # ------------------------------------------------------------------

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        """Validate connectivity/credentials and return capabilities."""
        root = self._root(config)

        try:
            async with self._connect(config) as sftp:
                try:
                    attrs = await sftp.stat(root)
                except asyncssh.SFTPError as exc:
                    raise ExternalConfigError(
                        f"Cannot access remote root {root!r}: {exc}",
                        field="root",
                    ) from exc
        except ExternalConfigError:
            raise
        except asyncssh.PermissionDenied as exc:
            raise ExternalConfigError(
                f"Authentication failed for {self._username(config)!r}: {exc}",
                field="username",
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise ExternalConfigError(
                f"Cannot connect to SFTP host {self._host(config)}: {exc}",
                field="host",
            ) from exc

        if attrs.permissions is not None and not stat_module.S_ISDIR(attrs.permissions):
            raise ExternalConfigError(
                f"Remote root is not a directory: {root}",
                field="root",
            )

        self._capabilities = ExternalLibraryCapabilities(
            list_items=True,
            read_bytes=True,
            stream_url=False,
            range_read=True,
            download=True,
            compute_hash=bool(config.get("allow_hashing", True)),
            read_tags=True,
            write_tags=bool(config.get("allow_write_tags")),
            rename_source=bool(config.get("allow_rename_source")),
            delete_source=bool(config.get("allow_delete_source")),
            detect_changes=True,
            validate_config=True,
            limits={"checksum_algorithm": "sha256"},
        )
        assert self._capabilities  # for mypy
        return self._capabilities

    async def iter_items(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalItemRef]:
        """Yield audio files under the configured remote root."""
        base, rel_base = self._scope_root(config, scope)

        extensions = frozenset(config.get("extensions", _DEFAULT_EXTENSIONS))
        extension_set = {ext.lstrip(".").lower() for ext in extensions}
        recursive = bool(config.get("recursive", True))
        follow_symlinks = bool(config.get("follow_symlinks", False))
        exclude = list(config.get("exclude", []))

        try:
            async with self._connect(config) as sftp:
                stack: list[tuple[str, str]] = [(base, rel_base)]
                while stack:
                    remote_dir, rel_dir = stack.pop()
                    try:
                        entries = await sftp.readdir(remote_dir)
                    except asyncssh.SFTPError as exc:
                        mapped = self._map_sftp_error(exc, remote_dir, rel_dir or remote_dir)
                        if isinstance(mapped, (ExternalItemNotFound, ExternalPermissionDenied)):
                            raise mapped
                        raise ExternalConfigError(
                            f"Cannot list remote directory {remote_dir!r}: {exc}",
                            field="root",
                        ) from exc

                    for entry in sorted(entries, key=lambda e: str(e.filename)):
                        name = self._entry_name(entry)
                        if name is None or name in (".", ".."):
                            continue

                        rel = f"{rel_dir}/{name}" if rel_dir else name
                        if self._is_excluded(rel, exclude):
                            continue

                        remote = posixpath.join(remote_dir, name)
                        attrs = entry.attrs
                        perms = attrs.permissions if attrs is not None else None

                        if perms is not None and stat_module.S_ISLNK(perms):
                            if not follow_symlinks:
                                continue
                            try:
                                attrs = await sftp.stat(remote)
                            except asyncssh.SFTPError:
                                continue
                            perms = attrs.permissions

                        if perms is None:
                            try:
                                attrs = await sftp.stat(remote)
                            except asyncssh.SFTPError:
                                continue
                            perms = attrs.permissions

                        mode = perms or 0
                        if stat_module.S_ISDIR(mode):
                            if recursive:
                                stack.append((remote, rel))
                            continue
                        if not stat_module.S_ISREG(mode):
                            continue

                        suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                        if suffix not in extension_set:
                            continue

                        mtime = self._mtime_for_attrs(attrs)
                        if since is not None and mtime is not None and mtime <= since:
                            continue

                        yield ExternalItemRef(
                            provider_key=rel,
                            display_path=rel,
                            etag=self._etag_for_attrs(attrs),
                            mtime=mtime,
                            size=attrs.size,
                            mime_type=self._mime_for_key(rel),
                            checksum=None,
                            sha256=None,
                        )
        except (ExternalConfigError, ExternalItemNotFound, ExternalPermissionDenied):
            raise
        except asyncssh.PermissionDenied as exc:
            raise ExternalConfigError(
                f"Authentication failed for {self._username(config)!r}: {exc}",
                field="username",
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise ExternalConfigError(
                f"Cannot connect to SFTP host {self._host(config)}: {exc}",
                field="host",
            ) from exc

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        """Download a remote file to a temp file and read its embedded tags."""
        remote = self._full_path(config, item.provider_key)

        async with self._connect(config) as sftp:
            tmp_path = await self._download_to_temp(sftp, remote, item.provider_key)

        metadata = None

        try:
            from ._audio import track_metadata_from_file

            metadata = track_metadata_from_file(tmp_path, item.provider_key)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        assert metadata  # for mypy
        raw = dict(metadata.raw_metadata or {})
        mimetype = raw.get("mimetype") or item.mime_type
        raw["mimetype"] = mimetype
        raw["path"] = remote
        return dataclasses.replace(metadata, raw_metadata=raw)

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Return a proxied byte iterator over the remote file."""
        remote = self._full_path(config, item.provider_key)
        content_type = item.mime_type or self._mime_for_key(item.provider_key)

        start = 0
        end: Optional[int] = None
        size: Optional[int] = item.size
        if range is not None:
            start, end = range
            size = end - start + 1

        async def _iter_sftp() -> AsyncIterator[bytes]:
            async with self._connect(config) as sftp:
                try:
                    handle = await sftp.open(remote, "rb")
                except asyncssh.SFTPError as exc:
                    raise self._map_sftp_error(exc, remote, item.provider_key) from exc
                try:
                    pos = start
                    while True:
                        to_read = _CHUNK_SIZE if end is None else min(_CHUNK_SIZE, end - pos + 1)
                        if to_read <= 0:
                            break
                        chunk = cast(bytes, await handle.read(to_read, offset=pos))
                        if not chunk:
                            break
                        yield chunk
                        pos += len(chunk)
                finally:
                    await handle.close()

        return ExternalStream(
            kind="iterator",
            iterator=_iter_sftp(),
            content_type=content_type,
            size=size,
            supports_range=True,
            headers={},
            temporary=False,
        )

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Return a complete byte stream for the remote file."""
        return await self.open_stream(config, item)

    async def compute_sha256(self, config: dict, item: ExternalItemRef) -> str:
        """Compute the item's audio hash over a streamed or downloaded copy."""
        remote = self._full_path(config, item.provider_key)

        if config.get("fast_hash"):
            hasher = hashlib.sha256()
            async with self._connect(config) as sftp:
                try:
                    handle = await sftp.open(remote, "rb")
                except asyncssh.SFTPError as exc:
                    raise self._map_sftp_error(exc, remote, item.provider_key) from exc
                try:
                    pos = 0
                    while True:
                        chunk = cast(bytes, await handle.read(_CHUNK_SIZE, offset=pos))
                        if not chunk:
                            break
                        hasher.update(chunk)
                        pos += len(chunk)
                finally:
                    await handle.close()
            return hasher.hexdigest()

        async with self._connect(config) as sftp:
            tmp_path = await self._download_to_temp(sftp, remote, item.provider_key)

        try:
            from ..services.storage import audio_hash

            async with _FFMPEG_SEMAPHORE:
                return await audio_hash(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def write_metadata(
        self,
        config: dict,
        item: ExternalItemRef,
        metadata: ExternalTrackMetadata,
    ) -> ExternalMutationResult:
        """Rewrite the remote file's embedded tags and re-upload it in place."""
        if not self.capabilities().write_tags:
            raise UnsupportedExternalOperation("write_tags is not enabled for this SFTP library")

        remote = self._full_path(config, item.provider_key)

        async with self._connect(config) as sftp:
            tmp_path = await self._download_to_temp(sftp, remote, item.provider_key)

            try:
                from ..services.metadata import AudioMetadataWrite, write_metadata

                write_obj = AudioMetadataWrite(
                    title=metadata.title,
                    artist=metadata.artist,
                    album=metadata.album,
                    track_number=metadata.track_number,
                    disc_number=metadata.disc_number,
                    genre=metadata.genre,
                    year=metadata.release_year,
                    cover_art=metadata.cover_art,
                    cover_art_mime=metadata.cover_art_mime,
                )

                def _write() -> None:
                    write_metadata(tmp_path, write_obj)

                await asyncio.to_thread(_write)

                try:
                    await sftp.put(str(tmp_path), remote)
                except asyncssh.SFTPError as exc:
                    raise self._map_sftp_error(exc, remote, item.provider_key) from exc
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            try:
                attrs = await sftp.stat(remote)
            except asyncssh.SFTPError:
                attrs = None

        mtime = self._mtime_for_attrs(attrs) if attrs is not None else None
        if mtime is None:
            mtime = datetime.now(timezone.utc)

        return ExternalMutationResult(
            provider_key=item.provider_key,
            etag=self._etag_for_attrs(attrs) if attrs is not None else None,
            mtime=mtime,
        )

    async def rename_source(
        self,
        config: dict,
        item: ExternalItemRef,
        new_name: str,
    ) -> ExternalItemRef:
        """Rename the remote file in place, preserving its parent directory."""
        if not self.capabilities().rename_source:
            raise UnsupportedExternalOperation("rename_source is not enabled for this SFTP library")

        old_path = PurePosixPath(item.provider_key)
        new_basename = PurePosixPath(new_name.replace("\\", "/")).name
        if not new_basename or new_basename in (".", ".."):
            raise ExternalPermissionDenied(
                f"Invalid new name: {new_name!r}",
                operation="rename_source",
            )

        new_provider_key = (
            new_basename if old_path.name == item.provider_key else (old_path.parent / new_basename).as_posix()
        )
        if ".." in PurePosixPath(new_provider_key).parts:
            raise ExternalPermissionDenied(
                f"Invalid new path: {new_provider_key}",
                operation="rename_source",
            )
        if new_provider_key == item.provider_key:
            return item

        old_remote = self._full_path(config, item.provider_key)
        new_remote = self._full_path(config, new_provider_key)

        async with self._connect(config) as sftp:
            try:
                await sftp.stat(new_remote)
            except asyncssh.SFTPNoSuchFile:
                pass
            except asyncssh.SFTPError as exc:
                raise self._map_sftp_error(exc, new_remote, new_provider_key) from exc
            else:
                raise ExternalPermissionDenied(
                    f"Target path already exists: {new_provider_key}",
                    operation="rename_source",
                )

            try:
                await sftp.rename(old_remote, new_remote)
            except asyncssh.SFTPError as exc:
                raise self._map_sftp_error(exc, old_remote, item.provider_key) from exc

            try:
                attrs = await sftp.stat(new_remote)
            except asyncssh.SFTPError:
                attrs = None

        size = attrs.size if attrs is not None else item.size
        mtime = self._mtime_for_attrs(attrs) if attrs is not None else None

        return ExternalItemRef(
            provider_key=new_provider_key,
            display_path=new_provider_key,
            etag=self._etag_for_attrs(attrs) if attrs is not None else None,
            mtime=mtime,
            size=size,
            mime_type=self._mime_for_key(new_provider_key),
            checksum=item.checksum,
            sha256=item.sha256,
        )

    async def delete_source(self, config: dict, item: ExternalItemRef) -> ExternalMutationResult:
        """Delete the remote file."""
        if not self.capabilities().delete_source:
            raise UnsupportedExternalOperation("delete_source is not enabled for this SFTP library")

        remote = self._full_path(config, item.provider_key)

        async with self._connect(config) as sftp:
            try:
                await sftp.stat(remote)
            except asyncssh.SFTPError as exc:
                raise self._map_sftp_error(exc, remote, item.provider_key) from exc
            try:
                await sftp.remove(remote)
            except asyncssh.SFTPError as exc:
                raise self._map_sftp_error(exc, remote, item.provider_key) from exc

        return ExternalMutationResult(provider_key=item.provider_key)

    async def healthcheck(self, config: dict) -> ExternalHealth:
        """Check that the remote host is reachable and the root is a directory."""
        try:
            root = self._root(config)
            async with self._connect(config) as sftp:
                attrs = await sftp.stat(root)
        except (OSError, asyncssh.Error) as exc:
            return ExternalHealth(ok=False, message=str(exc))
        except ExternalConfigError as exc:
            return ExternalHealth(ok=False, message=str(exc))

        if attrs.permissions is not None and not stat_module.S_ISDIR(attrs.permissions):
            return ExternalHealth(ok=False, message=f"Remote root is not a directory: {root}")
        return ExternalHealth(ok=True, message=f"SFTP root {root} on {self._host(config)} is accessible")
