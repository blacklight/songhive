"""
Thin async wrapper around ``tidalapi``'s synchronous request layer.

Every call runs in a worker thread (``asyncio.to_thread``) and is paced by
a per-client token bucket so a single account never exceeds the configured
request rate. Raw JSON (not parsed ``tidalapi`` objects) is returned so the
full payload can be written to the provider catalog unchanged.

Provider errors are normalized onto ``external.errors`` exceptions so the
sync pipeline and Celery ``autoretry_for`` behave consistently.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlsplit

import requests

from ..errors import (
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
    ExternalRateLimited,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RPS = 5.0
_PAGE_LIMIT = 200

try:  # pragma: no cover - exercised indirectly in most tests
    from tidalapi import exceptions as tidal_exceptions
except ImportError:  # pragma: no cover
    tidal_exceptions = None  # type: ignore[assignment]


class NotModified(Exception):
    """Sentinel raised internally when a conditional GET returns HTTP 304."""


@dataclass(frozen=True)
class ApiResponse:
    """JSON body plus response metadata for a TIDAL API call."""

    data: Any
    etag: Optional[str] = None


def is_allowed_media_url(url: str, *, extra_hosts: tuple[str, ...] = ()) -> bool:
    """
    Return whether ``url`` is safe to proxy/redirect for TIDAL media.

    Manifest and CDN URLs must be HTTPS on ``tidal.com`` (or an explicitly
    allow-listed CDN host) — tokens are never forwarded and nothing
    user-supplied reaches this point, but the check is cheap insurance
    against a compromised or buggy provider response.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    if host == "tidal.com" or host.endswith(".tidal.com"):
        return True
    return host in {h.lower() for h in extra_hosts}


class TidalApiClient:
    """Raw-JSON TIDAL API client bound to a restored session."""

    def __init__(self, session: Any, *, max_rps: float = _DEFAULT_MAX_RPS) -> None:
        self._session = session
        self._min_interval = 1.0 / max(max_rps, 0.1)
        self._last_request_at = 0.0
        self._throttle_lock: Optional[asyncio.Lock] = None

    @property
    def session(self) -> Any:
        return self._session

    async def _throttle(self) -> None:
        """Enforce a minimum interval between outbound requests."""
        if self._throttle_lock is None:
            self._throttle_lock = asyncio.Lock()
        async with self._throttle_lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    @staticmethod
    def _translate_error(exc: BaseException, path: str) -> BaseException:
        """Map tidalapi/requests errors onto external-library errors."""
        if tidal_exceptions is not None:
            if isinstance(exc, tidal_exceptions.TooManyRequests):
                retry_after = getattr(exc, "retry_after", None)
                return ExternalRateLimited(
                    f"TIDAL rate limit on {path}",
                    retry_after=float(retry_after) if isinstance(retry_after, (int, float)) else None,
                )
            if isinstance(exc, tidal_exceptions.ObjectNotFound):
                return ExternalItemNotFound(f"TIDAL object not found: {path}")
            if isinstance(exc, tidal_exceptions.StreamNotAvailable):
                return ExternalItemNotFound(f"TIDAL stream is unavailable: {path}")
            if isinstance(exc, tidal_exceptions.AuthenticationError):
                return ExternalPermissionDenied(
                    "TIDAL credentials were rejected",
                    operation="request",
                )
            if isinstance(exc, tidal_exceptions.TidalAPIError):
                return ExternalLibraryError(f"TIDAL API error on {path}: {exc.__class__.__name__}")
        if isinstance(exc, requests.HTTPError):
            status = exc.response.status_code if exc.response is not None else None
            if status == 429:
                retry = exc.response.headers.get("Retry-After") if exc.response is not None else None
                try:
                    retry_after = float(retry) if retry else None
                except (TypeError, ValueError):
                    retry_after = None
                return ExternalRateLimited(f"TIDAL rate limit on {path}", retry_after=retry_after)
            if status in (401, 403):
                return ExternalPermissionDenied(
                    f"TIDAL authorization failed on {path} ({status})",
                    operation="request",
                )
            if status == 404:
                return ExternalItemNotFound(f"TIDAL object not found: {path}")
            return ExternalLibraryError(f"TIDAL request {path} failed ({status})")
        if isinstance(exc, requests.RequestException):
            return ExternalLibraryError(f"TIDAL request {path} failed: {exc.__class__.__name__}")
        return ExternalLibraryError(f"TIDAL request {path} failed: {exc.__class__.__name__}")

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        etag: Optional[str] = None,
    ) -> ApiResponse:
        """Perform one throttled request and return the decoded JSON body."""
        request_headers = dict(headers or {})
        if etag:
            request_headers["If-None-Match"] = etag

        await self._throttle()

        def _do_request() -> ApiResponse:
            response = self._session.request.request(
                method,
                path,
                params,
                data,
                request_headers,
            )
            if response.status_code == 304:
                raise NotModified()
            try:
                payload = response.json()
            except ValueError as exc:
                raise ExternalLibraryError(f"TIDAL response for {path} was not valid JSON") from exc
            return ApiResponse(
                data=payload,
                etag=response.headers.get("ETag") or response.headers.get("etag"),
            )

        try:
            return await asyncio.to_thread(_do_request)
        except NotModified:
            raise
        except Exception as exc:
            raise self._translate_error(exc, path) from exc

    async def get_json(
        self,
        path: str,
        *,
        params: Optional[dict] = None,
        etag: Optional[str] = None,
    ) -> ApiResponse:
        """GET ``path`` and return the decoded JSON body + response etag."""
        return await self.request("GET", path, params=params, etag=etag)

    async def get_json_or_none(
        self,
        path: str,
        *,
        params: Optional[dict] = None,
    ) -> Optional[ApiResponse]:
        """GET ``path``, returning ``None`` on 304/404 instead of raising."""
        try:
            return await self.get_json(path, params=params)
        except NotModified:
            return None
        except ExternalItemNotFound:
            return None

    async def iter_paged(
        self,
        path: str,
        *,
        params: Optional[dict] = None,
        limit: int = _PAGE_LIMIT,
        item_path: Optional[str] = None,
        max_items: Optional[int] = None,
    ) -> AsyncIterator[dict]:
        """
        Yield items from a ``limit``/``offset``-paged TIDAL endpoint.

        The response may be a bare list, ``{"items": [...]}``, or the
        ``{"items": [{"item": {...}}]}`` envelope TIDAL uses for favorites —
        ``item_path="item"`` unwraps the latter while keeping positions
        dense. ``totalNumberOfItems`` short-circuits the paging loop when
        present.
        """
        offset = 0
        yielded = 0
        while True:
            page_params = dict(params or {})
            page_params.setdefault("limit", limit)
            page_params["offset"] = offset
            response = await self.get_json(path, params=page_params)
            payload = response.data

            if isinstance(payload, list):
                items = payload
                total = None
            elif isinstance(payload, dict):
                items = payload.get("items") or []
                total = payload.get("totalNumberOfItems")
            else:
                items = []
                total = None
            if not isinstance(items, list) or not items:
                break

            for entry in items:
                if item_path and isinstance(entry, dict):
                    entry = entry.get(item_path) or {}
                if not isinstance(entry, dict):
                    continue
                yield entry
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return

            fetched = offset + len(items)
            if isinstance(total, int):
                # With a known total, a short page just means the provider
                # honored a smaller page size — keep paging until fetched
                # reaches the total (or an empty page lands, handled above).
                if fetched >= total:
                    break
            elif len(items) < limit:
                break
            offset += len(items)

    # ------------------------------------------------------------------
    # Convenience endpoints (v1 API paths)
    # ------------------------------------------------------------------

    def favorites_path(self, kind: str) -> str:
        """Return the favorites path for a kind ("tracks"|"albums"|"artists"|"playlists")."""
        user_id = self._session.user.id
        return f"users/{user_id}/favorites/{kind}"

    def playlist_items_path(self, playlist_uuid: str) -> str:
        return f"playlists/{playlist_uuid}/items"

    def album_items_path(self, album_id: str) -> str:
        return f"albums/{album_id}/items"

    def playback_info_path(self, track_id: str) -> str:
        return f"tracks/{track_id}/playbackinfopostpaywall"


def decode_bts_manifest(manifest_b64: str) -> Optional[dict]:
    """Decode a base64 ``application/vnd.tidal.bts`` manifest into JSON."""
    import base64

    try:
        raw = base64.b64decode(manifest_b64)
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def decode_mpd_manifest(manifest_b64: str) -> Optional[str]:
    """Decode a base64 ``application/dash+xml`` manifest into MPD XML text."""
    import base64

    try:
        raw = base64.b64decode(manifest_b64)
        text = raw.decode("utf-8")
    except (ValueError, TypeError):
        return None
    return text if "<MPD" in text else None
