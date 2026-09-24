"""
WebDAV/HTTP external-library adapter.

Indexes audio files stored on a remote WebDAV server and serves them through
proxied HTTP streams. The remote host must be reachable from the Songhive
instance itself — all listing, download, and mutation traffic originates
server-side.

Because a WebDAV collection cannot be watched like a local directory, change
detection relies on the ``ETag`` (or ``mtime``/``size`` pair) reported by
``PROPFIND`` listings: scheduled syncs walk the configured root and only
download files whose identity changed, so unchanged trees never incur
per-file data transfer.
"""

import asyncio
import dataclasses
import fnmatch
import hashlib
import logging
import mimetypes
import os
import posixpath
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any, AsyncIterator, Optional

import aiofiles
import aiofiles.os
import httpx

from ..config.constants import AUDIO_EXTENSIONS
from ..config.loader import load_config
from .base import ExternalLibraryAdapter
from .errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalPermissionDenied,
    ExternalWriteBackError,
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
_DEFAULT_TIMEOUT_SECONDS = 30

# Body for a minimal, well-formed PROPFIND request for the properties we need.
_PROPFIND_BODY = b"""<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:displayname/>
    <D:getcontentlength/>
    <D:getlastmodified/>
    <D:getetag/>
    <D:resourcetype/>
  </D:prop>
</D:propfind>
"""

# Cap concurrent ffmpeg invocations across all WebDAV library syncs.
_FFMPEG_SEMAPHORE = asyncio.Semaphore(2)


@dataclasses.dataclass
class _DAVEntry:
    """A single resource returned by a PROPFIND listing."""

    path: str
    name: str
    size: Optional[int]
    mtime: Optional[datetime]
    etag: Optional[str]
    is_collection: bool


class WebDAVExternalAdapter(ExternalLibraryAdapter):
    """Adapter that indexes audio files from a remote WebDAV server."""

    provider_type = "webdav"
    user_configurable = True

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _url(config: dict) -> str:
        """Validate and return the base WebDAV URL."""
        url = config.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ExternalConfigError(
                'config["url"] is required and must be a non-empty string',
                field="url",
            )
        url = url.strip().rstrip("/")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ExternalConfigError(
                'config["url"] must be a valid http(s) URL',
                field="url",
            )
        return url

    @staticmethod
    def _root(config: dict) -> str:
        """Return the normalized remote root path, empty when it is the base URL."""
        raw = config.get("root") or ""
        if not isinstance(raw, str):
            raise ExternalConfigError(
                'config["root"] must be a string',
                field="root",
            )
        root = raw.strip("/")
        if root:
            path = PurePosixPath(root)
            if ".." in path.parts:
                raise ExternalConfigError(
                    f"Root {raw!r} is invalid",
                    field="root",
                )
        return root

    def _root_url(self, config: dict) -> str:
        """Return the base URL joined with the configured root path."""
        base = self._url(config)
        root = self._root(config)
        if root:
            return f"{base}/{urllib.parse.quote(root, safe='/')}"
        return base

    def _dir_url(self, root_url: str, provider_key: str) -> str:
        """Return the URL for a directory, always ``/``-terminated."""
        if provider_key:
            return f"{root_url}/{urllib.parse.quote(provider_key, safe='/')}/"
        return f"{root_url}/"

    def _full_url(self, config: dict, provider_key: str) -> str:
        """Return the full remote URL for a provider-relative key."""
        if not provider_key:
            raise ExternalItemNotFound("Empty provider key", provider_key=provider_key)
        path = PurePosixPath(provider_key)
        if path.is_absolute() or ".." in path.parts:
            raise ExternalPermissionDenied(
                f"Invalid provider key: {provider_key}",
                operation="resolve_item_url",
            )
        return f"{self._root_url(config)}/{urllib.parse.quote(provider_key, safe='/')}"

    def _scope_root(self, config: dict, scope: Optional[str]) -> tuple[str, str]:
        """Return ``(root_url, scope_provider_key)`` for a scoped sync."""
        root_url = self._root_url(config)
        if not scope:
            return root_url, ""
        clean = scope.strip().strip("/")
        if ".." in PurePosixPath(clean).parts:
            raise ExternalConfigError(
                f"Scope {scope!r} is invalid",
                field="scope",
            )
        return root_url, clean

    @staticmethod
    def _username(config: dict) -> Optional[str]:
        username = config.get("username")
        if isinstance(username, str) and username.strip():
            return username.strip()
        return None

    @staticmethod
    def _password(config: dict) -> Optional[str]:
        password = config.get("password")
        if isinstance(password, str) and password:
            return password
        return None

    @staticmethod
    def _token(config: dict) -> Optional[str]:
        token = config.get("token")
        if isinstance(token, str) and token:
            return token
        return None

    @staticmethod
    def _verify(config: dict) -> Any:
        """Return SSL verification setting (bool or CA bundle path)."""
        raw = config.get("verify_ssl", True)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw:
            return raw
        return True

    @staticmethod
    def _timeout(config: dict) -> httpx.Timeout:
        """Return an httpx timeout from the configured value."""
        raw = config.get("timeout", _DEFAULT_TIMEOUT_SECONDS)
        try:
            seconds = int(raw)
        except (TypeError, ValueError) as exc:
            raise ExternalConfigError(
                'config["timeout"] must be an integer',
                field="timeout",
            ) from exc
        if seconds <= 0:
            raise ExternalConfigError(
                'config["timeout"] must be positive',
                field="timeout",
            )
        return httpx.Timeout(seconds)

    def _auth(self, config: dict) -> Optional[httpx.Auth]:
        """Build httpx auth from username/password or token."""
        username = self._username(config)
        password = self._password(config)
        token = self._token(config)
        if username is not None:
            return httpx.BasicAuth(username, password or "")
        if token is not None:
            # Bearer tokens are not standard for all WebDAV servers, but they
            # are convenient for providers that expose a private token/API key.
            return None
        return None

    def _headers(self, config: dict) -> dict[str, str]:
        """Build any extra headers, including a Bearer token if configured."""
        headers: dict[str, str] = {}
        token = self._token(config)
        if token is not None and self._username(config) is None:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @asynccontextmanager
    async def _client(self, config: dict) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an httpx client configured for the WebDAV server."""
        async with httpx.AsyncClient(
            auth=self._auth(config),
            headers=self._headers(config),
            verify=self._verify(config),
            timeout=self._timeout(config),
            follow_redirects=True,
        ) as client:
            yield client

    # ------------------------------------------------------------------
    # WebDAV protocol helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _local_name(tag: str) -> str:
        """Return the XML local name, dropping any namespace prefix."""
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    @staticmethod
    def _status_code(status_text: Optional[str]) -> Optional[int]:
        """Parse an HTTP status line like ``HTTP/1.1 200 OK``."""
        if not status_text:
            return None
        parts = status_text.split()
        if len(parts) < 2:
            return None
        try:
            return int(parts[1])
        except ValueError:
            return None

    @staticmethod
    def _parse_last_modified(text: Optional[str]) -> Optional[datetime]:
        """Parse a WebDAV ``getlastmodified`` value to a UTC datetime."""
        if not text:
            return None
        text = text.strip()
        try:
            return parsedate_to_datetime(text)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(text)
        except (TypeError, ValueError):
            pass
        return None

    @staticmethod
    def _parse_etag(text: Optional[str]) -> Optional[str]:
        """Return a cleaned ETag, or None when the property is missing/empty."""
        if not text:
            return None
        etag = text.strip()
        if etag.startswith("W/"):
            etag = etag[2:].strip()
        if len(etag) >= 2 and etag.startswith('"') and etag.endswith('"'):
            etag = etag[1:-1]
        if not etag or etag == "-":
            return None
        return etag

    @staticmethod
    def _rel_path(base_path: str, path: str) -> str:
        """Return ``path`` relative to ``base_path`` (both unquoted, URL paths)."""
        path = path.rstrip("/")
        if base_path:
            base = base_path.rstrip("/")
            if path == base:
                return ""
            prefix = base + "/"
            if path.startswith(prefix):
                return path[len(prefix) :]
            # Different prefix than the directory we are listing; fall through.
        return path.lstrip("/")

    def _parse_propfind(self, xml: bytes, dir_url: str) -> list[_DAVEntry]:
        """Parse a PROPFIND 207 Multi-Status response."""
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise ExternalConfigError(
                f"Invalid WebDAV response from {dir_url}: {exc}",
                field="url",
            ) from exc

        entries: list[_DAVEntry] = []
        for response in root.iter():
            if self._local_name(response.tag) != "response":
                continue

            href_text: Optional[str] = None
            for child in response.iter():
                if self._local_name(child.tag) == "href":
                    href_text = (child.text or "").strip()
                    break

            if not href_text:
                continue

            resolved = urllib.parse.urljoin(dir_url, href_text)
            parsed = urllib.parse.urlparse(resolved)
            path = urllib.parse.unquote(parsed.path)

            prop: Optional[ET.Element] = None
            for propstat in response.iter():
                if self._local_name(propstat.tag) != "propstat":
                    continue

                status_text: Optional[str] = None
                candidate: Optional[ET.Element] = None
                for child in propstat.iter():
                    ln = self._local_name(child.tag)
                    if ln == "status":
                        status_text = (child.text or "").strip()
                    elif ln == "prop" and candidate is None:
                        candidate = child

                if self._status_code(status_text) == 200 and candidate is not None:
                    prop = candidate
                    break

            if prop is None:
                continue

            size: Optional[int] = None
            mtime: Optional[datetime] = None
            etag: Optional[str] = None
            is_collection = False
            displayname: Optional[str] = None

            for pchild in prop:
                ln = self._local_name(pchild.tag)
                text = (pchild.text or "").strip() if pchild.text else ""
                if ln == "getcontentlength" and text:
                    try:
                        size = int(text)
                    except ValueError:
                        pass
                elif ln == "getlastmodified":
                    mtime = self._parse_last_modified(text)
                elif ln == "getetag":
                    etag = self._parse_etag(text)
                elif ln == "resourcetype":
                    for type_child in pchild:
                        if self._local_name(type_child.tag) == "collection":
                            is_collection = True
                            break
                elif ln == "displayname" and text:
                    displayname = text

            if displayname is None:
                displayname = posixpath.basename(path.rstrip("/"))

            entries.append(
                _DAVEntry(
                    path=path,
                    name=displayname,
                    size=size,
                    mtime=mtime,
                    etag=etag,
                    is_collection=is_collection,
                )
            )

        return entries

    def _raise_for_listing(self, status: int, message: str) -> None:
        """Raise a config-level exception for a failed directory listing."""
        if status == 404 or status == 410:
            raise ExternalConfigError(
                f"Remote path not found: {message}",
                field="root",
            )
        if status in (401, 403):
            raise ExternalPermissionDenied(
                f"Remote permission denied: {message}",
                operation="webdav",
            )
        raise ExternalConfigError(
            f"WebDAV listing failed ({status}): {message}",
            field="url",
        )

    def _raise_for_item(self, status: int, path: str, provider_key: Optional[str] = None) -> None:
        """Raise an item-level exception for a failed WebDAV operation."""
        if status == 404 or status == 410:
            raise ExternalItemNotFound(
                f"Remote file not found: {path}",
                provider_key=provider_key or path,
            )
        if status in (401, 403):
            raise ExternalPermissionDenied(
                f"Remote permission denied: {path}",
                operation="webdav",
            )
        if status == 409:
            raise ExternalWriteBackError(
                f"Remote conflict: {path}",
                provider_key=provider_key or path,
            )
        raise ExternalConfigError(
            f"WebDAV request failed ({status}): {path}",
            field="url",
        )

    async def _propfind(
        self,
        client: httpx.AsyncClient,
        dir_url: str,
        *,
        depth: str = "1",
    ) -> list[_DAVEntry]:
        """Send a PROPFIND request and parse the response."""
        response = await client.request(
            "PROPFIND",
            dir_url,
            content=_PROPFIND_BODY,
            headers={"Depth": depth, "Content-Type": "text/xml; charset=utf-8"},
        )
        if response.is_error:
            self._raise_for_listing(response.status_code, dir_url)

        # Some servers return 207 Multi-Status; a 200 is also acceptable.
        if response.status_code not in (207, 200):
            self._raise_for_listing(response.status_code, dir_url)

        return self._parse_propfind(response.content, dir_url)

    async def _item_exists(self, client: httpx.AsyncClient, full_url: str) -> bool:
        """Return True when a PROPFIND Depth 0 for ``full_url`` succeeds."""
        try:
            response = await client.request(
                "PROPFIND",
                full_url,
                content=_PROPFIND_BODY,
                headers={"Depth": "0", "Content-Type": "text/xml; charset=utf-8"},
            )
        except httpx.HTTPError:
            return False
        return not response.is_error and response.status_code in (207, 200)

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
    def _etag_for_entry(entry: _DAVEntry) -> Optional[str]:
        """Return a change token for a directory-listing entry."""
        if entry.etag:
            return entry.etag
        if entry.mtime is not None and entry.size is not None:
            return f"{entry.mtime.timestamp()}:{entry.size}"
        return None

    @staticmethod
    async def _stream_temp_dir() -> Optional[Path]:
        """Return the configured temp dir for downloads, creating it if needed."""
        temp_dir = load_config([]).external_libraries.stream_temp_dir
        if temp_dir is None:
            return None
        path = Path(temp_dir)
        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
        return path

    async def _download_to_temp(
        self,
        client: httpx.AsyncClient,
        full_url: str,
        provider_key: str,
    ) -> Path:
        """Download a remote file to a local temp file and return its path."""
        temp_dir = await self._stream_temp_dir()
        fd, tmp_name = tempfile.mkstemp(
            dir=str(temp_dir) if temp_dir else None,
            prefix="webdav-ext-",
            suffix=PurePosixPath(provider_key).suffix,
        )
        os.close(fd)
        os.chmod(tmp_name, 0o600)
        tmp_path = Path(tmp_name)

        try:
            async with client.stream("GET", full_url) as response:
                if response.is_error:
                    await response.aread()
                    self._raise_for_item(response.status_code, full_url, provider_key)
                async with aiofiles.open(tmp_path, "wb") as dest:
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        await dest.write(chunk)
        except Exception:
            await aiofiles.os.remove(tmp_path)
            raise

        return tmp_path

    # ------------------------------------------------------------------
    # Adapter interface
    # ------------------------------------------------------------------

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        """Validate connectivity and the configured root and return capabilities."""
        root_url = self._root_url(config)
        dir_url = self._dir_url(root_url, "")

        try:
            async with self._client(config) as client:
                entries = await self._propfind(client, dir_url, depth="0")
        except ExternalConfigError:
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ExternalConfigError(
                f"Cannot connect to WebDAV server: {exc}",
                field="url",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalConfigError(
                f"Cannot access WebDAV root {root_url!r}: {exc}",
                field="url",
            ) from exc

        base_path = urllib.parse.unquote(urllib.parse.urlparse(dir_url).path).rstrip("/")
        root_entry = next(
            (e for e in entries if urllib.parse.unquote(e.path).rstrip("/") == base_path),
            None,
        )

        if root_entry is not None and not root_entry.is_collection:
            raise ExternalConfigError(
                f"Remote root is not a directory: {root_url}",
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
        """Yield audio files under the configured WebDAV root."""
        root_url, rel_base = self._scope_root(config, scope)

        extensions = frozenset(config.get("extensions", _DEFAULT_EXTENSIONS))
        extension_set = {ext.lstrip(".").lower() for ext in extensions}
        recursive = bool(config.get("recursive", True))
        exclude = list(config.get("exclude", []))

        try:
            async with self._client(config) as client:
                stack: list[tuple[str, str]] = [(rel_base, rel_base)]
                while stack:
                    dir_provider_key, dir_prefix = stack.pop()
                    dir_url = self._dir_url(root_url, dir_provider_key)
                    base_path = urllib.parse.unquote(urllib.parse.urlparse(dir_url).path).rstrip("/")

                    try:
                        entries = await self._propfind(client, dir_url, depth="1")
                    except httpx.HTTPError as exc:
                        raise ExternalConfigError(
                            f"Cannot list WebDAV directory {dir_url!r}: {exc}",
                            field="url",
                        ) from exc

                    for entry in entries:
                        rel = self._rel_path(base_path, entry.path)
                        if not rel or rel == ".":
                            continue

                        if dir_prefix:
                            provider_key = f"{dir_prefix}/{rel}"
                        else:
                            provider_key = rel

                        if self._is_excluded(provider_key, exclude):
                            continue

                        if entry.is_collection:
                            if recursive:
                                stack.append((provider_key, provider_key))
                            continue

                        suffix = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
                        if suffix not in extension_set:
                            continue

                        mtime = entry.mtime
                        if since is not None and mtime is not None and mtime <= since:
                            continue

                        yield ExternalItemRef(
                            provider_key=provider_key,
                            display_path=provider_key,
                            etag=self._etag_for_entry(entry),
                            mtime=mtime,
                            size=entry.size,
                            mime_type=self._mime_for_key(rel),
                            checksum=None,
                            sha256=None,
                        )
        except ExternalConfigError:
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ExternalConfigError(
                f"Cannot connect to WebDAV server: {exc}",
                field="url",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalConfigError(
                f"Cannot list WebDAV directory {self._root_url(config)!r}: {exc}",
                field="url",
            ) from exc

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        """Download a remote file to a temp file and read its embedded tags."""
        full_url = self._full_url(config, item.provider_key)

        async with self._client(config) as client:
            tmp_path = await self._download_to_temp(client, full_url, item.provider_key)

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
        raw["url"] = full_url
        raw["path"] = urllib.parse.unquote(urllib.parse.urlparse(full_url).path)
        return dataclasses.replace(metadata, raw_metadata=raw)

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Return a proxied byte iterator over the remote file."""
        full_url = self._full_url(config, item.provider_key)
        content_type = item.mime_type or self._mime_for_key(item.provider_key)

        start = 0
        end: Optional[int] = None
        size: Optional[int] = item.size
        request_headers: dict[str, str] = {}
        if range is not None:
            start, end = range
            request_headers["Range"] = f"bytes={start}-{end}"
            size = end - start + 1

        async def _iter_http() -> AsyncIterator[bytes]:
            async with self._client(config) as client:
                async with client.stream("GET", full_url, headers=request_headers) as response:
                    if response.is_error:
                        await response.aread()
                        self._raise_for_item(response.status_code, full_url, item.provider_key)
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        yield chunk

        return ExternalStream(
            kind="iterator",
            iterator=_iter_http(),
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
        """Compute the item's audio hash, downloading when necessary."""
        full_url = self._full_url(config, item.provider_key)

        if config.get("fast_hash"):
            hasher = hashlib.sha256()
            async with self._client(config) as client:
                async with client.stream("GET", full_url) as response:
                    if response.is_error:
                        await response.aread()
                        self._raise_for_item(response.status_code, full_url, item.provider_key)
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        hasher.update(chunk)
            return hasher.hexdigest()

        async with self._client(config) as client:
            tmp_path = await self._download_to_temp(client, full_url, item.provider_key)

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
            raise UnsupportedExternalOperation("write_tags is not enabled for this WebDAV library")

        full_url = self._full_url(config, item.provider_key)

        async with self._client(config) as client:
            tmp_path = await self._download_to_temp(client, full_url, item.provider_key)

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

                data = tmp_path.read_bytes()
                response = await client.request(
                    "PUT",
                    full_url,
                    content=data,
                    headers={"Content-Type": item.mime_type or "application/octet-stream"},
                )
                if response.is_error:
                    self._raise_for_item(response.status_code, full_url, item.provider_key)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            try:
                entries = await self._propfind(client, full_url, depth="0")
            except (httpx.HTTPError, ExternalConfigError, ExternalPermissionDenied):
                entries = []

        mtime = None
        etag = None

        for entry in entries:
            mtime = entry.mtime
            etag = entry.etag

        if mtime is None:
            mtime = datetime.now(timezone.utc)

        return ExternalMutationResult(
            provider_key=item.provider_key,
            etag=etag,
            mtime=mtime,
        )

    async def rename_source(
        self,
        config: dict,
        item: ExternalItemRef,
        new_name: str,
    ) -> ExternalItemRef:
        """Rename the remote file, preserving its parent directory."""
        if not self.capabilities().rename_source:
            raise UnsupportedExternalOperation("rename_source is not enabled for this WebDAV library")

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

        old_full = self._full_url(config, item.provider_key)
        new_full = self._full_url(config, new_provider_key)

        async with self._client(config) as client:
            if await self._item_exists(client, new_full):
                raise ExternalPermissionDenied(
                    f"Target path already exists: {new_provider_key}",
                    operation="rename_source",
                )

            response = await client.request(
                "MOVE",
                old_full,
                headers={
                    "Destination": new_full,
                    "Overwrite": "F",
                },
            )
            if response.is_error:
                self._raise_for_item(response.status_code, old_full, item.provider_key)

            try:
                entries = await self._propfind(client, new_full, depth="0")
            except (httpx.HTTPError, ExternalConfigError, ExternalPermissionDenied):
                entries = []

        mtime = None
        etag = None
        size = item.size

        for entry in entries:
            mtime = entry.mtime
            etag = entry.etag
            size = entry.size

        return ExternalItemRef(
            provider_key=new_provider_key,
            display_path=new_provider_key,
            etag=etag,
            mtime=mtime,
            size=size,
            mime_type=self._mime_for_key(new_provider_key),
            checksum=item.checksum,
            sha256=item.sha256,
        )

    async def delete_source(self, config: dict, item: ExternalItemRef) -> ExternalMutationResult:
        """Delete the remote file."""
        if not self.capabilities().delete_source:
            raise UnsupportedExternalOperation("delete_source is not enabled for this WebDAV library")

        full_url = self._full_url(config, item.provider_key)

        async with self._client(config) as client:
            response = await client.request("DELETE", full_url)
            if response.is_error:
                self._raise_for_item(response.status_code, full_url, item.provider_key)

        return ExternalMutationResult(provider_key=item.provider_key)

    async def healthcheck(self, config: dict) -> ExternalHealth:
        """Check that the WebDAV root is reachable and is a collection."""
        root_url = self._root_url(config)
        dir_url = self._dir_url(root_url, "")
        try:
            async with self._client(config) as client:
                entries = await self._propfind(client, dir_url, depth="0")
        except (httpx.HTTPError, ExternalConfigError, ExternalPermissionDenied) as exc:
            return ExternalHealth(ok=False, message=str(exc))

        base_path = urllib.parse.unquote(urllib.parse.urlparse(dir_url).path).rstrip("/")
        root_entry = next(
            (e for e in entries if urllib.parse.unquote(e.path).rstrip("/") == base_path),
            None,
        )

        if root_entry is not None and not root_entry.is_collection:
            return ExternalHealth(ok=False, message=f"Remote root is not a directory: {root_url}")

        return ExternalHealth(ok=True, message=f"WebDAV root {root_url} is accessible")
