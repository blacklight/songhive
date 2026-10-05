"""
TIDAL external-library adapter.

Imports a subscriber's saved tracks, albums, artists and playlists as
first-class Songhive entities. There are no local bytes: metadata is
fetched from the TIDAL API, cached aggressively in the instance-wide
provider catalog (tracks are immutable), and playback resolves signed
CDN URLs or DASH manifests at play time.

Authentication is account-linking via OAuth device authorization (the
"link code" flow in ``external/device_auth.py``) or, for Hi-Res, a
paste-the-redirect-URL PKCE flow. Credential fragments produced by those
flows are merged into the library config, which is encrypted at rest like
every other provider.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from ..base import ExternalLibraryAdapter
from ..errors import ExternalConfigError, ExternalLibraryError, ExternalPermissionDenied
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
from .api import TidalApiClient
from .conf import download_format, downloads_allowed, max_rps, playlist_ttl
from .mapping import map_album, map_artist, map_playlist, map_track_ref, parse_tidal_url
from .session import effective_quality, session_for_config

logger = logging.getLogger(__name__)


class TidalExternalAdapter(ExternalLibraryAdapter):
    """Adapter that indexes entities from a subscriber's TIDAL account."""

    provider_type = "tidal"
    user_configurable = True

    # ------------------------------------------------------------------
    # Session plumbing
    # ------------------------------------------------------------------

    async def _session(self, config: dict):
        """Return a session with a valid access token."""
        redis = self._redis()
        return await session_for_config(config, redis)

    def _redis(self):
        """Best-effort shared Redis client; ``None`` degrades to no caching."""
        try:
            from ...services.redis import get_redis_client

            return get_redis_client(load_config_safe())
        except Exception:
            return None

    async def _client(self, config: dict) -> TidalApiClient:
        session = await self._session(config)
        return TidalApiClient(session, max_rps=max_rps(config))

    def _require_bool(self, config: dict, key: str, default: bool = True) -> bool:
        raw = config.get(key, default)
        return bool(raw)

    # ------------------------------------------------------------------
    # Adapter interface
    # ------------------------------------------------------------------

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        if not config.get("access_token") and not config.get("refresh_token"):
            raise ExternalConfigError(
                "TIDAL credentials are required; connect the account via device auth",
                field="access_token",
            )
        session = await self._session(config)
        try:
            await asyncio.to_thread(_check_session, session)
        except ExternalLibraryError:
            raise
        except Exception as exc:
            raise ExternalConfigError(
                f"TIDAL credentials were rejected: {exc.__class__.__name__}",
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
            list_artists=self._require_bool(config, "include_artists"),
            list_playlists=self._require_bool(config, "include_playlists"),
            limits={
                "checksum_algorithm": None,
                "entity_import": True,
                "lazy_contents": ["playlist", "album"],
                "editable_fields": ["genres", "tags"],
                "immutable_tracks": True,
                # Remote servers never receive Songhive-hosted TIDAL bytes;
                # federated objects carry metadata + a tidal.com link only.
                "federate_audio": False,
                "search": True,
                "playlist_ttl_seconds": playlist_ttl(config),
            },
        )
        return self._capabilities

    async def iter_items(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalItemRef]:
        """Enumerate the subscriber's saved (favorite) tracks."""
        if not self._require_bool(config, "include_tracks"):
            return
        client = await self._client(config)
        path = client.favorites_path("tracks")
        async for entry in client.iter_paged(path, item_path="item"):
            ref = map_track_ref(entry)
            if ref is not None:
                yield ref

    async def iter_albums(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalAlbumMetadata]:
        if not self._require_bool(config, "include_albums"):
            return
        client = await self._client(config)
        async for entry in client.iter_paged(client.favorites_path("albums"), item_path="item"):
            album = map_album(entry)
            if album is not None:
                yield album

    async def iter_artists(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalArtistMetadata]:
        if not self._require_bool(config, "include_artists"):
            return
        client = await self._client(config)
        async for entry in client.iter_paged(client.favorites_path("artists"), item_path="item"):
            artist = map_artist(entry)
            if artist is not None:
                yield artist

    async def iter_playlists(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalPlaylistMetadata]:
        """
        Enumerate owned playlists (plus favorited ones when
        ``include_followed_playlists``), metadata only — contents stay lazy.
        """
        if not self._require_bool(config, "include_playlists"):
            return
        client = await self._client(config)
        user_id = client.session.user.id

        async for entry in client.iter_paged(f"users/{user_id}/playlists"):
            playlist = map_playlist(entry)
            if playlist is not None:
                yield playlist

        if self._require_bool(config, "include_followed_playlists", False):
            async for entry in client.iter_paged(client.favorites_path("playlists"), item_path="item"):
                playlist = map_playlist(entry)
                if playlist is not None:
                    yield playlist

    async def fetch_container_contents(
        self,
        config: dict,
        kind: str,
        provider_key: str,
        *,
        etag: Optional[str] = None,
    ) -> dict:
        """
        Fetch the ordered contents of a playlist or album.

        Returns ``{"entries": [(position, provider_key, raw_json), ...],
        "etag": ...}``. Returns ``{"not_modified": True}`` when the provider
        answers the conditional request with HTTP 304.
        """
        client = await self._client(config)
        path = client.playlist_items_path(provider_key) if kind == "playlist" else client.album_items_path(provider_key)
        from .api import NotModified
        from .mapping import map_playlist_items

        # Container items endpoints reject limit>100 with HTTP 400
        # (verified against api.tidal.com v1; favorites endpoints
        # tolerate the larger _PAGE_LIMIT used by iter_paged).
        limit = 100
        offset = 0
        captured_etag: Optional[str] = None
        entries: list[tuple[int, str, Optional[dict]]] = []
        while True:
            try:
                response = await client.get_json(
                    path,
                    params={"limit": limit, "offset": offset},
                    etag=etag if offset == 0 else None,
                )
            except NotModified:
                return {"not_modified": True}
            if offset == 0:
                captured_etag = response.etag
            chunk = map_playlist_items(response.data)
            # Re-sequence positions across pages so they stay dense.
            base = len(entries)
            entries.extend((base + pos, key, raw) for pos, key, raw in chunk)

            payload = response.data if isinstance(response.data, dict) else {}
            items = payload.get("items") or []
            total = payload.get("totalNumberOfItems")
            if isinstance(total, int) and offset + len(items) >= total:
                break
            if len(items) < limit:
                break
            offset += len(items)

        return {"entries": entries, "etag": captured_etag}

    async def iter_contents(
        self,
        config: dict,
        kind: str,
        provider_key: str,
        *,
        etag: Optional[str] = None,
    ) -> ExternalContents:
        """Return the ordered track contents of a playlist or album."""
        from ..types import ContentsNotModified, ExternalContentEntry
        from .mapping import map_track

        result = await self.fetch_container_contents(config, kind, provider_key, etag=etag)
        if result.get("not_modified"):
            raise ContentsNotModified()

        entries = []
        for position, key, raw in result["entries"]:
            metadata = map_track(raw) if isinstance(raw, dict) else None
            entries.append(ExternalContentEntry(position=position, provider_key=key, metadata=metadata))
        return ExternalContents(entries=tuple(entries), etag=result.get("etag"))

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Download the track to a tagged FLAC/M4A file (gated by instance config)."""
        if not downloads_allowed():
            raise ExternalPermissionDenied(
                "TIDAL downloads are disabled on this instance",
                operation="download",
            )
        from .remux import download_track

        return await download_track(config, item, fmt=download_format(config))

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ):
        """Resolve the track's playback manifest into a streamable object."""
        from .stream import open_stream as _open_stream

        return await _open_stream(config, item, range=range, redis=self._redis())

    def external_url(self, kind: str, provider_key: str) -> Optional[str]:
        """Public tidal.com browse URL for an entity."""
        slug = {"track": "track", "album": "album", "artist": "artist", "playlist": "playlist"}.get(kind)
        if slug is None:
            return None
        return f"https://tidal.com/browse/{slug}/{provider_key}"

    async def search(
        self,
        config: dict,
        query: str,
        *,
        limit: int = 20,
    ) -> list[dict]:
        """
        Provider search — tidal.com URLs resolve by id, other text searches.

        ``tidal.com`` URLs (canonical ``/browse/{kind}/{id}`` and share-link
        ``/{kind}/{id}`` shapes) are looked up directly; other queries go
        through the catalog search endpoint.
        """
        from .mapping import image_url

        results: list[dict] = []
        parsed = parse_tidal_url(query)
        if parsed is not None:
            kind, provider_key = parsed
            payload = await self.fetch_entity_payload(config, kind, provider_key)
            entity = self.entity_from_payload(config, kind, payload) if payload else None
            if entity is not None:
                results.append(self._search_result_for(kind, provider_key, entity))
            return results

        if query.strip().lower().startswith(("http://", "https://")):
            # A foreign URL is a lookup, not a text query — fuzzy-searching
            # the catalog for a URL string only produces noise.
            return results

        client = await self._client(config)
        response = await client.get_json(
            "search",
            params={"query": query, "limit": limit, "types": "TRACKS,ALBUMS,ARTISTS,PLAYLISTS"},
        )
        data = response.data if isinstance(response.data, dict) else {}

        def _items(section: str) -> list[dict]:
            block = data.get(section)
            if not isinstance(block, dict):
                return []
            items = block.get("items")
            return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

        def _names(item: dict) -> Optional[str]:
            names = [str(a["name"]) for a in item.get("artists") or [] if isinstance(a, dict) and a.get("name")]
            if not names and isinstance(item.get("artist"), dict) and item["artist"].get("name"):
                names.append(str(item["artist"]["name"]))
            return ", ".join(names) or None

        for item in _items("tracks"):
            if item.get("id") is None:
                continue
            album_raw = item.get("album")
            album: dict = album_raw if isinstance(album_raw, dict) else {}
            results.append(
                {
                    "kind": "track",
                    "provider_key": str(item["id"]),
                    "title": str(item.get("title") or item["id"]),
                    "subtitle": _names(item),
                    "image_url": image_url(album.get("cover"), width=320, height=320),
                    "external_url": self.external_url("track", str(item["id"])),
                }
            )
        for item in _items("albums"):
            if item.get("id") is None:
                continue
            results.append(
                {
                    "kind": "album",
                    "provider_key": str(item["id"]),
                    "title": str(item.get("title") or item["id"]),
                    "subtitle": _names(item),
                    "image_url": image_url(item.get("cover"), width=320, height=320),
                    "external_url": self.external_url("album", str(item["id"])),
                }
            )
        for item in _items("artists"):
            if item.get("id") is None:
                continue
            results.append(
                {
                    "kind": "artist",
                    "provider_key": str(item["id"]),
                    "title": str(item.get("name") or item["id"]),
                    "subtitle": None,
                    "image_url": image_url(item.get("picture"), width=320, height=320),
                    "external_url": self.external_url("artist", str(item["id"])),
                }
            )
        for item in _items("playlists"):
            uuid = item.get("uuid") or item.get("id")
            if uuid is None:
                continue
            creator = item.get("creator")
            owner = creator.get("name") if isinstance(creator, dict) else None
            results.append(
                {
                    "kind": "playlist",
                    "provider_key": str(uuid),
                    "title": str(item.get("title") or uuid),
                    "subtitle": str(owner) if owner else None,
                    "image_url": image_url(item.get("squareImage") or item.get("image"), width=320, height=320),
                    "external_url": self.external_url("playlist", str(uuid)),
                }
            )
        return results

    def _search_result_for(self, kind: str, provider_key: str, entity: Any) -> dict:
        """Normalize a mapped entity into the transient search-result shape."""
        if isinstance(entity, ExternalItemRef):
            entity = entity.metadata
        if kind == "track":
            title = getattr(entity, "title", provider_key)
            subtitle = getattr(entity, "artist", None) or None
            image = getattr(entity, "cover_url", None)
        elif kind == "album":
            title = getattr(entity, "title", provider_key)
            subtitle = ", ".join(getattr(entity, "artist_names", ()) or ()) or None
            image = getattr(entity, "cover_url", None)
        elif kind == "artist":
            title = getattr(entity, "name", provider_key)
            subtitle = None
            image = getattr(entity, "image_url", None)
        else:
            title = getattr(entity, "title", provider_key)
            subtitle = getattr(entity, "owner_name", None)
            image = getattr(entity, "cover_url", None)
        return {
            "kind": kind,
            "provider_key": provider_key,
            "title": str(title),
            "subtitle": subtitle,
            "image_url": image,
            "external_url": self.external_url(kind, provider_key),
        }

    def entity_from_payload(
        self,
        config: dict,
        kind: str,
        payload: dict,
    ) -> Optional[Any]:
        """Re-map a cached TIDAL payload without touching the API."""
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
        """Fetch the raw TIDAL JSON for one entity (catalog refresh)."""
        client = await self._client(config)
        path = {
            "track": f"tracks/{provider_key}",
            "album": f"albums/{provider_key}",
            "artist": f"artists/{provider_key}",
            "playlist": f"playlists/{provider_key}",
        }.get(kind)
        if path is None:
            return None
        try:
            response = await client.get_json(path)
        except Exception:
            return None
        return response.data if isinstance(response.data, dict) else None

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
        try:
            session = await self._session(config)
        except ExternalLibraryError as exc:
            return ExternalHealth(ok=False, message=str(exc))
        try:
            info = await asyncio.to_thread(_session_info, session)
        except Exception as exc:
            return ExternalHealth(ok=False, message=f"TIDAL check failed: {exc.__class__.__name__}")
        if not info:
            return ExternalHealth(ok=False, message="TIDAL credentials were rejected")
        user = getattr(session, "user", None)
        username = getattr(user, "username", None) if user is not None else None
        return ExternalHealth(
            ok=True,
            message="TIDAL session is authenticated",
            details={
                "user_id": info.get("userId"),
                "username": username,
                "country_code": info.get("countryCode"),
                "quality": effective_quality(config),
            },
        )

    # Unsupported write paths — TIDAL metadata is provider-owned.

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        from ..errors import UnsupportedExternalOperation

        raise UnsupportedExternalOperation(
            "TIDAL metadata is provided inline by iter_items",
            operation="read_metadata",
        )

    async def write_metadata(self, config: dict, item: ExternalItemRef, metadata: ExternalTrackMetadata):
        from ..errors import UnsupportedExternalOperation

        raise UnsupportedExternalOperation(
            "TIDAL metadata is read-only",
            operation="write_metadata",
        )


def _check_session(session: Any) -> None:
    """One cheap authenticated request to validate the grant."""
    response = session.request.request("GET", "sessions")
    if response.status_code in (401, 403):
        from ..errors import ExternalPermissionDenied

        raise ExternalPermissionDenied(
            "TIDAL credentials were rejected",
            operation="validate_config",
        )
    if not response.ok:
        raise ExternalLibraryError(f"TIDAL /sessions returned {response.status_code}")


def _session_info(session: Any) -> dict:
    response = session.request.request("GET", "sessions")
    try:
        return response.json() if response.ok else {}
    except ValueError:
        return {}


def load_config_safe():
    """Load the process config, tolerating test contexts without one."""
    from ...config.loader import load_config

    return load_config([])


def register() -> None:
    """Register the TIDAL adapter + device-auth provider."""
    from ..device_auth import register_device_auth_provider
    from ..registry import register_external_adapter

    register_external_adapter(TidalExternalAdapter.provider_type, TidalExternalAdapter)
    from .auth import TidalDeviceAuthProvider

    register_device_auth_provider(TidalDeviceAuthProvider())
