"""
Dropbox external-library adapter.

Indexes audio files stored in a Dropbox account and serves them through
short-lived ``files/get_temporary_link`` redirects (the Dropbox analogue of
S3 presigned URLs) or proxied HTTP streams. All traffic originates
server-side against the Dropbox HTTP API v2, so the Songhive instance needs
outbound HTTPS access to ``api.dropboxapi.com`` and
``content.dropboxapi.com`` (plus ``dl.dropboxusercontent.com`` when clients
fetch temporary links directly).

Authentication accepts either a static ``access_token`` or an OAuth
``refresh_token`` + ``app_key`` (+ optional ``app_secret``) pair — Dropbox
access tokens only live ~4 hours, so the refresh grant is the durable
option. Refreshed access tokens are cached process-wide for their declared
lifetime.

Because a Dropbox account cannot be watched like a local directory, change
detection relies on the ``rev`` reported by ``files/list_folder`` listings:
scheduled syncs walk the configured root and only download files whose
revision changed, so unchanged trees never incur per-file data transfer.
"""

import asyncio
import dataclasses
import fnmatch
import hashlib
import json
import logging
import mimetypes
import os
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
    ExternalLibraryError,
    ExternalPermissionDenied,
    ExternalWriteBackError,
    UnsupportedExternalOperation,
)
from .oauth import OAuthProviderSpec
from .types import (
    ExternalHealth,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalMutationResult,
    ExternalStream,
    ExternalTrackMetadata,
)

logger = logging.getLogger(__name__)

# OAuth authorization-code flow spec powering the UI "Connect" button.
# ``token_access_type=offline`` makes Dropbox return a durable refresh token.
DROPBOX_OAUTH_SPEC = OAuthProviderSpec(
    provider_type="dropbox",
    authorize_url="https://www.dropbox.com/oauth2/authorize",
    token_url="https://api.dropboxapi.com/oauth2/token",
    client_id_field="app_key",
    client_secret_field="app_secret",
    extra_authorize_params={"token_access_type": "offline"},
    token_fields={
        "access_token": "access_token",
        "refresh_token": "refresh_token",
        "account_id": "account_id",
    },
)

_API_BASE = "https://api.dropboxapi.com"
_CONTENT_BASE = "https://content.dropboxapi.com"

_DEFAULT_EXTENSIONS = frozenset(AUDIO_EXTENSIONS)
_CHUNK_SIZE = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30
_LIST_PAGE_SIZE = 2000
# Single ``files/upload`` calls are capped at 150 MiB; larger payloads go
# through an upload session chunked at this size.
_UPLOAD_SESSION_THRESHOLD = 150 * 1024 * 1024
_UPLOAD_CHUNK = 16 * 1024 * 1024
# Refresh margin so an access token is never used on its last minute.
_TOKEN_EXPIRY_MARGIN_SECONDS = 60

# Refreshed OAuth access tokens, keyed by a fingerprint of the refresh grant.
# Adapters are instantiated per operation, so the cache must be module-level.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}

# Cap concurrent ffmpeg invocations across all Dropbox library syncs.
_FFMPEG_SEMAPHORE = asyncio.Semaphore(2)


class DropboxExternalAdapter(ExternalLibraryAdapter):
    """Adapter that indexes audio files from a Dropbox account."""

    provider_type = "dropbox"
    user_configurable = True

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _access_token(config: dict) -> Optional[str]:
        token = config.get("access_token")
        if isinstance(token, str) and token.strip():
            return token.strip()
        return None

    @staticmethod
    def _refresh_config(config: dict) -> Optional[tuple[str, str, Optional[str]]]:
        """Return ``(refresh_token, app_key, app_secret)`` when a refresh grant is configured."""
        refresh_token = config.get("refresh_token")
        if not (isinstance(refresh_token, str) and refresh_token.strip()):
            return None
        app_key = config.get("app_key")
        if not (isinstance(app_key, str) and app_key.strip()):
            raise ExternalConfigError(
                'config["app_key"] is required when config["refresh_token"] is set',
                field="app_key",
            )
        app_secret = config.get("app_secret")
        if not (isinstance(app_secret, str) and app_secret):
            app_secret = None
        return refresh_token.strip(), app_key.strip(), app_secret

    @staticmethod
    def _root(config: dict) -> str:
        """Return the normalized Dropbox root path (``Music/Lossless`` style)."""
        raw = config.get("root") or ""
        if not isinstance(raw, str):
            raise ExternalConfigError(
                'config["root"] must be a string',
                field="root",
            )
        root = raw.strip().strip("/")
        if root and ".." in PurePosixPath(root).parts:
            raise ExternalConfigError(
                f"Root {raw!r} is invalid",
                field="root",
            )
        return root

    @staticmethod
    def _scope(scope: Optional[str]) -> str:
        """Return the validated scope path relative to the configured root."""
        if not scope:
            return ""
        if not isinstance(scope, str):
            raise ExternalConfigError(
                'config["scope"] must be a string',
                field="scope",
            )
        clean = scope.strip().strip("/")
        if clean and ".." in PurePosixPath(clean).parts:
            raise ExternalConfigError(
                f"Scope {scope!r} is invalid",
                field="scope",
            )
        return clean

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

    # ------------------------------------------------------------------
    # Dropbox path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dbx_path(*parts: str) -> str:
        """Join path fragments into a Dropbox API path (``""`` for the root)."""
        joined = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
        return f"/{joined}" if joined else ""

    def _full_path(self, config: dict, provider_key: str) -> str:
        """Return the absolute Dropbox path for a provider-relative key."""
        if not provider_key:
            raise ExternalItemNotFound("Empty provider key", provider_key=provider_key)
        path = PurePosixPath(provider_key)
        if path.is_absolute() or ".." in path.parts:
            raise ExternalPermissionDenied(
                f"Invalid provider key: {provider_key}",
                operation="resolve_item_path",
            )
        return self._dbx_path(self._root(config), provider_key)

    @staticmethod
    def _provider_key(root: str, path_display: str) -> str:
        """Return the root-relative provider key for a ``path_display`` value."""
        rel = path_display.lstrip("/")
        if not root:
            return rel
        prefix = root + "/"
        # Dropbox paths are case-insensitive but case-preserving; compare
        # case-insensitively and slice the original to keep display casing.
        if rel.lower().startswith(prefix.lower()):
            return rel[len(prefix) :]
        if rel.lower() == root.lower():
            return ""
        return rel

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _client(self, config: dict) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an httpx client configured for the Dropbox API."""
        async with httpx.AsyncClient(
            timeout=self._timeout(config),
            follow_redirects=True,
        ) as client:
            yield client

    def _token_cache_key(self, config: dict) -> Optional[str]:
        """Return the token-cache key for the configured refresh grant."""
        refresh = self._refresh_config(config)
        if refresh is None:
            return None
        refresh_token, app_key, _ = refresh
        return hashlib.sha256(f"{app_key}|{refresh_token}".encode()).hexdigest()

    def _evict_token(self, config: dict) -> None:
        """Drop a cached access token for the configured refresh grant."""
        key = self._token_cache_key(config)
        if key is not None:
            _TOKEN_CACHE.pop(key, None)

    async def _refreshed_token(
        self,
        client: httpx.AsyncClient,
        refresh_token: str,
        app_key: str,
        app_secret: Optional[str],
    ) -> str:
        """Exchange a refresh grant for an access token, using the cache."""
        cache_key = hashlib.sha256(f"{app_key}|{refresh_token}".encode()).hexdigest()
        cached = _TOKEN_CACHE.get(cache_key)
        if cached is not None and cached[1] > time.time() + _TOKEN_EXPIRY_MARGIN_SECONDS:
            return cached[0]

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": app_key,
        }
        if app_secret:
            data["client_secret"] = app_secret

        response = await client.post(f"{_API_BASE}/oauth2/token", data=data)
        if response.is_error:
            try:
                error = response.json().get("error")
            except ValueError:
                error = None
            raise ExternalConfigError(
                f"Dropbox token refresh failed ({response.status_code}): {error or 'unknown error'}",
                field="refresh_token",
            )

        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ExternalConfigError(
                "Dropbox token refresh returned no access_token",
                field="refresh_token",
            )
        try:
            expires_in = int(payload.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        _TOKEN_CACHE[cache_key] = (token, time.time() + expires_in)
        return token

    async def _token(self, config: dict, client: httpx.AsyncClient) -> str:
        """Resolve the bearer token for a request."""
        refresh = self._refresh_config(config)
        if refresh is not None:
            return await self._refreshed_token(client, *refresh)
        token = self._access_token(config)
        if token is not None:
            return token
        raise ExternalConfigError(
            'config["access_token"] or config["refresh_token"] is required',
            field="access_token",
        )

    async def _post(
        self,
        client: httpx.AsyncClient,
        config: dict,
        url: str,
        *,
        arg: Optional[dict] = None,
        headers: Optional[dict] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """POST to the Dropbox API with auth, retrying once on a stale token."""
        extra = dict(headers or {})
        if arg is not None:
            # Non-ASCII path names must be escaped in the Dropbox-API-Arg
            # header; json.dumps does that with ensure_ascii=True.
            extra["Dropbox-API-Arg"] = json.dumps(arg)
        extra["Authorization"] = f"Bearer {await self._token(config, client)}"

        response = await client.post(url, headers=extra, **kwargs)
        if response.status_code == 401 and self._refresh_config(config) is not None:
            # A cached access token may have been revoked early; drop it and
            # retry once with a freshly refreshed token.
            self._evict_token(config)
            extra["Authorization"] = f"Bearer {await self._token(config, client)}"
            response = await client.post(url, headers=extra, **kwargs)
        return response

    @asynccontextmanager
    async def _post_stream(
        self,
        client: httpx.AsyncClient,
        config: dict,
        url: str,
        *,
        arg: Optional[dict] = None,
        headers: Optional[dict] = None,
        **kwargs: Any,
    ) -> AsyncIterator[httpx.Response]:
        """Streaming variant of ``_post`` for content-download endpoints."""
        extra = dict(headers or {})
        if arg is not None:
            extra["Dropbox-API-Arg"] = json.dumps(arg)
        extra["Authorization"] = f"Bearer {await self._token(config, client)}"

        for attempt in range(2):
            async with client.stream("POST", url, headers=extra, **kwargs) as response:
                if response.status_code == 401 and attempt == 0 and self._refresh_config(config) is not None:
                    self._evict_token(config)
                    extra["Authorization"] = f"Bearer {await self._token(config, client)}"
                    continue
                yield response
                return

    # ------------------------------------------------------------------
    # Error mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _error_summary(response: httpx.Response) -> str:
        """Return the ``error_summary`` tag path from a Dropbox error body."""
        try:
            return str(response.json().get("error_summary") or "")
        except ValueError:
            return ""

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """
        Return the provider error detail, falling back to the raw body.

        Dropbox returns JSON ``error_summary`` values for 409s, but 400-level
        rejections (undecodable ``Dropbox-API-Arg``, non-empty body or
        ``Content-Type``, missing fields) carry a plaintext explanation, and
        auth failures carry the offending scope in the ``error`` object
        (e.g. ``missing_scope`` with ``required_scope``).
        """
        try:
            body = response.json()
        except ValueError:
            text = (response.text or "").strip()
            return text[:300] if text else ""
        if not isinstance(body, dict):
            return str(body)[:300]
        summary = str(body.get("error_summary") or "")
        error = body.get("error")
        if isinstance(error, dict):
            extras = {k: v for k, v in error.items() if k != ".tag"}
            if extras:
                return f"{summary} {json.dumps(extras)}"[:300]
        return summary or json.dumps(body)[:300]

    def _raise_for_listing(self, response: httpx.Response, target: str) -> None:
        """Raise a config-level exception for a failed folder listing."""
        summary = self._error_summary(response)
        if response.status_code in (401, 403):
            detail = self._error_detail(response)
            raise ExternalPermissionDenied(
                f"Dropbox permission denied: {target}" + (f" — {detail}" if detail else ""),
                operation="dropbox",
            )
        if "not_folder" in summary:
            raise ExternalConfigError(
                f"Dropbox path is not a folder: {target}",
                field="root",
            )
        if "not_found" in summary or response.status_code == 404:
            raise ExternalConfigError(
                f"Dropbox path not found: {target}",
                field="root",
            )
        detail = self._error_detail(response)
        raise ExternalConfigError(
            f"Dropbox listing failed ({response.status_code}): {target}" + (f" — {detail}" if detail else ""),
            field="root",
        )

    def _raise_for_item(
        self,
        response: httpx.Response,
        path: str,
        provider_key: Optional[str] = None,
    ) -> None:
        """Raise an item-level exception for a failed Dropbox operation."""
        summary = self._error_summary(response)
        if response.status_code in (401, 403):
            detail = self._error_detail(response)
            raise ExternalPermissionDenied(
                f"Dropbox permission denied: {path}" + (f" — {detail}" if detail else ""),
                operation="dropbox",
            )
        if "not_found" in summary or response.status_code == 404:
            raise ExternalItemNotFound(
                f"Dropbox file not found: {path}",
                provider_key=provider_key or path,
            )
        if "insufficient_space" in summary or response.status_code == 413:
            raise ExternalWriteBackError(
                f"Dropbox storage is full: {path}",
                provider_key=provider_key or path,
            )
        if "conflict" in summary or response.status_code == 409:
            raise ExternalWriteBackError(
                f"Dropbox conflict: {path}",
                provider_key=provider_key or path,
            )
        detail = self._error_detail(response)
        raise ExternalConfigError(
            f"Dropbox request failed ({response.status_code}): {path}" + (f" — {detail}" if detail else ""),
            field="root",
        )

    # ------------------------------------------------------------------
    # Dropbox helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_server_modified(text: Optional[str]) -> Optional[datetime]:
        """Parse a Dropbox ``server_modified`` timestamp to a UTC datetime."""
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.strip())
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

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

    def _item_ref(self, root: str, entry: dict) -> Optional[ExternalItemRef]:
        """Build an ``ExternalItemRef`` from a ``files/list_folder`` entry."""
        path_display = entry.get("path_display") or entry.get("path_lower")
        if not path_display:
            return None
        provider_key = self._provider_key(root, path_display)
        if not provider_key:
            return None
        return ExternalItemRef(
            provider_key=provider_key,
            display_path=provider_key,
            etag=entry.get("rev"),
            mtime=self._parse_server_modified(entry.get("server_modified")),
            size=entry.get("size"),
            mime_type=self._mime_for_key(provider_key),
            # Dropbox's own content hash (SHA-256 of 4 MiB block hashes) is not
            # the audio sha256; it is still a useful integrity/change token.
            checksum=entry.get("content_hash"),
            sha256=None,
        )

    async def _list_folder(
        self,
        client: httpx.AsyncClient,
        config: dict,
        path: str,
        *,
        recursive: bool,
    ) -> AsyncIterator[dict]:
        """Yield ``files/list_folder`` entries, following cursors."""
        url = f"{_API_BASE}/2/files/list_folder"
        body: dict[str, Any] = {
            "path": path,
            "recursive": recursive,
            "include_deleted": False,
            "include_non_downloadable_files": False,
            "limit": _LIST_PAGE_SIZE,
        }
        while True:
            response = await self._post(client, config, url, json=body)
            if response.is_error:
                self._raise_for_listing(response, path or "/")

            payload = response.json()
            for entry in payload.get("entries") or []:
                yield entry

            if not payload.get("has_more"):
                break
            cursor = payload.get("cursor")
            if not cursor:
                break
            url = f"{_API_BASE}/2/files/list_folder/continue"
            body = {"cursor": cursor}

    async def _get_metadata(
        self,
        client: httpx.AsyncClient,
        config: dict,
        path: str,
    ) -> dict:
        """Return ``files/get_metadata`` for a path, raising typed errors."""
        response = await self._post(
            client,
            config,
            f"{_API_BASE}/2/files/get_metadata",
            json={"path": path},
        )
        if response.is_error:
            self._raise_for_item(response, path)
        return response.json()

    async def _current_account(self, client: httpx.AsyncClient, config: dict) -> dict:
        """Return the authenticated account, validating the token."""
        response = await self._post(
            client,
            config,
            f"{_API_BASE}/2/users/get_current_account",
        )
        if response.is_error:
            summary = self._error_summary(response)
            if response.status_code in (401, 403):
                detail = self._error_detail(response)
                raise ExternalPermissionDenied(
                    "Dropbox access token was rejected" + (f" — {detail}" if detail else ""),
                    operation="dropbox",
                )
            raise ExternalConfigError(
                f"Dropbox account check failed ({response.status_code}): {summary or 'unknown error'}",
                field="access_token",
            )
        return response.json()

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
        config: dict,
        full_path: str,
        provider_key: str,
    ) -> Path:
        """Download a Dropbox file to a local temp file and return its path."""
        temp_dir = await self._stream_temp_dir()
        fd, tmp_name = tempfile.mkstemp(
            dir=str(temp_dir) if temp_dir else None,
            prefix="dropbox-ext-",
            suffix=PurePosixPath(provider_key).suffix,
        )
        os.close(fd)
        os.chmod(tmp_name, 0o600)
        tmp_path = Path(tmp_name)

        try:
            async with self._post_stream(
                client,
                config,
                f"{_CONTENT_BASE}/2/files/download",
                arg={"path": full_path},
            ) as response:
                if response.is_error:
                    await response.aread()
                    self._raise_for_item(response, full_path, provider_key)
                async with aiofiles.open(tmp_path, "wb") as dest:
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        await dest.write(chunk)
        except Exception:
            await aiofiles.os.remove(tmp_path)
            raise

        return tmp_path

    async def _upload(
        self,
        client: httpx.AsyncClient,
        config: dict,
        full_path: str,
        data: bytes,
    ) -> dict:
        """Upload ``data`` to ``full_path``, returning the file metadata."""
        commit = {
            "path": full_path,
            "mode": "overwrite",
            "autorename": False,
            "mute": True,
        }

        if len(data) <= _UPLOAD_SESSION_THRESHOLD:
            response = await self._post(
                client,
                config,
                f"{_CONTENT_BASE}/2/files/upload",
                arg=commit,
                content=data,
            )
            if response.is_error:
                self._raise_for_item(response, full_path)
            return response.json()

        # Files above the single-upload cap go through an upload session.
        response = await self._post(
            client,
            config,
            f"{_CONTENT_BASE}/2/files/upload_session/start",
            arg={"close": False},
            content=data[:_UPLOAD_CHUNK],
        )
        if response.is_error:
            self._raise_for_item(response, full_path)
        session_id = response.json().get("session_id")
        if not session_id:
            raise ExternalWriteBackError(
                f"Dropbox upload session start returned no session_id: {full_path}",
                provider_key=full_path,
            )

        offset = _UPLOAD_CHUNK
        while len(data) - offset > _UPLOAD_CHUNK:
            response = await self._post(
                client,
                config,
                f"{_CONTENT_BASE}/2/files/upload_session/append_v2",
                arg={
                    "cursor": {"session_id": session_id, "offset": offset},
                    "close": False,
                },
                content=data[offset : offset + _UPLOAD_CHUNK],
            )
            if response.is_error:
                self._raise_for_item(response, full_path)
            offset += _UPLOAD_CHUNK

        response = await self._post(
            client,
            config,
            f"{_CONTENT_BASE}/2/files/upload_session/finish",
            arg={
                "cursor": {"session_id": session_id, "offset": offset},
                "commit": commit,
            },
            content=data[offset:],
        )
        if response.is_error:
            self._raise_for_item(response, full_path)
        return response.json()

    async def _validate_root(self, client: httpx.AsyncClient, config: dict) -> None:
        """Check the configured root exists and is a folder."""
        root = self._root(config)
        if not root:
            return
        try:
            metadata = await self._get_metadata(client, config, self._dbx_path(root))
        except ExternalItemNotFound as exc:
            raise ExternalConfigError(
                f"Dropbox path not found: /{root}",
                field="root",
            ) from exc
        if metadata.get(".tag") != "folder":
            raise ExternalConfigError(
                f"Dropbox root is not a folder: /{root}",
                field="root",
            )

    # ------------------------------------------------------------------
    # Adapter interface
    # ------------------------------------------------------------------

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        """Validate credentials and the configured root; return capabilities."""
        # Surface missing/invalid auth and root config before any HTTP call.
        if self._access_token(config) is None and self._refresh_config(config) is None:
            raise ExternalConfigError(
                'config["access_token"] or config["refresh_token"] is required',
                field="access_token",
            )
        self._root(config)
        self._timeout(config)

        try:
            async with self._client(config) as client:
                await self._current_account(client, config)
                await self._validate_root(client, config)
        except ExternalConfigError:
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ExternalConfigError(
                f"Cannot connect to the Dropbox API: {exc}",
                field="access_token",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalConfigError(
                f"Cannot access the Dropbox API: {exc}",
                field="access_token",
            ) from exc

        self._capabilities = ExternalLibraryCapabilities(
            list_items=True,
            read_bytes=True,
            stream_url=bool(config.get("temporary_links")),
            range_read=True,
            download=True,
            compute_hash=bool(config.get("allow_hashing", True)),
            read_tags=True,
            write_tags=bool(config.get("allow_write_tags")),
            rename_source=bool(config.get("allow_rename_source")),
            delete_source=bool(config.get("allow_delete_source")),
            detect_changes=True,
            validate_config=True,
            limits={"checksum_algorithm": "dropbox"},
        )
        assert self._capabilities  # for mypy
        return self._capabilities

    async def iter_items(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalItemRef]:
        """Yield audio files under the configured Dropbox root."""
        root = self._root(config)
        scope_path = self._scope(scope)
        list_path = self._dbx_path(root, scope_path)

        extensions = frozenset(config.get("extensions", _DEFAULT_EXTENSIONS))
        extension_set = {ext.lstrip(".").lower() for ext in extensions}
        recursive = bool(config.get("recursive", True))
        exclude = list(config.get("exclude", []))

        try:
            async with self._client(config) as client:
                async for entry in self._list_folder(client, config, list_path, recursive=recursive):
                    if entry.get(".tag") != "file":
                        continue

                    item = self._item_ref(root, entry)
                    if item is None:
                        continue

                    suffix = item.provider_key.rsplit(".", 1)[-1].lower() if "." in item.provider_key else ""
                    if suffix not in extension_set:
                        continue
                    if self._is_excluded(item.provider_key, exclude):
                        continue
                    if since is not None and item.mtime is not None and item.mtime <= since:
                        continue

                    yield item
        except ExternalConfigError:
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ExternalConfigError(
                f"Cannot connect to the Dropbox API: {exc}",
                field="root",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalConfigError(
                f"Cannot list Dropbox folder {list_path or '/'!r}: {exc}",
                field="root",
            ) from exc

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        """Download a Dropbox file to a temp file and read its embedded tags."""
        full_path = self._full_path(config, item.provider_key)

        async with self._client(config) as client:
            tmp_path = await self._download_to_temp(client, config, full_path, item.provider_key)

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
        raw["path"] = full_path
        if item.etag:
            raw["rev"] = item.etag
        return dataclasses.replace(metadata, raw_metadata=raw)

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Return a temporary-link URL stream, or a proxied byte iterator."""
        full_path = self._full_path(config, item.provider_key)
        content_type = item.mime_type or self._mime_for_key(item.provider_key)

        if config.get("temporary_links"):
            async with self._client(config) as client:
                response = await self._post(
                    client,
                    config,
                    f"{_API_BASE}/2/files/get_temporary_link",
                    json={"path": full_path},
                )
                if response.is_error:
                    self._raise_for_item(response, full_path, item.provider_key)
                payload = response.json()

            link = payload.get("link")
            if not link:
                raise ExternalConfigError(
                    f"Dropbox returned no temporary link for {full_path}",
                    field="root",
                )
            metadata = payload.get("metadata") or {}
            return ExternalStream(
                kind="url",
                url=link,
                content_type=content_type,
                size=metadata.get("size") or item.size,
                supports_range=True,
                headers={},
                temporary=True,
                safe_to_redirect=True,
            )

        size: Optional[int] = item.size
        request_headers: dict[str, str] = {}
        content_range: Optional[str] = None
        if range is not None:
            start, end = range
            request_headers["Range"] = f"bytes={start}-{end}"
            size = end - start + 1
            content_range = f"bytes {start}-{end}/{item.size if item.size is not None else '*'}"

        async def _iter_dropbox() -> AsyncIterator[bytes]:
            async with self._client(config) as client:
                async with self._post_stream(
                    client,
                    config,
                    f"{_CONTENT_BASE}/2/files/download",
                    arg={"path": full_path},
                    headers=request_headers,
                ) as response:
                    if response.is_error:
                        await response.aread()
                        self._raise_for_item(response, full_path, item.provider_key)
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        yield chunk

        return ExternalStream(
            kind="iterator",
            iterator=_iter_dropbox(),
            content_type=content_type,
            size=size,
            supports_range=True,
            headers={},
            temporary=False,
            content_range=content_range,
        )

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Return a complete byte stream for the Dropbox file."""
        return await self.open_stream(config, item)

    async def compute_sha256(self, config: dict, item: ExternalItemRef) -> str:
        """Compute the item's audio hash, downloading when necessary."""
        full_path = self._full_path(config, item.provider_key)

        if config.get("fast_hash"):
            hasher = hashlib.sha256()
            async with self._client(config) as client:
                async with self._post_stream(
                    client,
                    config,
                    f"{_CONTENT_BASE}/2/files/download",
                    arg={"path": full_path},
                ) as response:
                    if response.is_error:
                        await response.aread()
                        self._raise_for_item(response, full_path, item.provider_key)
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        hasher.update(chunk)
            return hasher.hexdigest()

        async with self._client(config) as client:
            tmp_path = await self._download_to_temp(client, config, full_path, item.provider_key)

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
        """Rewrite the Dropbox file's embedded tags and re-upload it in place."""
        if not self.capabilities().write_tags:
            raise UnsupportedExternalOperation("write_tags is not enabled for this Dropbox library")

        full_path = self._full_path(config, item.provider_key)

        async with self._client(config) as client:
            tmp_path = await self._download_to_temp(client, config, full_path, item.provider_key)

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

                data = await asyncio.to_thread(tmp_path.read_bytes)
                uploaded = await self._upload(client, config, full_path, data)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        return ExternalMutationResult(
            provider_key=item.provider_key,
            etag=uploaded.get("rev"),
            mtime=self._parse_server_modified(uploaded.get("server_modified")) or datetime.now(timezone.utc),
            checksum=uploaded.get("content_hash"),
        )

    async def rename_source(
        self,
        config: dict,
        item: ExternalItemRef,
        new_name: str,
    ) -> ExternalItemRef:
        """Rename the Dropbox file, preserving its parent folder."""
        if not self.capabilities().rename_source:
            raise UnsupportedExternalOperation("rename_source is not enabled for this Dropbox library")

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

        old_full = self._full_path(config, item.provider_key)
        new_full = self._full_path(config, new_provider_key)

        async with self._client(config) as client:
            response = await self._post(
                client,
                config,
                f"{_API_BASE}/2/files/move_v2",
                json={
                    "from_path": old_full,
                    "to_path": new_full,
                    "autorename": False,
                    "allow_shared_folder": False,
                    "allow_ownership_transfer": False,
                },
            )
            if response.is_error:
                summary = self._error_summary(response)
                if "to/conflict" in summary or summary.startswith("to/"):
                    raise ExternalPermissionDenied(
                        f"Target path already exists: {new_provider_key}",
                        operation="rename_source",
                    )
                self._raise_for_item(response, old_full, item.provider_key)
            moved = response.json().get("metadata") or response.json()

        return ExternalItemRef(
            provider_key=new_provider_key,
            display_path=new_provider_key,
            etag=moved.get("rev") or item.etag,
            mtime=self._parse_server_modified(moved.get("server_modified")) or item.mtime,
            size=moved.get("size", item.size),
            mime_type=self._mime_for_key(new_provider_key),
            checksum=moved.get("content_hash") or item.checksum,
            sha256=item.sha256,
        )

    async def delete_source(self, config: dict, item: ExternalItemRef) -> ExternalMutationResult:
        """Delete the Dropbox file."""
        if not self.capabilities().delete_source:
            raise UnsupportedExternalOperation("delete_source is not enabled for this Dropbox library")

        full_path = self._full_path(config, item.provider_key)

        async with self._client(config) as client:
            response = await self._post(
                client,
                config,
                f"{_API_BASE}/2/files/delete_v2",
                json={"path": full_path},
            )
            if response.is_error:
                self._raise_for_item(response, full_path, item.provider_key)

        return ExternalMutationResult(provider_key=item.provider_key)

    async def healthcheck(self, config: dict) -> ExternalHealth:
        """Check that the Dropbox credentials and root are usable."""
        try:
            async with self._client(config) as client:
                account = await self._current_account(client, config)
                await self._validate_root(client, config)
        except (httpx.HTTPError, ExternalLibraryError) as exc:
            return ExternalHealth(ok=False, message=str(exc))

        email = (account.get("email") or "").strip()
        name = ((account.get("name") or {}).get("display_name") or "").strip()
        identity = email or name or "authenticated account"
        root = self._root(config)
        location = f"/{root}" if root else "account root"
        return ExternalHealth(ok=True, message=f"Dropbox {location} is accessible for {identity}")
