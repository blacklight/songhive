"""
YouTube external-library adapter.

Imports a Google account's liked/saved videos, playlists, channel
subscriptions and (for YouTube Music Premium users) saved albums as
first-class Songhive entities. There are no local bytes: metadata comes
from YouTube Music internals (``ytmusicapi``) for Premium accounts and the
YouTube Data API v3 otherwise; playback resolves a signed googlevideo URL
per play through ``yt-dlp`` — audio-only by default, video+audio when the
requester opts in (``variant="video"``).

Authentication is one of:

- Google OAuth device authorization (``external/device_auth.py``), driven
  by ``ytmusicapi.auth.oauth.OAuthCredentials`` — requires an OAuth client
  configured on the instance (``external_libraries.youtube``).
- A pasted browser session (``request_headers``/``cookies`` config fields)
  whose cookies are persisted on the encrypted config and handed to yt-dlp
  for every extraction, covering member/age-restricted content.
"""

import asyncio
import logging
from typing import Any, AsyncIterator, Optional

from ..base import ExternalLibraryAdapter
from ..errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from ..types import (
    ExternalAlbumMetadata,
    ExternalArtistMetadata,
    ExternalContents,
    ExternalHealth,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalPlaylistMetadata,
    ExternalStream,
    ExternalTrackMetadata,
)
from .api import YouTubeClient
from .conf import download_format, downloads_allowed, playlist_ttl, video_playback_allowed
from .mapping import (
    browse_url,
    map_album,
    map_artist,
    map_playlist,
    map_playlist_items,
    map_track,
    map_track_ref,
    parse_youtube_url,
)
from .session import auth_mode

logger = logging.getLogger(__name__)


class YouTubeExternalAdapter(ExternalLibraryAdapter):
    """Adapter that indexes entities from a user's YouTube/YouTube Music account."""

    provider_type = "youtube"
    user_configurable = True

    # ------------------------------------------------------------------
    # Session plumbing
    # ------------------------------------------------------------------

    def _redis(self):
        """Best-effort shared Redis client; ``None`` degrades to no caching."""
        try:
            from ...services.redis import get_redis_client

            return get_redis_client(load_config_safe())
        except Exception:
            return None

    def _client(self, config: dict) -> YouTubeClient:
        return YouTubeClient(config, redis=self._redis())

    def _require_bool(self, config: dict, key: str, default: bool = True) -> bool:
        return bool(config.get(key, default))

    # ------------------------------------------------------------------
    # Adapter interface
    # ------------------------------------------------------------------

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        if auth_mode(config) is None:
            raise ExternalConfigError(
                "YouTube credentials are required; connect via device auth or " "paste a captured browser session",
                field="access_token",
            )
        client = self._client(config)
        try:
            await client.api_mode()
        except ExternalLibraryError:
            raise
        except Exception as exc:
            raise ExternalConfigError(
                f"YouTube credentials were rejected: {exc.__class__.__name__}",
                field="access_token",
            ) from exc

        self._capabilities = ExternalLibraryCapabilities(
            list_items=self._require_bool(config, "include_tracks"),
            read_bytes=False,
            stream_url=True,
            range_read=True,
            download=downloads_allowed(),
            compute_hash=False,
            read_tags=False,
            write_tags=False,
            rename_source=False,
            delete_source=False,
            detect_changes=True,
            validate_config=True,
            list_albums=self._require_bool(config, "include_albums"),
            list_artists=self._require_bool(config, "include_subscriptions"),
            list_playlists=self._require_bool(config, "include_playlists"),
            limits={
                "checksum_algorithm": None,
                "entity_import": True,
                "lazy_contents": ["playlist", "album"],
                "editable_fields": ["genres", "tags"],
                "immutable_tracks": True,
                # Remote servers never receive Songhive-hosted YouTube
                # bytes; federated objects carry metadata + a youtube.com
                # link only.
                "federate_audio": False,
                "search": True,
                "playlist_ttl_seconds": playlist_ttl(config),
                "stream_variants": ["audio", "video"] if video_playback_allowed() else ["audio"],
            },
        )
        return self._capabilities

    async def iter_items(self, config: dict, *_, **__) -> AsyncIterator[ExternalItemRef]:
        """Enumerate the account's saved (liked) videos."""
        if not self._require_bool(config, "include_tracks"):
            return
        client = self._client(config)
        async for payload in client.iter_liked_videos():
            ref = map_track_ref(payload)
            if ref is not None:
                yield ref

    async def iter_playlists(self, config: dict, *_, **__) -> AsyncIterator[ExternalPlaylistMetadata]:
        """Enumerate the account's YouTube playlists; contents stay lazy."""
        if not self._require_bool(config, "include_playlists"):
            return
        client = self._client(config)
        async for payload in client.iter_playlists():
            playlist = map_playlist(payload)
            if playlist is not None:
                yield playlist

    async def iter_artists(self, config: dict, *_, **__) -> AsyncIterator[ExternalArtistMetadata]:
        """Enumerate channel subscriptions as provider artists."""
        if not self._require_bool(config, "include_subscriptions"):
            return
        client = self._client(config)
        async for payload in client.iter_subscriptions():
            artist = map_artist(payload)
            if artist is not None:
                yield artist

    async def iter_albums(self, config: dict, *_, **__) -> AsyncIterator[ExternalAlbumMetadata]:
        """Enumerate saved YouTube Music albums (music mode only)."""
        if not self._require_bool(config, "include_albums"):
            return
        client = self._client(config)
        async for payload in client.iter_albums():
            album = map_album(payload)
            if album is not None:
                yield album

    async def iter_contents(
        self,
        config: dict,
        kind: str,
        provider_key: str,
        *,
        etag: Optional[str] = None,
    ) -> ExternalContents:
        """Return the ordered contents of a playlist or album."""
        from ..types import ContentsNotModified, ExternalContentEntry

        client = self._client(config)
        if kind == "playlist":
            payload = await client.fetch_playlist_contents(provider_key)
        elif kind == "album":
            payload = await client.fetch_album_contents(provider_key)
        else:
            raise UnsupportedExternalOperation(
                f"iter_contents is not supported for kind {kind!r}",
                operation="iter_contents",
            )

        entries = []
        for position, key, raw in map_playlist_items(payload):
            metadata = map_track(raw) if isinstance(raw, dict) else None
            entries.append(ExternalContentEntry(position=position, provider_key=key, metadata=metadata))
        captured_etag = payload.get("etag") if isinstance(payload.get("etag"), str) else None
        if etag is not None and captured_etag is not None and captured_etag == etag:
            raise ContentsNotModified()
        return ExternalContents(entries=tuple(entries), etag=captured_etag)

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Resolve the video's googlemedia URL — audio-only unless variant=video."""
        from .stream import open_stream as _open_stream

        variant = getattr(item, "variant", "audio")
        return await _open_stream(
            config,
            item,
            variant="video" if variant == "video" else "audio",
            range=range,
            redis=self._redis(),
        )

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Download the video via yt-dlp into a temp file (instance-gated)."""
        if not downloads_allowed():
            raise ExternalPermissionDenied(
                "YouTube downloads are disabled on this instance",
                operation="download",
            )
        from .ytdlp import download_media, temp_download_dir

        dest_dir = temp_download_dir()
        path = await asyncio.to_thread(download_media, config, item.provider_key, dest_dir=dest_dir)
        mime = "video/mp4" if download_format(config) == "video" else "audio/mp4"
        return ExternalStream(
            kind="path",
            path=path,
            content_type=mime,
            size=path.stat().st_size,
            temporary=True,
            safe_to_redirect=False,
        )

    def external_url(self, kind: str, provider_key: str) -> Optional[str]:
        """Public youtube.com browse URL for an entity."""
        return browse_url(kind, provider_key)

    async def search(
        self,
        config: dict,
        query: str,
        *,
        limit: int = 20,
    ) -> list[dict]:
        """
        Provider search — YouTube URLs resolve by id, other text searches.

        ``youtube.com``/``youtu.be`` URLs are looked up directly (a video id
        needs no text search); other queries go through ytmusicapi's catalog
        search which works for both account modes.
        """
        results: list[dict] = []
        parsed = parse_youtube_url(query)
        if parsed is not None:
            kind, provider_key = parsed
            if kind == "track":
                results.extend(await self._search_track_url(config, provider_key))
            else:
                payload = await self.fetch_entity_payload(config, kind, provider_key)
                entity = self.entity_from_payload(config, kind, payload) if payload else None
                if entity is not None:
                    results.append(self._search_result_for(kind, provider_key, entity, payload or {}))
            return results
        if query.strip().lower().startswith(("http://", "https://")):
            # A foreign URL is a lookup, not a text query — fuzzy-searching
            # the catalog for a URL string only produces noise.
            return results

        client = self._client(config)
        for item in await client.search(query, limit=limit):
            kind = item.pop("_kind", None)
            if kind == "track":
                video_id = item.get("videoId")
                if video_id:
                    results.append(
                        {
                            "kind": "track",
                            "provider_key": str(video_id),
                            "title": str(item.get("title") or video_id),
                            "subtitle": _ytmusic_artists(item),
                            "image_url": _ytmusic_thumbnail(item),
                            "external_url": browse_url("track", str(video_id)),
                        }
                    )
            elif kind == "album":
                browse_id = item.get("browseId")
                if browse_id:
                    results.append(
                        {
                            "kind": "album",
                            "provider_key": str(browse_id),
                            "title": str(item.get("title") or browse_id),
                            "subtitle": _ytmusic_artists(item),
                            "image_url": _ytmusic_thumbnail(item),
                            "external_url": browse_url("album", str(browse_id)),
                        }
                    )
            elif kind == "artist":
                browse_id = item.get("browseId")
                if browse_id:
                    results.append(
                        {
                            "kind": "artist",
                            "provider_key": str(browse_id),
                            "title": str(item.get("artist") or item.get("title") or browse_id),
                            "subtitle": item.get("subscribers"),
                            "image_url": _ytmusic_thumbnail(item),
                            "external_url": browse_url("artist", str(browse_id)),
                        }
                    )
            elif kind == "playlist":
                browse_id = item.get("browseId") or item.get("playlistId")
                if browse_id:
                    results.append(
                        {
                            "kind": "playlist",
                            "provider_key": str(browse_id),
                            "title": str(item.get("title") or browse_id),
                            "subtitle": item.get("author"),
                            "image_url": _ytmusic_thumbnail(item),
                            "external_url": browse_url("playlist", str(browse_id)),
                        }
                    )
            if len(results) >= limit:
                break
        return results[:limit]

    async def _search_track_url(self, config: dict, video_id: str) -> list[dict]:
        """Resolve a video URL/id — API fetch first, yt-dlp fallback."""
        payload: Optional[dict] = None
        try:
            payload = await self.fetch_entity_payload(config, "track", video_id)
        except Exception:
            payload = None
        if payload is None:
            try:
                from .ytdlp import extract_video_metadata

                payload = await asyncio.to_thread(extract_video_metadata, config, video_id)
            except Exception:
                return []
        entity = map_track(payload)
        if entity is None:
            return []
        return [self._search_result_for("track", video_id, entity, payload)]

    def _search_result_for(self, kind: str, provider_key: str, entity: Any, *_, **__) -> dict:
        """Normalize a mapped entity into the transient search-result shape."""
        if kind == "track":
            return {
                "kind": "track",
                "provider_key": provider_key,
                "title": entity.title,
                "subtitle": entity.artist or None,
                "image_url": entity.cover_url,
                "external_url": browse_url("track", provider_key),
            }
        if kind == "playlist":
            return {
                "kind": "playlist",
                "provider_key": provider_key,
                "title": entity.title,
                "subtitle": entity.owner_name,
                "image_url": entity.cover_url,
                "external_url": browse_url("playlist", provider_key),
            }
        if kind == "artist":
            return {
                "kind": "artist",
                "provider_key": provider_key,
                "title": entity.name,
                "subtitle": None,
                "image_url": entity.image_url,
                "external_url": browse_url("artist", provider_key),
            }
        return {
            "kind": kind,
            "provider_key": provider_key,
            "title": getattr(entity, "title", provider_key),
            "subtitle": None,
            "image_url": getattr(entity, "cover_url", None),
            "external_url": browse_url(kind, provider_key),
        }

    def entity_from_payload(
        self,
        config: dict,
        kind: str,
        payload: dict,
    ) -> Optional[Any]:
        """Re-map a cached YouTube payload without touching the APIs."""
        mapper = {
            "track": map_track_ref,
            "album": map_album,
            "artist": map_artist,
            "playlist": map_playlist,
        }.get(kind)
        if mapper is None or not isinstance(payload, dict):
            return None
        try:
            return mapper(payload)
        except Exception:
            return None

    async def fetch_entity_payload(
        self,
        config: dict,
        kind: str,
        provider_key: str,
    ) -> Optional[dict]:
        """Fetch the raw provider payload for one entity (catalog refresh)."""
        client = self._client(config)
        try:
            if kind == "track":
                return await client.fetch_video(provider_key)
            if kind == "playlist":
                return await client.fetch_playlist_contents(provider_key)
            if kind == "artist":
                return await client.fetch_channel(provider_key)
            if kind == "album":
                return await client.fetch_album(provider_key)
        except ExternalItemNotFound:
            return None
        except ExternalConfigError:
            # e.g. album fetch while in youtube mode — yt-dlp fallback below.
            pass
        except Exception:
            pass
        # yt-dlp fallback keeps URL lookups working even when the configured
        # API surface can't address the entity (e.g. albums in youtube mode).
        try:
            from .ytdlp import (
                extract_channel_metadata,
                extract_playlist_metadata,
                extract_video_metadata,
            )

            if kind == "track":
                return await asyncio.to_thread(extract_video_metadata, config, provider_key)
            if kind == "playlist":
                return await asyncio.to_thread(extract_playlist_metadata, config, provider_key)
            if kind == "artist":
                return await asyncio.to_thread(extract_channel_metadata, config, provider_key)
        except Exception:
            return None
        return None

    async def fetch_entity_metadata(
        self,
        config: dict,
        kind: str,
        provider_key: str,
    ) -> Optional[Any]:
        """Fetch one entity's metadata for the import/materialization path."""
        payload = await self.fetch_entity_payload(config, kind, provider_key)
        if payload is None:
            return None
        return self.entity_from_payload(config, kind, payload)

    async def healthcheck(self, config: dict) -> ExternalHealth:
        if auth_mode(config) is None:
            return ExternalHealth(ok=False, message="No YouTube credentials configured")
        try:
            client = self._client(config)
            mode = await client.api_mode()
            from .session import ytmusic_for_config

            ytm = await ytmusic_for_config(config, self._redis())
            info = await asyncio.to_thread(ytm.get_account_info)
        except ExternalLibraryError as exc:
            return ExternalHealth(ok=False, message=str(exc))
        except Exception as exc:
            return ExternalHealth(ok=False, message=f"YouTube check failed: {exc.__class__.__name__}")
        details: dict[str, Any] = {
            "api_mode": mode,
            "auth_mode": auth_mode(config),
        }
        if isinstance(info, dict):
            details["account_name"] = info.get("accountName")
            details["channel_handle"] = info.get("channelHandle")
        return ExternalHealth(ok=True, message="YouTube session is authenticated", details=details)

    # Unsupported write paths — YouTube metadata is provider-owned.

    async def read_metadata(self, *_, **__) -> ExternalTrackMetadata:
        raise UnsupportedExternalOperation(
            "YouTube metadata is provided inline by iter_items",
            operation="read_metadata",
        )

    async def write_metadata(self, *_, **__):
        raise UnsupportedExternalOperation(
            "YouTube metadata is read-only",
            operation="write_metadata",
        )


def _ytmusic_artists(item: dict) -> Optional[str]:
    """Join the artist names on a ytmusicapi result dict."""
    names = [str(a["name"]) for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name")]
    if not names and item.get("author"):
        names.append(str(item["author"]))
    if not names and item.get("artist"):
        names.append(str(item["artist"]))
    return ", ".join(names) or None


def _ytmusic_thumbnail(item: dict) -> Optional[str]:
    from .mapping import pick_thumbnail

    return pick_thumbnail(item.get("thumbnails"))


def load_config_safe():
    """Load the process config, tolerating test contexts without one."""
    from ...config.loader import load_config

    return load_config([])


def register() -> None:
    """Register the YouTube adapter + device-auth provider."""
    from ..device_auth import register_device_auth_provider
    from ..registry import register_external_adapter

    register_external_adapter(YouTubeExternalAdapter.provider_type, YouTubeExternalAdapter)
    from .auth import YouTubeDeviceAuthProvider

    register_device_auth_provider(YouTubeDeviceAuthProvider())
