"""
Jellyfin external-library adapter.

Imports the tracks, albums, artists, and playlists of a remote Jellyfin
server into Songhive as first-class local entities — the first instance of
the "entity-backed provider" pattern. Unlike file-oriented providers there
are no local bytes: metadata comes from the Jellyfin API (never embedded
tags) and playback proxies ``GET /Audio/{id}/stream`` re-resolved at play
time so credentials never reach client-facing URLs.

Authentication prefers a Jellyfin API key (``api_key``, sent as the
``X-Emby-Token`` header). When absent, ``username`` + ``password`` are
exchanged for a session token via ``POST /Users/AuthenticateByName``; the
token is cached on the adapter instance for the lifetime of a sync and is
never persisted outside the encrypted ``ExternalLibrary.config``.

``server_url`` is SSRF-guarded: non-http(s) schemes and private, loopback,
link-local, or reserved targets are rejected, and every redirect hop is
re-validated before credentials are forwarded.
"""

import asyncio
import hashlib
import ipaddress
import logging
import mimetypes
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx

from .base import ExternalLibraryAdapter
from .errors import (
    ExternalConfigError,
    ExternalItemNotFound,
    ExternalLibraryError,
    ExternalPermissionDenied,
    UnsupportedExternalOperation,
)
from .types import (
    ExternalAlbumMetadata,
    ExternalArtistMetadata,
    ExternalHealth,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalPlaylistEntry,
    ExternalPlaylistMetadata,
    ExternalStream,
    ExternalTrackMetadata,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30
_PAGE_SIZE = 500
# Metadata/listing responses are bounded; audio streaming itself is proxied
# without a body cap.
_MAX_METADATA_BODY_BYTES = 10 * 1024 * 1024

_ITEM_FIELDS = (
    "Etag,DateModified,Path,Genres,MediaSources,ProviderIds,"
    "AlbumArtist,Artists,ArtistItems,AlbumArtists,Overview,ParentIndexNumber,"
    "IndexNumber,ProductionYear,RunTimeTicks"
)


def _is_public_address(host: str, resolved: list[str]) -> None:
    """Raise ``ExternalConfigError`` when any resolved address is non-public."""
    for raw in resolved:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ExternalConfigError(
                f"Could not parse resolved address for {host!r}",
                field="server_url",
            ) from exc
        mapped = getattr(address, "ipv6_mapped", None)
        if mapped is not None:
            address = mapped
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ExternalConfigError(
                f"server_url resolves to a non-public address ({raw})",
                field="server_url",
            )


class JellyfinExternalAdapter(ExternalLibraryAdapter):
    """Adapter that indexes music entities from a remote Jellyfin server."""

    provider_type = "jellyfin"
    user_configurable = True

    def __init__(self) -> None:
        super().__init__()
        # (fingerprint, token, fetched_at) — session tokens carry no stated
        # expiry; refresh when the server rejects them instead.
        self._token_cache: Optional[tuple[str, str, float]] = None

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _timeout(config: dict) -> httpx.Timeout:
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
    def _verify(config: dict) -> Any:
        raw = config.get("verify_ssl", True)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw:
            return raw
        return True

    async def _server_url(self, config: dict) -> str:
        """Return the SSRF-validated base URL for the Jellyfin server."""
        raw = config.get("server_url")
        if not isinstance(raw, str) or not raw.strip():
            raise ExternalConfigError(
                'config["server_url"] is required and must be a non-empty string',
                field="server_url",
            )
        url = raw.strip().rstrip("/")
        parsed = httpx.URL(url)
        if parsed.scheme not in ("http", "https") or not parsed.host:
            raise ExternalConfigError(
                'config["server_url"] must be a valid http(s) URL',
                field="server_url",
            )
        host = parsed.host
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            _is_public_address(host, [str(literal)])
        else:
            loop = asyncio.get_running_loop()
            try:
                infos = await loop.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
            except socket.gaierror as exc:
                raise ExternalConfigError(
                    f"Could not resolve server_url host {host!r}",
                    field="server_url",
                ) from exc
            resolved = sorted({info[4][0] for info in infos})
            _is_public_address(host, resolved)
        return url

    def _auth_fingerprint(self, config: dict) -> str:
        payload = repr(
            (
                config.get("server_url"),
                config.get("api_key"),
                config.get("username"),
                config.get("password"),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _resolve_token(self, client: httpx.AsyncClient, config: dict) -> str:
        """Return the API key, or exchange username/password for a session token."""
        api_key = config.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            return api_key.strip()

        username = config.get("username")
        password = config.get("password")
        if not (isinstance(username, str) and username.strip()) or not (isinstance(password, str) and password):
            raise ExternalConfigError(
                'config requires "api_key" or "username" + "password"',
                field="api_key",
            )

        fingerprint = self._auth_fingerprint(config)
        cached = self._token_cache
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        base = await self._server_url(config)
        response = await client.post(
            f"{base}/Users/AuthenticateByName",
            json={"Username": username.strip(), "Pw": password},
            headers={
                "X-Emby-Authorization": (
                    'MediaBrowser Client="Songhive", Device="Songhive", ' 'DeviceId="songhive-external", Version="1.0"'
                )
            },
        )
        if response.is_error:
            raise ExternalPermissionDenied(
                f"Jellyfin authentication failed ({response.status_code})",
                operation="authenticate",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExternalPermissionDenied(
                "Jellyfin authentication returned an invalid response",
                operation="authenticate",
            ) from exc
        token = payload.get("AccessToken")
        if not isinstance(token, str) or not token:
            raise ExternalPermissionDenied(
                "Jellyfin authentication response did not include an AccessToken",
                operation="authenticate",
            )
        self._token_cache = (fingerprint, token, time.time())
        return token

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _client(self, config: dict) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an httpx client that re-validates redirect hops against SSRF."""
        base = await self._server_url(config)

        async def _guard_redirect(request: httpx.Request) -> None:
            # httpx builds each redirect request via the event hook chain; we
            # re-resolve and check the target host before the request leaves.
            host = request.url.host
            if not host:
                raise ExternalConfigError(
                    "Redirect target has no host",
                    field="server_url",
                )
            try:
                literal = ipaddress.ip_address(host)
            except ValueError:
                literal = None
            if literal is not None:
                _is_public_address(host, [str(literal)])
            else:
                infos = await asyncio.get_running_loop().getaddrinfo(host, request.url.port)
                _is_public_address(host, sorted({info[4][0] for info in infos}))

        async with httpx.AsyncClient(
            verify=self._verify(config),
            timeout=self._timeout(config),
            follow_redirects=True,
            max_redirects=5,
            event_hooks={"request": [_guard_redirect]},
            base_url=base,
        ) as client:
            yield client

    @asynccontextmanager
    async def _authed_client(self, config: dict) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
        """Yield ``(client, token)`` with the resolved credential."""
        async with self._client(config) as client:
            token = await self._resolve_token(client, config)
            yield client, token

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        token: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        """GET ``path`` with auth and return the JSON body, size-capped."""
        response = await client.get(
            path,
            params=params or {},
            headers={"X-Emby-Token": token},
        )
        if response.is_error:
            raise ExternalItemNotFound(
                f"Jellyfin request {path} failed ({response.status_code})",
            )
        if len(response.content) > _MAX_METADATA_BODY_BYTES:
            raise ExternalLibraryError(f"Jellyfin response for {path} exceeded {_MAX_METADATA_BODY_BYTES} bytes")
        try:
            return response.json()
        except ValueError as exc:
            raise ExternalLibraryError(f"Jellyfin response for {path} was not valid JSON") from exc

    # ------------------------------------------------------------------
    # Item mapping helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _container(item: dict) -> str:
        container = item.get("Container")
        if isinstance(container, str) and container:
            return container.lstrip(".").lower()
        sources = item.get("MediaSources")
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict) and isinstance(source.get("Container"), str):
                    return source["Container"].lstrip(".").lower()
        path = item.get("Path")
        if isinstance(path, str) and "." in path:
            return path.rsplit(".", 1)[-1].lower()
        return "bin"

    def _mime_for_item(self, item: dict) -> str:
        container = self._container(item)
        return mimetypes.types_map.get(f".{container}", f"audio/{container}")

    def _display_path(self, item: dict) -> str:
        """Derive a read-only pseudo-filename from track metadata."""
        container = self._container(item)
        title = str(item.get("Name") or item.get("Id") or "track")
        album = item.get("Album")
        album_artists = item.get("AlbumArtists") or []
        album_artist = None
        if isinstance(album_artists, list) and album_artists:
            first = album_artists[0]
            if isinstance(first, dict):
                album_artist = first.get("Name")
            elif isinstance(first, str):
                album_artist = first
        if not album_artist:
            album_artist = item.get("AlbumArtist")

        track_no = item.get("IndexNumber")
        disc_no = item.get("ParentIndexNumber")

        def _num(value: Any) -> Optional[int]:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        track_no = _num(track_no)
        disc_no = _num(disc_no)

        leaf = title
        if track_no is not None:
            if disc_no is not None and disc_no > 1:
                leaf = f"{disc_no:02d}-{track_no:02d} {title}"
            else:
                leaf = f"{track_no:02d} {title}"
        leaf = f"{leaf}.{container}"

        def _clean(segment: Any) -> str:
            text = str(segment).replace("/", "_").replace("\\", "_").strip()
            return text or "Unknown"

        if album and album_artist:
            return f"{_clean(album_artist)}/{_clean(album)}/{leaf}"
        if album:
            return f"{_clean(album)}/{leaf}"
        return leaf

    def _track_metadata(self, item: dict, server_url: str) -> ExternalTrackMetadata:
        """Map a Jellyfin Audio item onto ``ExternalTrackMetadata``."""
        artists = item.get("Artists")
        artist_names = tuple(str(name) for name in artists) if isinstance(artists, list) else ()
        album_artists_raw = item.get("AlbumArtists")
        album_artist_names: tuple[str, ...] = ()
        if isinstance(album_artists_raw, list):
            album_artist_names = tuple(
                str(entry.get("Name")) for entry in album_artists_raw if isinstance(entry, dict) and entry.get("Name")
            )
        if not album_artist_names and item.get("AlbumArtist"):
            album_artist_names = (str(item["AlbumArtist"]),)

        genres = item.get("Genres")
        genre_names = tuple(str(name) for name in genres) if isinstance(genres, list) else ()

        provider_ids = item.get("ProviderIds")
        provider_ids = {str(k): str(v) for k, v in provider_ids.items()} if isinstance(provider_ids, dict) else {}

        cover_url = None
        image_tags = item.get("ImageTags")
        if isinstance(image_tags, dict) and image_tags.get("Primary"):
            cover_url = f"{server_url}/Items/{item.get('Id')}/Images/Primary"
        elif item.get("AlbumId") and item.get("AlbumPrimaryImageTag"):
            cover_url = f"{server_url}/Items/{item['AlbumId']}/Images/Primary"

        run_time_ticks = item.get("RunTimeTicks")
        duration = run_time_ticks / 10_000_000 if isinstance(run_time_ticks, (int, float)) else None

        def _num(value: Any) -> Optional[int]:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        primary_artist = artist_names[0] if artist_names else (album_artist_names[0] if album_artist_names else "")

        return ExternalTrackMetadata(
            title=str(item.get("Name") or "Unknown"),
            artist=primary_artist,
            album=str(item.get("Album") or ""),
            album_artist=album_artist_names[0] if album_artist_names else "",
            track_number=_num(item.get("IndexNumber")),
            disc_number=_num(item.get("ParentIndexNumber")),
            duration=duration,
            release_year=_num(item.get("ProductionYear")),
            genre=genre_names[0] if genre_names else None,
            musicbrainz_id=provider_ids.get("MusicBrainzTrack"),
            artists=artist_names,
            album_artists=album_artist_names,
            genres=genre_names,
            cover_url=cover_url,
            description=item.get("Overview") if isinstance(item.get("Overview"), str) else None,
            provider_ids=provider_ids,
            raw_metadata=dict(item),
        )

    async def _collection_ids(self, client: httpx.AsyncClient, token: str, config: dict) -> list[str]:
        """Resolve configured ``collections`` names/ids to Jellyfin item ids.

        An empty ``collections`` list means "all music libraries" and is
        signalled by returning ``[]`` (no ``ParentId`` filter).
        """
        wanted = config.get("collections") or []
        if not isinstance(wanted, list):
            raise ExternalConfigError(
                'config["collections"] must be a list of library names or ids',
                field="collections",
            )
        wanted = [str(entry).strip() for entry in wanted if str(entry).strip()]
        if not wanted:
            return []

        folders = await self._get_json(client, token, "/Library/MediaFolders")
        items = folders.get("Items") if isinstance(folders, dict) else None
        by_name: dict[str, str] = {}
        if isinstance(items, list):
            for folder in items:
                if isinstance(folder, dict) and folder.get("Id") and folder.get("Name"):
                    by_name[str(folder["Name"])] = str(folder["Id"])

        resolved: list[str] = []
        for entry in wanted:
            resolved.append(by_name.get(entry, entry))
        return resolved

    async def _iter_entity_items(
        self,
        client: httpx.AsyncClient,
        token: str,
        config: dict,
        include_types: str,
        since: Optional[datetime],
        extra_params: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[dict]:
        """Page through ``/Items`` for the given ``IncludeItemTypes``."""
        collection_ids = await self._collection_ids(client, token, config)
        parent_ids: list[Optional[str]] = list(collection_ids) or [None]

        for parent_id in parent_ids:
            start = 0
            while True:
                params: dict[str, Any] = {
                    "IncludeItemTypes": include_types,
                    "Recursive": "true",
                    "Fields": _ITEM_FIELDS,
                    "StartIndex": start,
                    "Limit": _PAGE_SIZE,
                }
                if parent_id is not None:
                    params["ParentId"] = parent_id
                if since is not None:
                    params["MinDateLastSaved"] = since.strftime("%Y%m%d%H%M%S")
                if extra_params:
                    params.update(extra_params)
                page = await self._get_json(client, token, "/Items", params=params)
                items = page.get("Items") if isinstance(page, dict) else None
                if not isinstance(items, list) or not items:
                    break
                for item in items:
                    if isinstance(item, dict):
                        yield item
                if len(items) < _PAGE_SIZE:
                    break
                start += len(items)

    # ------------------------------------------------------------------
    # Adapter interface
    # ------------------------------------------------------------------

    async def validate_config(self, config: dict) -> ExternalLibraryCapabilities:
        base = await self._server_url(config)
        async with self._client(config) as client:
            response = await client.get(f"{base}/System/Info/Public")
            if response.is_error:
                raise ExternalConfigError(
                    f"Jellyfin server at {base} is not reachable ({response.status_code})",
                    field="server_url",
                )
            token = await self._resolve_token(client, config)
            auth_check = await client.get(
                f"{base}/System/Info",
                headers={"X-Emby-Token": token},
            )
            if auth_check.status_code in (401, 403):
                self._token_cache = None
                raise ExternalConfigError(
                    "Jellyfin credentials were rejected",
                    field="api_key" if config.get("api_key") else "password",
                )

        self._capabilities = ExternalLibraryCapabilities(
            list_items=bool(config.get("include_tracks", True)),
            read_bytes=False,
            stream_url=True,
            range_read=True,
            download=True,
            compute_hash=False,
            read_tags=False,
            write_tags=False,
            rename_source=False,
            delete_source=False,
            detect_changes=True,
            validate_config=True,
            list_albums=bool(config.get("include_albums", True)),
            list_artists=bool(config.get("include_artists", True)),
            list_playlists=bool(config.get("include_playlists", True)),
            limits={"checksum_algorithm": None, "entity_import": True},
        )
        return self._capabilities

    async def iter_items(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalItemRef]:
        """Enumerate Jellyfin Audio items as track references with inline metadata."""
        async with self._authed_client(config) as (client, token):
            base = await self._server_url(config)
            async for item in self._iter_entity_items(client, token, config, "Audio", since):
                item_id = item.get("Id")
                if not item_id:
                    continue
                size = item.get("Size")
                if size is None:
                    sources = item.get("MediaSources")
                    if isinstance(sources, list):
                        for source in sources:
                            if isinstance(source, dict) and source.get("Size"):
                                size = source["Size"]
                                break
                yield ExternalItemRef(
                    provider_key=str(item_id),
                    display_path=self._display_path(item),
                    etag=item.get("Etag") if isinstance(item.get("Etag"), str) else None,
                    mtime=self._parse_datetime(item.get("DateModified")),
                    size=int(size) if size is not None else None,
                    mime_type=self._mime_for_item(item),
                    metadata=self._track_metadata(item, base),
                )

    async def iter_albums(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalAlbumMetadata]:
        async with self._authed_client(config) as (client, token):
            base = await self._server_url(config)
            async for item in self._iter_entity_items(client, token, config, "MusicAlbum", since):
                item_id = item.get("Id")
                if not item_id:
                    continue

                artist_items = item.get("ArtistItems") or item.get("AlbumArtists") or []
                artist_names: list[str] = []
                artist_keys: list[str] = []
                if isinstance(artist_items, list):
                    for entry in artist_items:
                        if isinstance(entry, dict):
                            if entry.get("Name"):
                                artist_names.append(str(entry["Name"]))
                            if entry.get("Id"):
                                artist_keys.append(str(entry["Id"]))
                if not artist_names and item.get("AlbumArtist"):
                    artist_names.append(str(item["AlbumArtist"]))

                genres = item.get("Genres")
                provider_ids = item.get("ProviderIds")
                image_tags = item.get("ImageTags")
                cover_url = None
                if isinstance(image_tags, dict) and image_tags.get("Primary"):
                    cover_url = f"{base}/Items/{item_id}/Images/Primary"

                year = item.get("ProductionYear")
                yield ExternalAlbumMetadata(
                    provider_key=str(item_id),
                    title=str(item.get("Name") or "Unknown"),
                    artist_names=tuple(artist_names),
                    artist_provider_keys=tuple(artist_keys),
                    release_year=int(year) if isinstance(year, (int, float)) else None,
                    genres=tuple(str(g) for g in genres) if isinstance(genres, list) else (),
                    cover_url=cover_url,
                    description=item.get("Overview") if isinstance(item.get("Overview"), str) else None,
                    provider_ids=(
                        {str(k): str(v) for k, v in provider_ids.items()} if isinstance(provider_ids, dict) else {}
                    ),
                    etag=item.get("Etag") if isinstance(item.get("Etag"), str) else None,
                    mtime=self._parse_datetime(item.get("DateModified")),
                    raw_metadata=dict(item),
                )

    async def iter_artists(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalArtistMetadata]:
        async with self._authed_client(config) as (client, token):
            base = await self._server_url(config)
            async for item in self._iter_entity_items(client, token, config, "MusicArtist", since):
                item_id = item.get("Id")
                if not item_id:
                    continue
                provider_ids = item.get("ProviderIds")
                image_tags = item.get("ImageTags")
                image_url = None
                if isinstance(image_tags, dict) and image_tags.get("Primary"):
                    image_url = f"{base}/Items/{item_id}/Images/Primary"
                yield ExternalArtistMetadata(
                    provider_key=str(item_id),
                    name=str(item.get("Name") or "Unknown"),
                    image_url=image_url,
                    bio=item.get("Overview") if isinstance(item.get("Overview"), str) else None,
                    provider_ids=(
                        {str(k): str(v) for k, v in provider_ids.items()} if isinstance(provider_ids, dict) else {}
                    ),
                    etag=item.get("Etag") if isinstance(item.get("Etag"), str) else None,
                    mtime=self._parse_datetime(item.get("DateModified")),
                    raw_metadata=dict(item),
                )

    async def iter_playlists(
        self,
        config: dict,
        since: Optional[datetime] = None,
        scope: Optional[str] = None,
    ) -> AsyncIterator[ExternalPlaylistMetadata]:
        async with self._authed_client(config) as (client, token):
            base = await self._server_url(config)
            async for item in self._iter_entity_items(client, token, config, "Playlist", since):
                item_id = item.get("Id")
                if not item_id:
                    continue

                entries: list[ExternalPlaylistEntry] = []
                start = 0
                while True:
                    page = await self._get_json(
                        client,
                        token,
                        f"/Playlists/{item_id}/Items",
                        params={"StartIndex": start, "Limit": _PAGE_SIZE, "Fields": "Etag"},
                    )
                    items = page.get("Items") if isinstance(page, dict) else None
                    if not isinstance(items, list) or not items:
                        break
                    for offset, entry in enumerate(items):
                        if isinstance(entry, dict) and entry.get("Id"):
                            entries.append(
                                ExternalPlaylistEntry(
                                    position=start + offset,
                                    track_provider_key=str(entry["Id"]),
                                )
                            )
                    if len(items) < _PAGE_SIZE:
                        break
                    start += len(items)

                image_tags = item.get("ImageTags")
                cover_url = None
                if isinstance(image_tags, dict) and image_tags.get("Primary"):
                    cover_url = f"{base}/Items/{item_id}/Images/Primary"

                owner_name = None
                # Jellyfin playlists don't always carry the owner on the item;
                # keep the field for providers that do.
                if isinstance(item.get("OwnerName"), str):
                    owner_name = item["OwnerName"]

                yield ExternalPlaylistMetadata(
                    provider_key=str(item_id),
                    title=str(item.get("Name") or "Untitled playlist"),
                    description=item.get("Overview") if isinstance(item.get("Overview"), str) else None,
                    cover_url=cover_url,
                    owner_name=owner_name,
                    entries=tuple(entries),
                    etag=item.get("Etag") if isinstance(item.get("Etag"), str) else None,
                    mtime=self._parse_datetime(item.get("DateModified")),
                    raw_metadata=dict(item),
                )

    async def open_stream(
        self,
        config: dict,
        item: ExternalItemRef,
        *,
        range: Optional[tuple[int, int]] = None,
    ) -> ExternalStream:
        """Return a proxied URL stream for ``/Audio/{id}/stream``."""
        base = await self._server_url(config)
        async with self._client(config) as client:
            token = await self._resolve_token(client, config)

        headers = {"X-Emby-Token": token}
        content_range: Optional[str] = None
        if range is not None:
            start, end = range
            headers["Range"] = f"bytes={start}-{end}"
            if item.size:
                content_range = f"bytes {start}-{end}/{item.size}"

        return ExternalStream(
            kind="url",
            url=f"{base}/Audio/{item.provider_key}/stream",
            headers=headers,
            content_type=item.mime_type,
            size=item.size,
            supports_range=True,
            safe_to_redirect=False,
            content_range=content_range,
        )

    async def download(self, config: dict, item: ExternalItemRef) -> ExternalStream:
        """Return a proxied full download stream (same endpoint as streaming)."""
        return await self.open_stream(config, item)

    async def read_metadata(self, config: dict, item: ExternalItemRef) -> ExternalTrackMetadata:
        raise UnsupportedExternalOperation(
            "Jellyfin metadata is provided inline by iter_items",
            operation="read_metadata",
        )

    async def compute_sha256(self, config: dict, item: ExternalItemRef) -> str:
        raise UnsupportedExternalOperation(
            "Jellyfin items have no local bytes to hash",
            operation="compute_sha256",
        )

    async def write_metadata(self, config: dict, item: ExternalItemRef, metadata: ExternalTrackMetadata):
        raise UnsupportedExternalOperation(
            "Jellyfin metadata is read-only",
            operation="write_metadata",
        )

    async def rename_source(self, config: dict, item: ExternalItemRef, new_name: str) -> ExternalItemRef:
        raise UnsupportedExternalOperation(
            "Jellyfin pseudo-filenames are read-only",
            operation="rename_source",
        )

    async def delete_source(self, config: dict, item: ExternalItemRef):
        raise UnsupportedExternalOperation(
            "Jellyfin items cannot be deleted through Songhive",
            operation="delete_source",
        )

    async def healthcheck(self, config: dict) -> ExternalHealth:
        try:
            base = await self._server_url(config)
        except ExternalConfigError as exc:
            return ExternalHealth(ok=False, message=str(exc))

        async with self._client(config) as client:
            try:
                response = await client.get(f"{base}/System/Info/Public")
            except httpx.HTTPError as exc:
                return ExternalHealth(ok=False, message=f"Jellyfin unreachable: {exc}")
            if response.is_error:
                return ExternalHealth(
                    ok=False,
                    message=f"Jellyfin /System/Info/Public returned {response.status_code}",
                )
            try:
                token = await self._resolve_token(client, config)
            except ExternalLibraryError as exc:
                return ExternalHealth(ok=False, message=str(exc))
            auth_check = await client.get(
                f"{base}/System/Info",
                headers={"X-Emby-Token": token},
            )
            if auth_check.status_code in (401, 403):
                return ExternalHealth(ok=False, message="Jellyfin credentials were rejected")
        return ExternalHealth(ok=True, message="Jellyfin server is reachable and authenticated")
