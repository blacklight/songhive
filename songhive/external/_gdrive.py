"""
Google Drive external-library adapter.

Indexes audio files stored in a Google Drive folder (optionally a shared
drive) and serves them through proxied HTTP streams. All listing, download,
and mutation traffic originates server-side via the Drive API v3 — the
Songhive instance must be able to reach ``googleapis.com``.

Items are addressed by their Drive file ID (``provider_key``), which stays
stable across renames and moves; ``display_path`` carries the human-readable
folder path reported by the last listing. Because Drive cannot be watched
like a local directory, change detection relies on the ``md5Checksum``
reported by ``files.list`` (falling back to ``modifiedTime``/``size``):
scheduled syncs walk the configured root folder and only download files
whose identity changed, so unchanged trees never incur per-file data
transfer.

Authentication accepts either a service account key (``service_account_key``,
a JSON blob with ``client_email``/``private_key``), OAuth user credentials
(``client_id``/``client_secret``/``refresh_token``), or a static
``access_token``. Service account access tokens are minted locally with an
RS256 JWT bearer grant; OAuth tokens are refreshed through the token
endpoint. Service account libraries are read-only unless one of the write
flags is enabled, in which case the full ``drive`` scope is requested.
"""

import asyncio
import base64
import dataclasses
import fnmatch
import hashlib
import json
import logging
import mimetypes
import os
import re
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

_DEFAULT_EXTENSIONS = frozenset(AUDIO_EXTENSIONS)
_CHUNK_SIZE = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30

_API_BASE = "https://www.googleapis.com/drive/v3"
_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

_FOLDER_MIME = "application/vnd.google-apps.folder"
_GOOGLE_APPS_MIME_PREFIX = "application/vnd.google-apps."

_SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
_SCOPE_FULL = "https://www.googleapis.com/auth/drive"


def _drive_scope_for_config(config: dict) -> str:
    """Return the Drive OAuth scope the submitted config needs."""
    if config.get("allow_write_tags") or config.get("allow_rename_source") or config.get("allow_delete_source"):
        return _SCOPE_FULL
    return _SCOPE_READONLY


# OAuth authorization-code flow spec powering the UI "Connect" button.
# ``access_type=offline`` + ``prompt=consent`` make Google always return a
# durable refresh token, including on re-connects after scope changes. The
# requested scope follows the write flags in the submitted config.
GDRIVE_OAUTH_SPEC = OAuthProviderSpec(
    provider_type="gdrive",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    client_id_field="client_id",
    client_secret_field="client_secret",
    scopes_for_config=lambda config: (_drive_scope_for_config(config),),
    extra_authorize_params={"access_type": "offline", "prompt": "consent"},
    token_fields={
        "access_token": "access_token",
        "refresh_token": "refresh_token",
    },
)

_LIST_FIELDS = "nextPageToken, files(id, name, mimeType, size, modifiedTime, md5Checksum)"
_ITEM_FIELDS = "id, name, mimeType, size, modifiedTime, md5Checksum"

# Drive file IDs are opaque URL-safe strings; anything outside this alphabet
# is rejected before it can reach a URL path or query literal.
_FILE_ID_RE = re.compile(r"[A-Za-z0-9._~+-]+")

# Refresh access tokens this far ahead of their stated expiry.
_TOKEN_EXPIRY_SKEW_SECONDS = 60

# Cap concurrent ffmpeg invocations across all Google Drive library syncs.
_FFMPEG_SEMAPHORE = asyncio.Semaphore(2)


def _b64url(data: bytes) -> str:
    """Return URL-safe base64 without padding (JWT encoding)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class GoogleDriveExternalAdapter(ExternalLibraryAdapter):
    """Adapter that indexes audio files from a Google Drive folder."""

    provider_type = "gdrive"
    user_configurable = True

    def __init__(self) -> None:
        super().__init__()
        self._token_cache: Optional[tuple[str, str, float]] = None

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _auth_mode(config: dict) -> str:
        """Return the credential mode: ``service_account``, ``oauth``, or ``token``."""
        if config.get("service_account_key") is not None:
            return "service_account"
        if config.get("refresh_token"):
            if not config.get("client_id") or not config.get("client_secret"):
                raise ExternalConfigError(
                    'config["refresh_token"] requires "client_id" and "client_secret"',
                    field="client_id",
                )
            return "oauth"
        if config.get("access_token"):
            return "token"
        raise ExternalConfigError(
            'config requires "service_account_key", "refresh_token" with '
            '"client_id"/"client_secret", or "access_token"',
            field="access_token",
        )

    @staticmethod
    def _access_token_value(config: dict) -> str:
        token = config.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise ExternalConfigError(
                'config["access_token"] is required and must be a non-empty string',
                field="access_token",
            )
        return token.strip()

    @staticmethod
    def _service_account_info(config: dict) -> dict:
        """Parse and validate the service account JSON blob."""
        raw = config.get("service_account_key")
        info: Any = raw
        if isinstance(raw, str) and raw.strip():
            try:
                info = json.loads(raw)
            except ValueError as exc:
                raise ExternalConfigError(
                    'config["service_account_key"] must contain a JSON object',
                    field="service_account_key",
                ) from exc
        if not isinstance(info, dict):
            raise ExternalConfigError(
                'config["service_account_key"] must be a non-empty JSON object',
                field="service_account_key",
            )
        for key in ("client_email", "private_key"):
            if not isinstance(info.get(key), str) or not info[key].strip():
                raise ExternalConfigError(
                    f'config["service_account_key"] is missing "{key}"',
                    field="service_account_key",
                )
        return info

    @staticmethod
    def _token_uri(config: dict, info: Optional[dict] = None) -> str:
        """Return the OAuth token endpoint, honouring explicit overrides."""
        raw = config.get("token_uri") or (info or {}).get("token_uri") or _DEFAULT_TOKEN_URI
        if not isinstance(raw, str) or not raw.strip():
            raise ExternalConfigError(
                'config["token_uri"] must be a non-empty string',
                field="token_uri",
            )
        uri = raw.strip()
        parsed = httpx.URL(uri)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            raise ExternalConfigError(
                'config["token_uri"] must be a valid http(s) URL',
                field="token_uri",
            )
        return uri

    @staticmethod
    def _drive_scope(config: dict) -> str:
        """Return the OAuth scope a service account should request."""
        return _drive_scope_for_config(config)

    @staticmethod
    def _root_folder_id(config: dict) -> str:
        """Return the folder ID listings start from (``root`` = My Drive root)."""
        raw = config.get("root_folder_id")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            drive_id = config.get("drive_id")
            if isinstance(drive_id, str) and drive_id.strip():
                # A shared drive's root folder carries the drive's own ID.
                return drive_id.strip()
            return "root"
        if not isinstance(raw, str):
            raise ExternalConfigError(
                'config["root_folder_id"] must be a string',
                field="root_folder_id",
            )
        return raw.strip()

    @staticmethod
    def _drive_id(config: dict) -> Optional[str]:
        drive_id = config.get("drive_id")
        if isinstance(drive_id, str) and drive_id.strip():
            return drive_id.strip()
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

    @staticmethod
    def _api_base(config: dict) -> str:
        raw = config.get("api_base") or _API_BASE
        if not isinstance(raw, str):
            raise ExternalConfigError(
                'config["api_base"] must be a string',
                field="api_base",
            )
        return raw.strip().rstrip("/")

    @staticmethod
    def _upload_base(config: dict) -> str:
        raw = config.get("upload_base") or _UPLOAD_BASE
        if not isinstance(raw, str):
            raise ExternalConfigError(
                'config["upload_base"] must be a string',
                field="upload_base",
            )
        return raw.strip().rstrip("/")

    # ------------------------------------------------------------------
    # Item helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _file_id(provider_key: str) -> str:
        """Validate a provider key as a Drive file ID."""
        if not provider_key:
            raise ExternalItemNotFound("Empty provider key", provider_key=provider_key)
        if not _FILE_ID_RE.fullmatch(provider_key):
            raise ExternalPermissionDenied(
                f"Invalid provider key: {provider_key}",
                operation="resolve_file_id",
            )
        return provider_key

    @staticmethod
    def _escape_query(value: str) -> str:
        """Escape a string literal for a Drive ``q`` query clause."""
        return value.replace("\\", "\\\\").replace("'", "\\'")

    @staticmethod
    def _parse_rfc3339(text: Any) -> Optional[datetime]:
        """Parse an RFC 3339 timestamp (``2024-01-02T03:04:05.000Z``)."""
        if not isinstance(text, str) or not text.strip():
            return None
        try:
            parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _parse_size(value: Any) -> Optional[int]:
        """Parse a Drive ``size`` field (a decimal string)."""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _scope_folder_id(self, config: dict, scope: Optional[str]) -> str:
        """Return the folder ID a scoped sync should start from."""
        if not scope:
            return self._root_folder_id(config)
        clean = scope.strip().strip("/")
        if not clean or not _FILE_ID_RE.fullmatch(clean):
            raise ExternalConfigError(
                f"Scope {scope!r} is invalid",
                field="scope",
            )
        return clean

    @staticmethod
    def _is_excluded(display_path: str, name: str, exclude: list[str]) -> bool:
        """Return True when the display path or name matches an exclude pattern."""
        return any(fnmatch.fnmatch(display_path, pattern) or fnmatch.fnmatch(name, pattern) for pattern in exclude)

    @staticmethod
    def _mime_for_name(name: str) -> str:
        mime_type = mimetypes.guess_type(name)[0]
        if mime_type is None:
            suffix = PurePosixPath(name).suffix.lstrip(".").lower()
            mime_type = f"audio/{suffix}" if suffix else "application/octet-stream"
        return mime_type

    def _list_params(self, config: dict) -> dict[str, Any]:
        params: dict[str, Any] = {
            "fields": _LIST_FIELDS,
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "spaces": "drive",
        }
        drive_id = self._drive_id(config)
        if drive_id:
            params["corpora"] = "drive"
            params["driveId"] = drive_id
        return params

    @staticmethod
    def _item_params() -> dict[str, Any]:
        return {"fields": _ITEM_FIELDS, "supportsAllDrives": "true"}

    @staticmethod
    def _etag_for_file(file_meta: dict, mtime: Optional[datetime], size: Optional[int]) -> Optional[str]:
        """Return a change token for a listed file."""
        md5 = file_meta.get("md5Checksum")
        if isinstance(md5, str) and md5:
            return md5
        if mtime is not None and size is not None:
            return f"{mtime.timestamp()}:{size}"
        return None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _service_account_assertion(self, config: dict) -> str:
        """Build a signed RS256 JWT for the service account token grant."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        info = self._service_account_info(config)
        token_uri = self._token_uri(config, info)
        now = int(time.time())
        segments = [
            {"alg": "RS256", "typ": "JWT"},
            {
                "iss": info["client_email"].strip(),
                "scope": self._drive_scope(config),
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            },
        ]
        signing_input = ".".join(
            _b64url(json.dumps(segment, separators=(",", ":")).encode("utf-8")) for segment in segments
        )
        try:
            key = serialization.load_pem_private_key(
                info["private_key"].encode("utf-8"),
                password=None,
            )
        except ValueError as exc:
            raise ExternalConfigError(
                'config["service_account_key"] private_key is not a valid PEM key',
                field="service_account_key",
            ) from exc
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ExternalConfigError(
                'config["service_account_key"] private_key must be an RSA private key',
                field="service_account_key",
            )
        signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input}.{_b64url(signature)}"

    def _auth_fingerprint(self, config: dict) -> str:
        """Return a fingerprint of the credential material for cache keying."""
        mode = self._auth_mode(config)
        payload = repr(
            (
                mode,
                config.get("refresh_token"),
                config.get("client_id"),
                config.get("service_account_key"),
                config.get("token_uri"),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _access_token(self, client: httpx.AsyncClient, config: dict) -> str:
        """Return a valid bearer token, minting or refreshing when needed."""
        mode = self._auth_mode(config)
        if mode == "token":
            return self._access_token_value(config)

        fingerprint = self._auth_fingerprint(config)
        cached = self._token_cache
        if cached is not None and cached[0] == fingerprint and cached[2] > time.time() + _TOKEN_EXPIRY_SKEW_SECONDS:
            return cached[1]

        if mode == "oauth":
            data = {
                "grant_type": "refresh_token",
                "refresh_token": str(config["refresh_token"]),
                "client_id": str(config["client_id"]),
                "client_secret": str(config["client_secret"]),
            }
            token_uri = self._token_uri(config)
        else:
            info = self._service_account_info(config)
            data = {
                "grant_type": _JWT_BEARER_GRANT,
                "assertion": self._service_account_assertion(config),
            }
            token_uri = self._token_uri(config, info)

        response = await client.request("POST", token_uri, data=data)
        if response.is_error:
            message = self._error_message(response)
            raise ExternalPermissionDenied(
                f"Google OAuth token request failed ({response.status_code}): {message}",
                operation="access_token",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExternalPermissionDenied(
                "Google OAuth token request returned an invalid response",
                operation="access_token",
            ) from exc
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ExternalPermissionDenied(
                "Google OAuth token response did not include an access_token",
                operation="access_token",
            )
        try:
            expires_in = int(payload.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600
        self._token_cache = (fingerprint, token, time.time() + expires_in)
        return token

    def invalidate_token_cache(self) -> None:
        """Drop the cached access token (used after a 401)."""
        self._token_cache = None

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _client(self, config: dict) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an httpx client configured for the Drive API."""
        async with httpx.AsyncClient(
            verify=self._verify(config),
            timeout=self._timeout(config),
            follow_redirects=True,
        ) as client:
            yield client

    async def _request(
        self,
        client: httpx.AsyncClient,
        config: dict,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        retry: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send an authorized API request, refreshing the token once on 401."""
        token = await self._access_token(client, config)
        merged = dict(headers or {})
        merged["Authorization"] = f"Bearer {token}"
        response = await client.request(method, url, headers=merged, **kwargs)
        if response.status_code == 401 and retry and self._auth_mode(config) != "token":
            self.invalidate_token_cache()
            response = await self._request(client, config, method, url, headers=headers, retry=False, **kwargs)
        return response

    @asynccontextmanager
    async def _stream(
        self,
        client: httpx.AsyncClient,
        config: dict,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        retry: bool = True,
    ) -> AsyncIterator[httpx.Response]:
        """Open an authorized streaming GET, refreshing the token once on 401."""
        token = await self._access_token(client, config)
        merged = dict(headers or {})
        merged["Authorization"] = f"Bearer {token}"
        async with client.stream("GET", url, headers=merged) as response:
            if response.status_code == 401 and retry and self._auth_mode(config) != "token":
                self.invalidate_token_cache()
                async with self._stream(client, config, url, headers=headers, retry=False) as retried:
                    yield retried
                return
            yield response

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        """Extract the Drive API error message, falling back to the status."""
        try:
            data = response.json()
        except Exception:
            return f"HTTP {response.status_code}"
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        if isinstance(error, str) and error:
            description = data.get("error_description")
            return f"{error}: {description}" if isinstance(description, str) and description else error
        return f"HTTP {response.status_code}"

    def _raise_for_listing(self, response: httpx.Response, message: str) -> None:
        """Raise a config-level exception for a failed Drive listing."""
        status = response.status_code
        detail = self._error_message(response)
        if status == 404 or status == 410:
            raise ExternalConfigError(
                f"Remote folder not found: {message} ({detail})",
                field="root_folder_id",
            )
        if status in (401, 403):
            raise ExternalPermissionDenied(
                f"Google Drive permission denied: {message} ({detail})",
                operation="gdrive",
            )
        raise ExternalConfigError(
            f"Google Drive request failed ({status}): {message} ({detail})",
            field="root_folder_id",
        )

    def _raise_for_item(self, response: httpx.Response, provider_key: str) -> None:
        """Raise an item-level exception for a failed Drive operation."""
        status = response.status_code
        detail = self._error_message(response)
        if status == 404 or status == 410:
            raise ExternalItemNotFound(
                f"Remote file not found: {provider_key} ({detail})",
                provider_key=provider_key,
            )
        if status in (401, 403):
            raise ExternalPermissionDenied(
                f"Google Drive permission denied: {provider_key} ({detail})",
                operation="gdrive",
            )
        if status == 409:
            raise ExternalWriteBackError(
                f"Google Drive conflict: {provider_key} ({detail})",
                provider_key=provider_key,
            )
        raise ExternalLibraryError(f"Google Drive request failed ({status}): {provider_key} ({detail})")

    async def _list_children(
        self,
        client: httpx.AsyncClient,
        config: dict,
        folder_id: str,
    ) -> AsyncIterator[dict]:
        """Yield the non-trashed children of a folder, paging transparently."""
        params = self._list_params(config)
        params["q"] = f"'{self._escape_query(folder_id)}' in parents and trashed = false"
        page_token: Optional[str] = None
        while True:
            if page_token:
                params["pageToken"] = page_token
            response = await self._request(
                client,
                config,
                "GET",
                f"{self._api_base(config)}/files",
                params=dict(params),
            )
            if response.is_error:
                self._raise_for_listing(response, f"folder {folder_id}")
            try:
                data = response.json()
            except ValueError as exc:
                raise ExternalConfigError(
                    "Invalid Google Drive listing response",
                    field="root_folder_id",
                ) from exc
            for child in data.get("files", []):
                yield child
            page_token = data.get("nextPageToken")
            if not page_token:
                break

    async def _get_file(self, client: httpx.AsyncClient, config: dict, file_id: str) -> dict:
        """Fetch a file's metadata resource."""
        response = await self._request(
            client,
            config,
            "GET",
            f"{self._api_base(config)}/files/{file_id}",
            params=self._item_params(),
        )
        if response.is_error:
            self._raise_for_item(response, file_id)
        try:
            data = response.json()
        except ValueError as exc:
            raise ExternalLibraryError(f"Invalid Google Drive metadata response for {file_id}") from exc
        return data

    def _download_url(self, config: dict, file_id: str) -> str:
        """Return the ``alt=media`` download URL for a file."""
        return f"{self._api_base(config)}/files/{file_id}?alt=media&supportsAllDrives=true"

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
        item: ExternalItemRef,
    ) -> Path:
        """Download a Drive file to a local temp file and return its path."""
        file_id = self._file_id(item.provider_key)
        url = self._download_url(config, file_id)
        temp_dir = await self._stream_temp_dir()
        suffix = PurePosixPath(item.display_path or item.provider_key).suffix
        fd, tmp_name = tempfile.mkstemp(
            dir=str(temp_dir) if temp_dir else None,
            prefix="gdrive-ext-",
            suffix=suffix,
        )
        os.close(fd)
        os.chmod(tmp_name, 0o600)
        tmp_path = Path(tmp_name)

        try:
            async with self._stream(client, config, url) as response:
                if response.is_error:
                    await response.aread()
                    self._raise_for_item(response, item.provider_key)
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
        """Validate credentials and the configured root folder."""
        self._auth_mode(config)
        root_id = self._root_folder_id(config)
        try:
            self._file_id(root_id)
        except ExternalPermissionDenied as exc:
            raise ExternalConfigError(
                f"Invalid folder ID: {root_id}",
                field="root_folder_id",
            ) from exc

        try:
            async with self._client(config) as client:
                meta = await self._get_file(client, config, root_id)
        except ExternalItemNotFound as exc:
            raise ExternalConfigError(
                f"Remote folder not found: {root_id}",
                field="root_folder_id",
            ) from exc
        except (ExternalConfigError, ExternalPermissionDenied):
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ExternalConfigError(
                f"Cannot connect to the Google Drive API: {exc}",
                field="api_base",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalConfigError(
                f"Cannot access Google Drive folder {root_id!r}: {exc}",
                field="root_folder_id",
            ) from exc

        if meta.get("mimeType") != _FOLDER_MIME:
            raise ExternalConfigError(
                f"Remote root is not a folder: {root_id}",
                field="root_folder_id",
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
            limits={"checksum_algorithm": "md5"},
        )
        assert self._capabilities  # for mypy
        return self._capabilities

    async def iter_items(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalItemRef]:
        """Yield audio files under the configured Drive folder.

        ``scope`` is a Drive folder ID to restrict the walk to a subtree.
        """
        root_id = self._scope_folder_id(config, scope)

        extensions = frozenset(config.get("extensions", _DEFAULT_EXTENSIONS))
        extension_set = {ext.lstrip(".").lower() for ext in extensions}
        recursive = bool(config.get("recursive", True))
        exclude = list(config.get("exclude", []))

        seen_folders: set[str] = set()
        seen_files: set[str] = set()

        try:
            async with self._client(config) as client:
                stack: list[tuple[str, str]] = [(root_id, "")]
                while stack:
                    folder_id, prefix = stack.pop()
                    if folder_id in seen_folders:
                        continue
                    seen_folders.add(folder_id)

                    children = [child async for child in self._list_children(client, config, folder_id)]
                    children.sort(key=lambda child: child.get("name") or "")

                    pending_folders: list[tuple[str, str]] = []
                    for child in children:
                        child_id = child.get("id")
                        name = child.get("name") or ""
                        mime_type = child.get("mimeType") or ""
                        if not isinstance(child_id, str) or not child_id or not name:
                            continue

                        display_path = f"{prefix}/{name}" if prefix else name

                        if mime_type == _FOLDER_MIME:
                            if (
                                recursive
                                and child_id not in seen_folders
                                and not self._is_excluded(display_path, name, exclude)
                            ):
                                pending_folders.append((child_id, display_path))
                            continue

                        # Google-native documents and shortcuts cannot be
                        # downloaded through alt=media.
                        if mime_type.startswith(_GOOGLE_APPS_MIME_PREFIX):
                            continue

                        suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                        if suffix not in extension_set:
                            continue
                        if self._is_excluded(display_path, name, exclude):
                            continue

                        mtime = self._parse_rfc3339(child.get("modifiedTime"))
                        if since is not None and mtime is not None and mtime <= since:
                            continue
                        if child_id in seen_files:
                            continue
                        seen_files.add(child_id)

                        size = self._parse_size(child.get("size"))
                        md5 = child.get("md5Checksum")
                        yield ExternalItemRef(
                            provider_key=child_id,
                            display_path=display_path,
                            etag=self._etag_for_file(child, mtime, size),
                            mtime=mtime,
                            size=size,
                            mime_type=mime_type or self._mime_for_name(name),
                            checksum=md5 if isinstance(md5, str) else None,
                            sha256=None,
                        )

                    for entry in reversed(pending_folders):
                        stack.append(entry)
        except ExternalLibraryError:
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ExternalConfigError(
                f"Cannot connect to the Google Drive API: {exc}",
                field="api_base",
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalConfigError(
                f"Cannot list Google Drive folder {root_id!r}: {exc}",
                field="root_folder_id",
            ) from exc

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        """Download a Drive file to a temp file and read its embedded tags."""
        async with self._client(config) as client:
            tmp_path = await self._download_to_temp(client, config, item)

        metadata = None

        try:
            from ._audio import track_metadata_from_file

            metadata = track_metadata_from_file(tmp_path, item.display_path or item.provider_key)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        assert metadata  # for mypy
        raw = dict(metadata.raw_metadata or {})
        mimetype = raw.get("mimetype") or item.mime_type
        raw["mimetype"] = mimetype
        raw["file_id"] = item.provider_key
        return dataclasses.replace(metadata, raw_metadata=raw)

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Return a proxied byte iterator over the Drive file."""
        file_id = self._file_id(item.provider_key)
        url = self._download_url(config, file_id)
        content_type = item.mime_type or self._mime_for_name(item.display_path or item.provider_key)

        size: Optional[int] = item.size
        request_headers: dict[str, str] = {}
        if range is not None:
            start, end = range
            request_headers["Range"] = f"bytes={start}-{end}"
            size = end - start + 1

        async def _iter_drive() -> AsyncIterator[bytes]:
            async with self._client(config) as client:
                async with self._stream(client, config, url, headers=request_headers) as response:
                    if response.is_error:
                        await response.aread()
                        self._raise_for_item(response, item.provider_key)
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        yield chunk

        return ExternalStream(
            kind="iterator",
            iterator=_iter_drive(),
            content_type=content_type,
            size=size,
            supports_range=True,
            headers={},
            temporary=False,
        )

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Return a complete byte stream for the Drive file."""
        return await self.open_stream(config, item)

    async def compute_sha256(self, config: dict, item: ExternalItemRef) -> str:
        """Compute the item's audio hash, downloading when necessary."""
        file_id = self._file_id(item.provider_key)
        url = self._download_url(config, file_id)

        if config.get("fast_hash"):
            hasher = hashlib.sha256()
            async with self._client(config) as client:
                async with self._stream(client, config, url) as response:
                    if response.is_error:
                        await response.aread()
                        self._raise_for_item(response, item.provider_key)
                    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                        hasher.update(chunk)
            return hasher.hexdigest()

        async with self._client(config) as client:
            tmp_path = await self._download_to_temp(client, config, item)

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
        """Rewrite the Drive file's embedded tags and re-upload it in place."""
        if not self.capabilities().write_tags:
            raise UnsupportedExternalOperation("write_tags is not enabled for this Google Drive library")

        file_id = self._file_id(item.provider_key)

        async with self._client(config) as client:
            tmp_path = await self._download_to_temp(client, config, item)

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

                def _write() -> bytes:
                    write_metadata(tmp_path, write_obj)
                    return tmp_path.read_bytes()

                data = await asyncio.to_thread(_write)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            response = await self._request(
                client,
                config,
                "PATCH",
                f"{self._upload_base(config)}/files/{file_id}",
                params={
                    "uploadType": "media",
                    "fields": _ITEM_FIELDS,
                    "supportsAllDrives": "true",
                },
                content=data,
                headers={"Content-Type": item.mime_type or "application/octet-stream"},
            )
            if response.is_error:
                self._raise_for_item(response, item.provider_key)

        mtime = None
        etag = None
        try:
            file_meta = response.json()
            etag = file_meta.get("md5Checksum") if isinstance(file_meta.get("md5Checksum"), str) else None
            mtime = self._parse_rfc3339(file_meta.get("modifiedTime"))
        except ValueError:
            pass

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
        """Rename the Drive file in place, keeping its file ID."""
        if not self.capabilities().rename_source:
            raise UnsupportedExternalOperation("rename_source is not enabled for this Google Drive library")

        file_id = self._file_id(item.provider_key)
        new_basename = PurePosixPath(new_name.replace("\\", "/")).name
        if not new_basename or new_basename in (".", ".."):
            raise ExternalPermissionDenied(
                f"Invalid new name: {new_name!r}",
                operation="rename_source",
            )

        old_display = item.display_path or item.provider_key
        old_basename = PurePosixPath(old_display).name
        if new_basename == old_basename:
            return item

        parent = PurePosixPath(old_display).parent
        new_display = new_basename if str(parent) == "." else (parent / new_basename).as_posix()

        async with self._client(config) as client:
            response = await self._request(
                client,
                config,
                "PATCH",
                f"{self._api_base(config)}/files/{file_id}",
                params=self._item_params(),
                json={"name": new_basename},
            )
            if response.is_error:
                self._raise_for_item(response, item.provider_key)

        mtime = None
        etag = None
        size = item.size
        mime_type = item.mime_type
        try:
            file_meta = response.json()
            etag = file_meta.get("md5Checksum") if isinstance(file_meta.get("md5Checksum"), str) else None
            mtime = self._parse_rfc3339(file_meta.get("modifiedTime"))
            size = self._parse_size(file_meta.get("size")) or size
            mime_type = file_meta.get("mimeType") or mime_type
        except ValueError:
            pass

        return ExternalItemRef(
            provider_key=item.provider_key,
            display_path=new_display,
            etag=etag or item.etag,
            mtime=mtime or item.mtime,
            size=size,
            mime_type=mime_type or self._mime_for_name(new_basename),
            checksum=item.checksum,
            sha256=item.sha256,
        )

    async def delete_source(self, config: dict, item: ExternalItemRef) -> ExternalMutationResult:
        """Delete the Drive file, trashing it by default for recoverability."""
        if not self.capabilities().delete_source:
            raise UnsupportedExternalOperation("delete_source is not enabled for this Google Drive library")

        file_id = self._file_id(item.provider_key)

        async with self._client(config) as client:
            if config.get("trash_on_delete", True):
                response = await self._request(
                    client,
                    config,
                    "PATCH",
                    f"{self._api_base(config)}/files/{file_id}",
                    params={"fields": "id", "supportsAllDrives": "true"},
                    json={"trashed": True},
                )
            else:
                response = await self._request(
                    client,
                    config,
                    "DELETE",
                    f"{self._api_base(config)}/files/{file_id}",
                    params={"supportsAllDrives": "true"},
                )
            if response.is_error:
                self._raise_for_item(response, item.provider_key)

        return ExternalMutationResult(provider_key=item.provider_key)

    async def healthcheck(self, config: dict) -> ExternalHealth:
        """Check that the configured root folder is reachable and is a folder."""
        try:
            root_id = self._root_folder_id(config)
            self._file_id(root_id)
            async with self._client(config) as client:
                meta = await self._get_file(client, config, root_id)
        except (httpx.HTTPError, ExternalLibraryError) as exc:
            return ExternalHealth(ok=False, message=str(exc))

        if meta.get("mimeType") != _FOLDER_MIME:
            return ExternalHealth(ok=False, message=f"Remote root is not a folder: {root_id}")

        name = meta.get("name") or root_id
        return ExternalHealth(ok=True, message=f"Google Drive folder {name} is accessible")
