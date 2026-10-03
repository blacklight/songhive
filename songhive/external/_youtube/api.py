"""
YouTube client facade: YouTube Music (ytmusicapi) + Data API v3.

Which surface a library uses is decided once per credential set and cached
in Redis:

- ``music`` — the account has YouTube Music Premium (or was configured
  with browser cookies); ytmusicapi's innertube endpoints serve library
  data.
- ``youtube`` — plain Google account; the Data API v3 serves the same
  library concepts (``myRating=like`` for saved videos, ``playlists``,
  ``subscriptions``) under a Bearer token.
- ``auto`` (default) — probe Premium once via ``YTMusic.get_account_menu``;
  browser sessions always resolve to ``music`` since they cannot mint the
  Bearer token the Data API needs.

All YouTube calls run through a token-bucket throttle shared per config
fingerprint.
"""

import asyncio
import logging
from typing import Any, AsyncIterator, Optional

import requests
from redis.asyncio import Redis

from ..errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
    ExternalRateLimited,
)
from .conf import api_mode as configured_api_mode
from .conf import max_rps, request_timeout_seconds
from .mapping import account_menu_is_premium
from .session import _config_fingerprint, auth_mode, valid_access_token, ytmusic_for_config

logger = logging.getLogger(__name__)

DATA_API_BASE = "https://www.googleapis.com/youtube/v3"

# Redis keys
_MODE_CACHE_PREFIX = "songhive:youtube:api_mode:"
_MODE_CACHE_TTL = 3600
_THROTTLE_PREFIX = "songhive:youtube:token:"


class _TokenBucketThrottle:
    """Per-account token-bucket throttle (mirror of the TIDAL throttle)."""

    def __init__(self, redis: Redis, fingerprint: str, max_rps: float) -> None:
        self._redis = redis
        self._max_rps = max_rps
        self._key = f"{_THROTTLE_PREFIX}{fingerprint}"
        # INCR+EXPIRE rate limiting — good enough for a soft per-account cap.
        self._window = 10

    async def acquire(self) -> None:
        budget = int(self._max_rps * self._window)
        for _ in range(10):
            try:
                count = await self._redis.incr(self._key)
                if count == 1:
                    await self._redis.expire(self._key, self._window)
                if count <= budget:
                    return
            except Exception:
                return
            await asyncio.sleep(0.25)
        # If we can't honor the cap after retries, proceed anyway.

    def update_from_response(self, *_, **__) -> None:
        # The Data API does not expose rate headers; quota errors surface as
        # 403s and are translated by _raise_for_status.
        return


def _is_quota_error(response: requests.Response, data: dict) -> bool:
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    reasons = {err.get("reason") for err in (data.get("error") or {}).get("errors") or [] if isinstance(err, dict)}
    return bool(reasons & {"quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded"})


def _raise_for_status(response: requests.Response, operation: str) -> None:
    """Translate Data API error responses into external.* exceptions."""
    if response.status_code < 400:
        return
    try:
        data = response.json()
    except ValueError:
        data = {}
    if _is_quota_error(response, data):
        retry = response.headers.get("Retry-After")
        raise ExternalRateLimited(
            f"YouTube API quota exceeded ({operation}); sync will retry later",
            retry_after=float(retry) if retry else None,
        )
    if response.status_code == 404:
        raise ExternalItemNotFound(f"YouTube resource not found ({operation})")
    if response.status_code in (400, 401):
        raise ExternalPermissionDenied(
            f"YouTube rejected the request credentials ({operation})",
            operation=operation,
        )
    if response.status_code == 403:
        raise ExternalPermissionDenied(
            f"YouTube denied access ({operation}); the account may lack permission "
            "or the API may be disabled for the OAuth client",
            operation=operation,
        )
    raise ExternalLibraryError(f"YouTube API returned HTTP {response.status_code} ({operation})")


class _DataApiClient:
    """Async wrapper over ``requests`` for YouTube Data API v3 calls."""

    def __init__(
        self,
        config: dict,
        redis: Optional[Redis],
        throttle: Optional[_TokenBucketThrottle] = None,
    ) -> None:
        self._config = config
        self._redis = redis
        self._throttle = throttle
        self._timeout = request_timeout_seconds(config)

    async def _token(self) -> str:
        return await valid_access_token(self._config, self._redis)

    async def get_json(self, path: str, params: dict, *, operation: str) -> dict:
        token = await self._token()
        if self._throttle is not None:
            await self._throttle.acquire()
        url = f"{DATA_API_BASE}/{path.lstrip('/')}"
        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout,
            )
        except requests.Timeout as exc:
            raise ExternalLibraryError(f"YouTube request timed out ({operation})") from exc
        except requests.ConnectionError as exc:
            raise ExternalLibraryError(f"YouTube is unreachable ({operation})") from exc
        if self._throttle is not None:
            self._throttle.update_from_response(response)
        _raise_for_status(response, operation)
        data = response.json()
        if not isinstance(data, dict):
            raise ExternalLibraryError(f"Unexpected YouTube response ({operation})")
        return data

    async def iter_paged(
        self,
        path: str,
        params: dict,
        *,
        operation: str,
        items_key: str = "items",
    ) -> AsyncIterator[dict]:
        """Yield ``items_key`` entries across all result pages."""
        page_token: Optional[str] = None
        while True:
            page_params = dict(params)
            if page_token:
                page_params["pageToken"] = page_token
            data = await self.get_json(path, page_params, operation=operation)
            items = data.get(items_key)
            if not isinstance(items, list):
                return
            for item in items:
                if isinstance(item, dict):
                    yield item
            page_token = data.get("nextPageToken")
            if not page_token:
                return


class YouTubeClient:
    """Mode-aware facade over ytmusicapi and the YouTube Data API."""

    def __init__(self, config: dict, redis: Optional[Redis] = None) -> None:
        self.config = config
        self.redis = redis
        self._fingerprint = _config_fingerprint(config)
        self._throttle = _TokenBucketThrottle(redis, self._fingerprint, max_rps(config)) if redis is not None else None
        self._ytmusic: Optional[Any] = None
        self._data: Optional[_DataApiClient] = None
        self._mode: Optional[str] = None

    # -- mode resolution --------------------------------------------------

    async def _ytmusic_client(self) -> Any:
        if self._ytmusic is None:
            self._ytmusic = await ytmusic_for_config(self.config, self.redis)
        return self._ytmusic

    async def api_mode(self) -> str:
        """Resolve ``music`` or ``youtube`` for this credential set."""
        if self._mode is not None:
            return self._mode
        explicit = configured_api_mode(self.config)
        if explicit in ("music", "youtube"):
            self._mode = explicit
            return self._mode
        if self.redis is not None:
            try:
                cached = await self.redis.get(f"{_MODE_CACHE_PREFIX}{self._fingerprint}")
            except Exception:
                cached = None
            if isinstance(cached, str) and cached in ("music", "youtube"):
                self._mode = cached
                return self._mode
        self._mode = await self._resolve_mode()
        if self.redis is not None:
            try:
                await self.redis.set(
                    f"{_MODE_CACHE_PREFIX}{self._fingerprint}",
                    self._mode,
                    ex=_MODE_CACHE_TTL,
                )
            except Exception:
                pass
        return self._mode

    async def _resolve_mode(self) -> str:
        # Browser sessions can't mint the Bearer token the Data API needs.
        if auth_mode(self.config) == "browser":
            return "music"
        try:
            ytm = await self._ytmusic_client()
            menu = await asyncio.to_thread(ytm.get_account_menu)
        except Exception:
            # ytmusicapi auth broken — the Data API may still work.
            return "youtube"
        return "music" if account_menu_is_premium(menu) else "youtube"

    # -- library iteration ------------------------------------------------

    async def iter_liked_videos(self) -> AsyncIterator[dict]:
        """Saved/liked videos — the provider's base collection."""
        if await self.api_mode() == "music":
            ytm = await self._ytmusic_client()
            try:
                result = await asyncio.to_thread(ytm.get_liked_songs, 5000)
            except Exception as exc:
                raise ExternalLibraryError(f"YouTube liked-songs fetch failed: {exc}") from exc
            tracks = (result or {}).get("tracks") or []
            for track in tracks:
                if isinstance(track, dict) and track.get("videoId"):
                    yield {**track, "_source": "ytmusic"}
        else:
            data = self._data_client()
            async for item in data.iter_paged(
                "videos",
                {"part": "snippet,contentDetails", "myRating": "like", "maxResults": 50},
                operation="iter_liked_videos",
            ):
                yield {**item, "_source": "youtube_api"}

    async def iter_playlists(self) -> AsyncIterator[dict]:
        """The user's YouTube playlists (any source)."""
        if await self.api_mode() == "music":
            ytm = await self._ytmusic_client()
            try:
                playlists = await asyncio.to_thread(ytm.get_library_playlists, 5000)
            except Exception as exc:
                raise ExternalLibraryError(f"YouTube playlists fetch failed: {exc}") from exc
            for playlist in playlists or []:
                if isinstance(playlist, dict) and playlist.get("playlistId"):
                    yield {**playlist, "_source": "ytmusic"}
        else:
            data = self._data_client()
            async for item in data.iter_paged(
                "playlists",
                {"part": "snippet,contentDetails,status", "mine": "true", "maxResults": 50},
                operation="iter_playlists",
            ):
                yield {**item, "_source": "youtube_api"}

    async def iter_subscriptions(self) -> AsyncIterator[dict]:
        """The user's channel subscriptions (provider artists)."""
        if await self.api_mode() == "music":
            ytm = await self._ytmusic_client()
            try:
                channels = await asyncio.to_thread(ytm.get_library_subscriptions, 500)
            except Exception as exc:
                raise ExternalLibraryError(f"YouTube subscriptions fetch failed: {exc}") from exc
            for channel in channels or []:
                if isinstance(channel, dict):
                    yield {**channel, "_source": "ytmusic"}
        else:
            data = self._data_client()
            async for item in data.iter_paged(
                "subscriptions",
                {"part": "snippet,contentDetails", "mine": "true", "maxResults": 50},
                operation="iter_subscriptions",
            ):
                yield {**item, "_source": "youtube_api"}

    async def iter_albums(self) -> AsyncIterator[dict]:
        """Saved YouTube Music albums — empty for the youtube mode."""
        if await self.api_mode() != "music":
            return
        ytm = await self._ytmusic_client()
        try:
            albums = await asyncio.to_thread(ytm.get_library_albums, 1000)
        except Exception as exc:
            raise ExternalLibraryError(f"YouTube albums fetch failed: {exc}") from exc
        for album in albums or []:
            if isinstance(album, dict):
                yield {**album, "_source": "ytmusic"}

    # -- entity fetches ----------------------------------------------------

    async def fetch_video(self, video_id: str) -> dict:
        """Fetch one video's raw payload in whichever mode is active."""
        if await self.api_mode() == "music":
            ytm = await self._ytmusic_client()
            try:
                watch = await asyncio.to_thread(ytm.get_watch_playlist, video_id, limit=2)
            except Exception as exc:
                raise ExternalItemNotFound(f"YouTube video {video_id!r} not found") from exc
            tracks = (watch or {}).get("tracks") or []
            for track in tracks:
                if isinstance(track, dict) and track.get("videoId") == video_id:
                    return {**track, "_source": "ytmusic"}
            raise ExternalItemNotFound(f"YouTube video {video_id!r} not found")
        data = self._data_client()
        result = await data.get_json(
            "videos",
            {"part": "snippet,contentDetails", "id": video_id},
            operation="fetch_video",
        )
        items = result.get("items") or []
        if not items:
            raise ExternalItemNotFound(f"YouTube video {video_id!r} not found")
        return {**items[0], "_source": "youtube_api"}

    async def fetch_playlist_contents(self, playlist_id: str) -> dict:
        """Fetch a playlist's metadata + entries in active mode."""
        if await self.api_mode() == "music":
            ytm = await self._ytmusic_client()
            try:
                result = await asyncio.to_thread(ytm.get_playlist, playlist_id, 2000)
            except Exception as exc:
                raise ExternalItemNotFound(f"YouTube playlist {playlist_id!r} not found") from exc
            if not isinstance(result, dict):
                raise ExternalItemNotFound(f"YouTube playlist {playlist_id!r} not found")
            return {**result, "_source": "ytmusic"}

        data = self._data_client()
        meta = await data.get_json(
            "playlists",
            {"part": "snippet,contentDetails,status", "id": playlist_id},
            operation="fetch_playlist",
        )
        items = meta.get("items") or []
        if not items:
            raise ExternalItemNotFound(f"YouTube playlist {playlist_id!r} not found")
        entries: list[dict] = []
        async for entry in data.iter_paged(
            "playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50,
            },
            operation="fetch_playlist_items",
        ):
            entries.append(entry)
        return {
            **items[0],
            "items": entries,
            "_source": "youtube_api",
        }

    async def fetch_channel(self, channel_id: str) -> dict:
        """Fetch one channel's raw payload."""
        if await self.api_mode() == "music":
            ytm = await self._ytmusic_client()
            try:
                result = await asyncio.to_thread(ytm.get_channel, channel_id)
            except Exception as exc:
                raise ExternalItemNotFound(f"YouTube channel {channel_id!r} not found") from exc
            if not isinstance(result, dict):
                raise ExternalItemNotFound(f"YouTube channel {channel_id!r} not found")
            return {**result, "_source": "ytmusic"}
        data = self._data_client()
        params = {"part": "snippet,statistics"}
        if channel_id.startswith("@"):
            params["forHandle"] = channel_id
        else:
            params["id"] = channel_id
        result = await data.get_json("channels", params, operation="fetch_channel")
        items = result.get("items") or []
        if not items:
            raise ExternalItemNotFound(f"YouTube channel {channel_id!r} not found")
        return {**items[0], "_source": "youtube_api"}

    async def fetch_album(self, browse_id: str) -> dict:
        """Fetch one YouTube Music album's raw payload (music mode only)."""
        ytm = await self._ytmusic_client()
        try:
            result = await asyncio.to_thread(ytm.get_album, browse_id)
        except Exception as exc:
            raise ExternalItemNotFound(f"YouTube album {browse_id!r} not found") from exc
        if not isinstance(result, dict):
            raise ExternalItemNotFound(f"YouTube album {browse_id!r} not found")
        return {**result, "_source": "ytmusic"}

    async def fetch_album_contents(self, browse_id: str) -> dict:
        """Album contents payload: ``{"tracks": [...]}`` in ytmusic shape."""
        album = await self.fetch_album(browse_id)
        tracks = album.get("tracks") or []
        return {
            "_source": "ytmusic",
            "tracks": [{**t, "_source": "ytmusic"} for t in tracks if isinstance(t, dict) and t.get("videoId")],
        }

    # -- search ------------------------------------------------------------

    async def search(self, query: str, limit: int = 20) -> list[dict]:
        """
        Free-text catalog search via ytmusicapi (works for both modes).

        Returns dicts with a ``_source`` marker — each also carries a
        ``_kind`` hint (track/album/artist/playlist) so the adapter can
        route them to the right mapper.
        """
        ytm = await self._ytmusic_client()
        try:
            results = await asyncio.to_thread(ytm.search, query, limit=limit)
        except Exception as exc:
            raise ExternalLibraryError(f"YouTube search failed: {exc}") from exc
        if not isinstance(results, list):
            return []
        normalized: list[dict] = []
        kind_map = {
            "song": "track",
            "video": "track",
            "album": "album",
            "artist": "artist",
            "playlist": "playlist",
            "community_playlist": "playlist",
        }
        for item in results:
            if not isinstance(item, dict):
                continue
            kind = kind_map.get(str(item.get("resultType") or ""))
            if kind is None:
                continue
            normalized.append({**item, "_source": "ytmusic", "_kind": kind})
        return normalized

    def _data_client(self) -> _DataApiClient:
        if auth_mode(self.config) != "oauth":
            raise ExternalConfigError(
                "The YouTube Data API requires OAuth credentials; browser sessions can only use YouTube Music",
                field="access_token",
            )
        if self._data is None:
            self._data = _DataApiClient(self.config, self.redis, self._throttle)
        return self._data
