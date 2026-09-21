"""
Subsonic API routes mounted under ``/rest``.

Every endpoint answers with the standard ``subsonic-response`` envelope
(XML by default, JSON/JSONP via ``f``) rendered by ``responses.render``.
Binary endpoints (``stream``, ``download``, ``getCoverArt``, ``getAvatar``)
live in ``media.py``; under the Tornado bootstrap the first two are served
natively by ``handlers.py`` and these FastAPI routes act as the uvicorn
fallback.

Parameter handling follows the Subsonic spec: credentials and arguments
arrive as query parameters for GET requests or ``application/x-www-form-
urlencoded`` bodies for POST. Values may repeat (``id``, ``songId`` ...),
so ``Params`` keeps every occurrence.
"""

import logging
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple, TypeVar, Union, overload

from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...api._common import client_ip
from ...api.deps import get_db, get_storage_service
from ...api.routes.favorites import _notify_favorite, _retract_favorite_notification
from ...config.schema import SonghiveConfig
from ...models._enums import Visibility
from ...models.album import Album
from ...models.artist import Artist
from ...models.audit_log import AuditTargetType
from ...models.favorite import Favorite
from ...models.playlist import Playlist, PlaylistTrack
from ...models.track import Track
from ...models.user import User
from ...services import acl, audit, deletion, music
from ...services.auth import get_user_by_username
from ...services.storage import StorageService
from ...services.streaming import record_listen
from . import now_playing
from . import serializers as sz
from .auth import authenticate_subsonic
from .errors import (
    GENERIC_ERROR,
    NOT_AUTHORIZED,
    NOT_FOUND,
    SERVER_PROTOCOL_TOO_OLD,
    SubsonicError,
    missing_parameter,
)
from .queries import (
    accessible_artist_map,
    album_ids_in_library,
    album_stats_map,
    artist_album_count_map,
    artist_ids_in_library,
    favorite_map,
    genre_counts,
    group_artists_by_index,
    remove_playlist_tracks_at_indexes,
)
from .responses import API_VERSION, IGNORED_ARTICLES, envelope, error_envelope, render

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

router = APIRouter()

#: Music-folder id clients use for the "everything" folder.
ROOT_MUSIC_FOLDER_ID = "0"

#: OpenSubsonic extensions implemented by this adapter.
_OPEN_SUBSONIC_EXTENSIONS = {
    "apiKeyAuthentication": [1],
    "formPost": [1],
}


class Params(Mapping[str, str]):
    """
    Ordered multi-value request parameters.

    ``get`` returns the first occurrence; ``getlist`` returns all of them.
    The ``Mapping`` surface is what ``auth`` and ``responses`` consume.
    """

    def __init__(self, pairs: Sequence[Tuple[str, str]]):
        self._items: Dict[str, List[str]] = {}
        for key, value in pairs:
            self._items.setdefault(key, []).append(value)

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, name: str) -> str:
        values = self._items[name]
        return values[0]

    @overload
    def get(self, name: str, /) -> Optional[str]: ...

    @overload
    def get(self, name: str, default: str, /) -> str: ...

    @overload
    def get(self, name: str, default: _T, /) -> Union[str, _T]: ...

    def get(self, name: str, default: Any = None) -> Any:
        """Return the first value for ``name``."""
        values = self._items.get(name)
        return values[0] if values else default

    def getlist(self, name: str) -> List[str]:
        """Return every value for ``name``."""
        return list(self._items.get(name, []))

    def integer(self, name: str, default: Optional[int] = None, *, required: bool = False) -> Optional[int]:
        """Return ``name`` parsed as int, raising the protocol error on bad input."""
        raw = self.get(name)
        if raw is None:
            if required:
                raise missing_parameter(name)
            return default
        try:
            return int(raw)
        except ValueError:
            if required:
                raise missing_parameter(name)
            return default

    def boolean(self, name: str, default: bool = False) -> bool:
        """Return ``name`` parsed as a Subsonic boolean (``true``/``false``)."""
        raw = self.get(name)
        if raw is None:
            return default
        return raw.lower() == "true"


async def _collect_params(request: Request) -> Params:
    """Merge query-string and form-body parameters into one :class:`Params`."""
    pairs: List[Tuple[str, str]] = list(request.query_params.multi_items())
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            pairs.extend((str(k), str(v)) for k, v in form.multi_items())
    return Params(pairs)


@dataclass
class _Ctx:
    """Per-request context passed to endpoint handlers."""

    request: Request
    db: AsyncSession
    user: User
    params: Params
    config: SonghiveConfig
    redis: Optional[Redis]
    storage: StorageService


_Handler = Callable[[_Ctx], Awaitable[Dict[str, Any]]]


def _check_version(params: Params) -> None:
    """Reject clients requiring a newer protocol than we implement."""
    raw = params.get("v")
    if not raw:
        return
    try:
        client_version = tuple(int(part) for part in raw.split("."))
    except ValueError:
        return
    server_version = tuple(int(part) for part in API_VERSION.split("."))
    if client_version > server_version:
        raise SubsonicError(
            SERVER_PROTOCOL_TOO_OLD,
            f"Server must upgrade: client requires protocol {raw}, server supports {API_VERSION}",
        )


async def _dispatch(request: Request, db: AsyncSession, handler: _Handler) -> Response:
    """Authenticate, invoke ``handler`` and render the Subsonic envelope."""
    params = await _collect_params(request)
    try:
        config: SonghiveConfig = request.app.state.config
        redis = getattr(request.app.state, "redis", None)
        user = await authenticate_subsonic(db, params, config.auth.secret_key, redis)
        _check_version(params)
        storage = get_storage_service(request)
        payload = await handler(_Ctx(request, db, user, params, config, redis, storage))
        data = envelope(payload)
    except SubsonicError as exc:
        data = error_envelope(exc.code, exc.message)
    except Exception:
        logger.exception("Subsonic endpoint failed")
        data = error_envelope(GENERIC_ERROR, "Internal server error")
    body, content_type = render(data, params)
    return Response(content=body, headers={"Content-Type": content_type})


def _endpoint(name: str):
    """Register ``/rest/{name}.view`` for GET and POST."""

    def decorator(fn: _Handler):
        async def _route(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
            return await _dispatch(request, db, fn)

        _route.__name__ = f"{name}_view"
        router.api_route(f"/rest/{name}.view", methods=["GET", "POST"], include_in_schema=False)(_route)
        return fn

    return decorator


async def _music_folder_library_id(ctx: _Ctx) -> Optional[str]:
    """Resolve ``musicFolderId`` to an accessible library id, or ``None`` for root."""
    folder = ctx.params.get("musicFolderId")
    if not folder or folder in {ROOT_MUSIC_FOLDER_ID, "-1"}:
        return None
    if not await acl.can_access(ctx.db, ctx.user, "library", folder):
        raise SubsonicError(NOT_FOUND, "Music folder not found")
    return folder


async def _require_track(ctx: _Ctx, track_id: Optional[str] = None) -> Track:
    """Load an accessible track or raise the protocol not-found error."""
    track_id = track_id or ctx.params.get("id")
    if not track_id:
        raise missing_parameter("id")
    track = await music.get_track(ctx.db, track_id, include={"artist", "album"})
    if track is None or not await acl.can_access(ctx.db, ctx.user, "track", track_id):
        raise SubsonicError(NOT_FOUND, "Song not found")
    return track


async def _require_album(ctx: _Ctx, album_id: Optional[str] = None) -> Album:
    """Load an accessible album or raise the protocol not-found error."""
    album_id = album_id or ctx.params.get("id")
    if not album_id:
        raise missing_parameter("id")
    album = await music.get_album(ctx.db, album_id, include={"artist"})
    if album is None or not await acl.can_access(ctx.db, ctx.user, "album", album_id):
        raise SubsonicError(NOT_FOUND, "Album not found")
    return album


async def _require_artist(ctx: _Ctx, artist_id: Optional[str] = None) -> Artist:
    """Load a visible artist or raise the protocol not-found error."""
    artist_id = artist_id or ctx.params.get("id")
    if not artist_id:
        raise missing_parameter("id")
    artist = await ctx.db.get(Artist, artist_id)
    if artist is None:
        raise SubsonicError(NOT_FOUND, "Artist not found")
    visible = await accessible_artist_map(ctx.db, ctx.user, artist_ids=[artist_id])
    if not visible:
        raise SubsonicError(NOT_FOUND, "Artist not found")
    return artist


async def _require_playlist(ctx: _Ctx, playlist_id: Optional[str] = None) -> Playlist:
    """Load an accessible playlist or raise the protocol not-found error."""
    playlist_id = playlist_id or ctx.params.get("id") or ctx.params.get("playlistId")
    if not playlist_id:
        raise missing_parameter("id")
    playlist = await music.get_playlist(ctx.db, playlist_id)
    if playlist is None or not await acl.can_access(ctx.db, ctx.user, "playlist", playlist_id):
        raise SubsonicError(NOT_FOUND, "Playlist not found")
    return playlist


async def _require_manageable_playlist(ctx: _Ctx, playlist_id: Optional[str] = None) -> Playlist:
    """Load a playlist the user may modify."""
    playlist = await _require_playlist(ctx, playlist_id)
    if not await acl.can_manage(ctx.db, ctx.user, "playlist", playlist.id):
        raise SubsonicError(NOT_AUTHORIZED, "Not authorized to modify this playlist")
    return playlist


async def _songs_payload(ctx: _Ctx, tracks: List[Track]) -> List[Dict[str, Any]]:
    """Serialize tracks, annotating starred timestamps for the requester."""
    stars = await favorite_map(ctx.db, ctx.user, [str(t.id) for t in tracks])
    return [sz.song_dict(t, starred_at=stars.get(str(t.id))) for t in tracks]


async def _album_payloads(ctx: _Ctx, albums: List[Album]) -> List[Dict[str, Any]]:
    """Serialize albums with batched song counts/durations."""
    stats = await album_stats_map(ctx.db, ctx.user, [str(a.id) for a in albums])
    return [
        sz.album_dict(
            album,
            song_count=stats.get(str(album.id), {}).get("song_count"),
            duration=stats.get(str(album.id), {}).get("duration"),
            play_count=stats.get(str(album.id), {}).get("play_count"),
        )
        for album in albums
    ]


async def _accessible_albums(ctx: _Ctx, *, artist_id: Optional[str] = None) -> List[Album]:
    """Return accessible albums, optionally limited to one artist."""
    stmt = select(Album).order_by(Album.release_year.is_(None), Album.release_year.desc(), Album.id)
    if artist_id is not None:
        stmt = stmt.where(Album.artist_id == artist_id)
    stmt = acl.apply_access_filter(stmt, Album, ctx.user, "album")
    result = await ctx.db.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------


@_endpoint("ping")
async def _ping(ctx: _Ctx) -> Dict[str, Any]:
    return {}


@_endpoint("getLicense")
async def _license(ctx: _Ctx) -> Dict[str, Any]:
    return {"license": {"valid": True, "email": ctx.user.email or ""}}


@_endpoint("getOpenSubsonicExtensions")
async def _open_subsonic_extensions(ctx: _Ctx) -> Dict[str, Any]:
    return {
        "openSubsonicExtensions": {
            "extension": [{"name": name, "version": versions} for name, versions in _OPEN_SUBSONIC_EXTENSIONS.items()]
        }
    }


# ---------------------------------------------------------------------------
# Browsing
# ---------------------------------------------------------------------------


@_endpoint("getMusicFolders")
async def _music_folders(ctx: _Ctx) -> Dict[str, Any]:
    folders: List[Dict[str, Any]] = [{"id": ROOT_MUSIC_FOLDER_ID, "name": "Songhive"}]
    libraries = await music.list_libraries(ctx.db, user=ctx.user, include_external=True, limit=500)
    folders.extend({"id": str(lib.id), "name": lib.name} for lib in libraries)
    return {"musicFolders": {"musicFolder": folders}}


async def _artist_indexes(ctx: _Ctx) -> List[Dict[str, Any]]:
    """Return Subsonic ``index`` entries grouping visible artists by letter."""
    library_id = await _music_folder_library_id(ctx)
    artist_ids = await artist_ids_in_library(ctx.db, library_id) if library_id else None
    artists = await accessible_artist_map(ctx.db, ctx.user, artist_ids=artist_ids)
    album_counts = await artist_album_count_map(ctx.db, ctx.user, [str(a.id) for a in artists])
    return [
        {
            "name": group["name"],
            "artist": [sz.artist_dict(a, album_count=album_counts.get(str(a.id), 0)) for a in group["artist"]],
        }
        for group in group_artists_by_index(artists)
    ]


@_endpoint("getIndexes")
async def _indexes(ctx: _Ctx) -> Dict[str, Any]:
    return {
        "indexes": {
            "ignoredArticles": IGNORED_ARTICLES,
            "lastModified": int(time.time() * 1000),
            "index": await _artist_indexes(ctx),
        }
    }


@_endpoint("getArtists")
async def _artists(ctx: _Ctx) -> Dict[str, Any]:
    return {"artists": {"ignoredArticles": IGNORED_ARTICLES, "index": await _artist_indexes(ctx)}}


@_endpoint("getMusicDirectory")
async def _music_directory(ctx: _Ctx) -> Dict[str, Any]:
    """Resolve a directory id: artist ids list albums, album ids list songs."""
    dir_id = ctx.params.get("id")
    if not dir_id:
        raise missing_parameter("id")

    artist = await ctx.db.get(Artist, dir_id)
    if artist is not None:
        albums = await _accessible_albums(ctx, artist_id=dir_id)
        children = [sz.album_dir_dict(a) for a in albums]
        return {
            "directory": {
                "id": dir_id,
                "name": artist.name,
                "child": children,
            }
        }

    album = await _require_album(ctx, dir_id)
    tracks, _ = await music.list_tracks(
        ctx.db,
        album_id=dir_id,
        user=ctx.user,
        limit=10000,
        include={"artist", "album"},
    )
    return {
        "directory": {
            "id": dir_id,
            "name": album.title,
            "parent": str(album.artist_id),
            "child": await _songs_payload(ctx, tracks),
        }
    }


@_endpoint("getArtist")
async def _get_artist(ctx: _Ctx) -> Dict[str, Any]:
    artist = await _require_artist(ctx)
    albums = await _accessible_albums(ctx, artist_id=str(artist.id))
    counts = await artist_album_count_map(ctx.db, ctx.user, [str(artist.id)])
    payload = sz.artist_dict(artist, album_count=counts.get(str(artist.id), 0))
    payload["album"] = await _album_payloads(ctx, albums)
    return {"artist": payload}


@_endpoint("getAlbum")
async def _get_album(ctx: _Ctx) -> Dict[str, Any]:
    album = await _require_album(ctx)
    tracks, _ = await music.list_tracks(
        ctx.db,
        album_id=str(album.id),
        user=ctx.user,
        limit=10000,
        include={"artist", "album"},
    )
    stats = await music.get_album_stats(ctx.db, str(album.id), user=ctx.user)
    payload = sz.album_dict(
        album,
        song_count=stats["track_count"],
        duration=stats["total_duration"],
    )
    payload["song"] = await _songs_payload(ctx, tracks)
    return {"album": payload}


@_endpoint("getSong")
async def _get_song(ctx: _Ctx) -> Dict[str, Any]:
    track = await _require_track(ctx)
    stars = await favorite_map(ctx.db, ctx.user, [str(track.id)])
    return {"song": sz.song_dict(track, starred_at=stars.get(str(track.id)))}


async def _artist_info_payload(ctx: _Ctx) -> Dict[str, Any]:
    artist = await _require_artist(ctx)
    image_url = artist.image_url
    info: Dict[str, Any] = {
        "biography": artist.bio or "",
        "musicBrainzId": artist.musicbrainz_id,
        "smallImageUrl": image_url,
        "mediumImageUrl": image_url,
        "largeImageUrl": image_url,
        "similarArtist": [],
    }
    return {k: v for k, v in info.items() if v is not None}


@_endpoint("getArtistInfo")
async def _artist_info(ctx: _Ctx) -> Dict[str, Any]:
    return {"artistInfo": await _artist_info_payload(ctx)}


@_endpoint("getArtistInfo2")
async def _artist_info2(ctx: _Ctx) -> Dict[str, Any]:
    return {"artistInfo2": await _artist_info_payload(ctx)}


async def _album_info_payload(ctx: _Ctx) -> Dict[str, Any]:
    album = await _require_album(ctx)
    info: Dict[str, Any] = {
        "notes": album.description or "",
        "musicBrainzId": album.musicbrainz_id,
        "smallImageUrl": album.cover_url,
        "mediumImageUrl": album.cover_url,
        "largeImageUrl": album.cover_url,
    }
    return {k: v for k, v in info.items() if v is not None}


@_endpoint("getAlbumInfo")
async def _album_info(ctx: _Ctx) -> Dict[str, Any]:
    return {"albumInfo": await _album_info_payload(ctx)}


@_endpoint("getAlbumInfo2")
async def _album_info2(ctx: _Ctx) -> Dict[str, Any]:
    return {"albumInfo2": await _album_info_payload(ctx)}


@_endpoint("getGenres")
async def _genres(ctx: _Ctx) -> Dict[str, Any]:
    rows = await genre_counts(ctx.db, ctx.user)
    return {
        "genres": {
            "genre": [
                {"value": name, "songCount": song_count, "albumCount": album_count}
                for name, song_count, album_count in rows
            ]
        }
    }


@_endpoint("getSongsByGenre")
async def _songs_by_genre(ctx: _Ctx) -> Dict[str, Any]:
    genre = ctx.params.get("genre")
    if not genre:
        raise missing_parameter("genre")
    count = ctx.params.integer("count", 10) or 10
    offset = ctx.params.integer("offset", 0) or 0
    tracks, _ = await music.list_tracks(
        ctx.db,
        genre=genre,
        user=ctx.user,
        library_id=await _music_folder_library_id(ctx),
        limit=count,
        offset=offset,
        include={"artist", "album"},
        sort_by="title",
    )
    return {"songsByGenre": {"song": await _songs_payload(ctx, tracks)}}


@_endpoint("getRandomSongs")
async def _random_songs(ctx: _Ctx) -> Dict[str, Any]:
    size = ctx.params.integer("size", 10) or 10
    size = min(size, 500)
    tracks, _ = await music.list_tracks(
        ctx.db,
        genre=ctx.params.get("genre"),
        year_from=ctx.params.integer("fromYear"),
        year_to=ctx.params.integer("toYear"),
        library_id=await _music_folder_library_id(ctx),
        user=ctx.user,
        limit=size,
        include={"artist", "album"},
        sort_by="created_at",
    )
    # Shuffle in Python: list_tracks has no random ordering, and shuffling a
    # capped page is acceptable for the adapter (sizes are small).
    random.shuffle(tracks)
    return {"randomSongs": {"song": await _songs_payload(ctx, tracks)}}


@_endpoint("getNowPlaying")
async def _now_playing(ctx: _Ctx) -> Dict[str, Any]:
    entries = now_playing.current()
    track_ids = [entry.track_id for entry in entries]
    tracks = {}
    if track_ids:
        accessible = await acl.filter_accessible_track_ids(ctx.db, ctx.user, track_ids)
        for track_id in accessible:
            track = await music.get_track(ctx.db, track_id, include={"artist", "album"})
            if track is not None:
                tracks[track_id] = track

    payload = []
    stars = await favorite_map(ctx.db, ctx.user, list(tracks.keys()))
    for entry in entries:
        track = tracks.get(entry.track_id)
        if track is None:
            continue
        song = sz.song_dict(track, starred_at=stars.get(entry.track_id))
        song["username"] = entry.username
        song["minutesAgo"] = now_playing.minutes_ago(entry)
        if entry.player:
            song["playerName"] = entry.player
        payload.append(song)
    return {"nowPlaying": {"entry": payload}}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


async def _search(ctx: _Ctx) -> Dict[str, Any]:
    """Shared search3/search2/search implementation."""
    query = ctx.params.get("query") or ""
    library_id = await _music_folder_library_id(ctx)
    artist_ids = await artist_ids_in_library(ctx.db, library_id) if library_id else None

    artists = (
        await accessible_artist_map(
            ctx.db,
            ctx.user,
            artist_ids=artist_ids,
            query=query or None,
        )
    )[: ctx.params.integer("artistCount", 20) or 20]

    albums = await music.list_albums(
        ctx.db,
        query=query or None,
        user=ctx.user,
        limit=ctx.params.integer("albumCount", 20) or 20,
        offset=ctx.params.integer("albumOffset", 0) or 0,
        include={"artist"},
    )
    if library_id:
        allowed = set(await album_ids_in_library(ctx.db, library_id))
        albums = [a for a in albums if str(a.id) in allowed]

    tracks, _ = await music.list_tracks(
        ctx.db,
        query=query or None,
        library_id=library_id,
        user=ctx.user,
        limit=ctx.params.integer("songCount", 20) or 20,
        offset=ctx.params.integer("songOffset", 0) or 0,
        include={"artist", "album"},
        sort_by="title",
    )
    return {
        "artist": [sz.artist_dict(a) for a in artists],
        "album": await _album_payloads(ctx, albums),
        "song": await _songs_payload(ctx, tracks),
    }


@_endpoint("search3")
async def _search3(ctx: _Ctx) -> Dict[str, Any]:
    return {"searchResult3": await _search(ctx)}


@_endpoint("search2")
async def _search2(ctx: _Ctx) -> Dict[str, Any]:
    return {"searchResult2": await _search(ctx)}


@_endpoint("search")
async def _search1(ctx: _Ctx) -> Dict[str, Any]:
    """Legacy search.view: ``match`` children across artist/album/title terms."""
    count = ctx.params.integer("count", 20) or 20
    offset = ctx.params.integer("offset", 0) or 0
    matches: List[Dict[str, Any]] = []

    artist_term = ctx.params.get("artist") or ctx.params.get("any")
    if artist_term:
        for artist in await accessible_artist_map(ctx.db, ctx.user, query=artist_term):
            matches.append({"id": str(artist.id), "name": artist.name, "isDir": True})

    album_term = ctx.params.get("album") or ctx.params.get("any")
    if album_term:
        albums = await music.list_albums(ctx.db, query=album_term, user=ctx.user, limit=count, include={"artist"})
        matches.extend(sz.album_dir_dict(a) for a in albums)

    song_term = ctx.params.get("title") or ctx.params.get("any")
    if song_term:
        tracks, _ = await music.list_tracks(
            ctx.db,
            query=song_term,
            user=ctx.user,
            limit=count,
            offset=offset,
            include={"artist", "album"},
            sort_by="title",
        )
        matches.extend(await _songs_payload(ctx, tracks))

    return {"searchResult": {"match": matches[:count]}}


# ---------------------------------------------------------------------------
# Album lists
# ---------------------------------------------------------------------------


async def _album_list(ctx: _Ctx) -> List[Album]:
    """Resolve ``getAlbumList``/``getAlbumList2`` type filters to an album list."""
    p = ctx.params
    list_type = p.get("type", "newest")
    size = min(p.integer("size", 10) or 10, 500)
    offset = p.integer("offset", 0) or 0
    library_id = await _music_folder_library_id(ctx)

    if list_type == "starred":
        # Songhive favorites cover tracks only; no album-level starring.
        return []

    sort_by = "title"
    sort_dir = "asc"
    query: Optional[str] = None
    year_from = p.integer("fromYear")
    year_to = p.integer("toYear")
    genre: Optional[str] = None

    if list_type in {"newest", "recent", "frequent", "highest"}:
        sort_by, sort_dir = "created_at", "desc"
    elif list_type == "alphabeticalByName":
        sort_by, sort_dir = "title", "asc"
    elif list_type == "alphabeticalByArtist":
        sort_by, sort_dir = "artist_name", "asc"
    elif list_type == "random":
        sort_by = "title"
    elif list_type == "byYear":
        if year_from is None or year_to is None:
            raise missing_parameter("fromYear" if year_from is None else "toYear")
    elif list_type == "byGenre":
        genre = p.get("genre")
        if not genre:
            raise missing_parameter("genre")
    else:
        raise SubsonicError(GENERIC_ERROR, f"Unknown album list type: {list_type}")

    if list_type in {"recent", "highest", "frequent"}:
        # No per-album play/recency aggregates; frequent falls back to newest.
        sort_by, sort_dir = "created_at", "desc"

    albums = await music.list_albums(
        ctx.db,
        query=query,
        genre=genre,
        year_from=year_from if list_type == "byYear" else None,
        year_to=year_to if list_type == "byYear" else None,
        user=ctx.user,
        limit=size if list_type != "random" else max(size * 4, 50),
        offset=offset,
        include={"artist"},
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    if library_id:
        allowed = set(await album_ids_in_library(ctx.db, library_id))
        albums = [a for a in albums if str(a.id) in allowed]
    if list_type == "random":
        random.shuffle(albums)
        albums = albums[:size]
    return albums


@_endpoint("getAlbumList2")
async def _album_list2(ctx: _Ctx) -> Dict[str, Any]:
    return {"albumList2": {"album": await _album_payloads(ctx, await _album_list(ctx))}}


@_endpoint("getAlbumList")
async def _album_list_view(ctx: _Ctx) -> Dict[str, Any]:
    albums = await _album_list(ctx)
    return {"albumList": {"album": [sz.album_dir_dict(a) for a in albums]}}


# ---------------------------------------------------------------------------
# Starred / favorites
# ---------------------------------------------------------------------------


async def _starred(ctx: _Ctx, key: str) -> Dict[str, Any]:
    """Return the user's favorite tracks under ``starred`` or ``starred2``."""
    result = await ctx.db.execute(
        select(Favorite).where(Favorite.user_id == ctx.user.id).order_by(Favorite.created_at.desc())
    )
    favorites = list(result.scalars().all())
    track_ids = [str(f.track_id) for f in favorites]
    accessible = await acl.filter_accessible_track_ids(ctx.db, ctx.user, track_ids)

    songs = []
    starred_at = {str(f.track_id): f.created_at for f in favorites}
    for track_id in track_ids:
        if track_id not in accessible:
            continue
        track = await music.get_track(ctx.db, track_id, include={"artist", "album"})
        if track is not None:
            songs.append(sz.song_dict(track, starred_at=starred_at.get(track_id)))

    return {key: {"artist": [], "album": [], "song": songs}}


@_endpoint("getStarred")
async def _get_starred(ctx: _Ctx) -> Dict[str, Any]:
    return await _starred(ctx, "starred")


@_endpoint("getStarred2")
async def _get_starred2(ctx: _Ctx) -> Dict[str, Any]:
    return await _starred(ctx, "starred2")


@_endpoint("star")
async def _star(ctx: _Ctx) -> Dict[str, Any]:
    track_ids = ctx.params.getlist("id")
    accessible = await acl.filter_accessible_track_ids(ctx.db, ctx.user, track_ids)
    for track_id in track_ids:
        if track_id not in accessible:
            continue
        result = await ctx.db.execute(
            select(Favorite).where(Favorite.user_id == ctx.user.id, Favorite.track_id == track_id)
        )
        if result.scalar_one_or_none() is not None:
            continue
        ctx.db.add(Favorite(user_id=ctx.user.id, track_id=track_id))
        track = await ctx.db.get(Track, track_id)
        if track is not None:
            await ctx.db.flush()
            await _notify_favorite(ctx.db, track, ctx.user)
    await ctx.db.commit()
    return {}


@_endpoint("unstar")
async def _unstar(ctx: _Ctx) -> Dict[str, Any]:
    track_ids = ctx.params.getlist("id")
    for track_id in track_ids:
        result = await ctx.db.execute(
            select(Favorite).where(Favorite.user_id == ctx.user.id, Favorite.track_id == track_id)
        )
        favorite = result.scalar_one_or_none()
        if favorite is not None:
            await ctx.db.delete(favorite)
            await _retract_favorite_notification(ctx.db, track_id, ctx.user)
    await ctx.db.commit()
    return {}


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------


async def _playlist_entry_songs(ctx: _Ctx, playlist: Playlist) -> List[Dict[str, Any]]:
    """Return serialized, access-filtered playlist entries in order."""
    tracks = await music.list_playlist_tracks(
        ctx.db,
        str(playlist.id),
        user=ctx.user,
        limit=10000,
        include={"artist", "album"},
    )
    return await _songs_payload(ctx, tracks)


@_endpoint("getPlaylists")
async def _playlists(ctx: _Ctx) -> Dict[str, Any]:
    owner_id: Optional[str] = None
    username = ctx.params.get("username")
    if username:
        if username != ctx.user.username and not ctx.user.is_admin:
            raise SubsonicError(NOT_AUTHORIZED, "Not authorized to list other users' playlists")
        owner = await get_user_by_username(ctx.db, username)
        if owner is None:
            raise SubsonicError(NOT_FOUND, "User not found")
        owner_id = str(owner.id)

    playlists = await music.list_playlists(ctx.db, user=ctx.user, owner_id=owner_id, limit=1000)
    payload = []
    for playlist in playlists:
        stats = await music.get_playlist_stats(ctx.db, str(playlist.id), user=ctx.user)
        payload.append(
            sz.playlist_dict(
                playlist,
                song_count=stats["track_count"],
                duration=stats["total_duration"],
            )
        )
    return {"playlists": {"playlist": payload}}


@_endpoint("getPlaylist")
async def _get_playlist(ctx: _Ctx) -> Dict[str, Any]:
    playlist = await _require_playlist(ctx)
    stats = await music.get_playlist_stats(ctx.db, str(playlist.id), user=ctx.user)
    payload = sz.playlist_dict(
        playlist,
        song_count=stats["track_count"],
        duration=stats["total_duration"],
    )
    payload["entry"] = await _playlist_entry_songs(ctx, playlist)
    return {"playlist": payload}


@_endpoint("createPlaylist")
async def _create_playlist(ctx: _Ctx) -> Dict[str, Any]:
    song_ids = ctx.params.getlist("songId")
    playlist_id = ctx.params.get("playlistId")

    if playlist_id:
        playlist = await _require_manageable_playlist(ctx, playlist_id)
        name = ctx.params.get("name")
        if name:
            playlist.name = name
        # Replace the track list when songIds are provided. Podcast episode
        # entries are kept — Subsonic clients cannot see or address them.
        if song_ids:
            existing = await ctx.db.execute(
                select(PlaylistTrack).where(
                    PlaylistTrack.playlist_id == playlist.id,
                    PlaylistTrack.track_id.isnot(None),
                )
            )
            for row in existing.scalars().all():
                await ctx.db.delete(row)
            await ctx.db.flush()
            await music.add_playlist_tracks(ctx.db, str(playlist.id), song_ids, allow_duplicates=True)
        action = "playlist.update"
    else:
        name = ctx.params.get("name")
        if not name:
            raise missing_parameter("name")
        playlist = Playlist(
            name=name,
            owner_id=ctx.user.id,
            visibility=Visibility.PRIVATE.value,
        )
        ctx.db.add(playlist)
        await ctx.db.flush()
        if song_ids:
            await music.add_playlist_tracks(ctx.db, str(playlist.id), song_ids, allow_duplicates=True)
        action = "playlist.create"

    await audit.log_action(
        ctx.db,
        actor_id=ctx.user.id,
        action=action,
        target_type=AuditTargetType.PLAYLIST,
        target_id=str(playlist.id),
        details={"name": playlist.name, "song_ids": song_ids, "via": "subsonic"},
        ip_address=client_ip(ctx.request),
    )
    await ctx.db.commit()

    stats = await music.get_playlist_stats(ctx.db, str(playlist.id), user=ctx.user)
    payload = sz.playlist_dict(
        playlist,
        song_count=stats["track_count"],
        duration=stats["total_duration"],
    )
    payload["entry"] = await _playlist_entry_songs(ctx, playlist)
    return {"playlist": payload}


@_endpoint("updatePlaylist")
async def _update_playlist(ctx: _Ctx) -> Dict[str, Any]:
    playlist = await _require_manageable_playlist(ctx)

    name = ctx.params.get("name")
    if name is not None:
        playlist.name = name
    comment = ctx.params.get("comment")
    if comment is not None:
        playlist.description = comment
    if "public" in ctx.params:
        playlist.visibility = Visibility.PUBLIC.value if ctx.params.boolean("public") else Visibility.PRIVATE.value

    # songIndexToRemove may repeat; collect every occurrence.
    raw_indexes: List[int] = []
    for value in ctx.params.getlist("songIndexToRemove"):
        try:
            raw_indexes.append(int(value))
        except ValueError:
            continue
    removed = await remove_playlist_tracks_at_indexes(ctx.db, str(playlist.id), raw_indexes)

    song_ids_to_add = ctx.params.getlist("songIdToAdd")
    added: List[str] = []
    if song_ids_to_add:
        accessible = await acl.filter_accessible_track_ids(ctx.db, ctx.user, song_ids_to_add)
        ordered = [tid for tid in song_ids_to_add if tid in accessible]
        added = await music.add_playlist_tracks(ctx.db, str(playlist.id), ordered, allow_duplicates=True)

    await audit.log_action(
        ctx.db,
        actor_id=ctx.user.id,
        action="playlist.update",
        target_type=AuditTargetType.PLAYLIST,
        target_id=str(playlist.id),
        details={
            "name": playlist.name,
            "visibility": playlist.visibility,
            "added_track_ids": added,
            "removed_count": removed,
            "removed_indexes": raw_indexes,
            "via": "subsonic",
        },
        ip_address=client_ip(ctx.request),
    )
    await ctx.db.commit()
    return {}


@_endpoint("deletePlaylist")
async def _delete_playlist(ctx: _Ctx) -> Dict[str, Any]:
    playlist = await _require_manageable_playlist(ctx)
    await audit.log_action(
        ctx.db,
        actor_id=ctx.user.id,
        action="playlist.delete",
        target_type=AuditTargetType.PLAYLIST,
        target_id=str(playlist.id),
        details={"name": playlist.name, "via": "subsonic"},
        ip_address=client_ip(ctx.request),
    )
    await deletion.delete_playlist(
        ctx.db,
        ctx.storage,
        str(playlist.id),
        recursive=False,
        user=ctx.user,
        is_admin=ctx.user.is_admin,
    )
    await ctx.db.commit()
    return {}


# ---------------------------------------------------------------------------
# Playback reporting
# ---------------------------------------------------------------------------


@_endpoint("scrobble")
@_endpoint("nowPlaying")
async def _scrobble(ctx: _Ctx) -> Dict[str, Any]:
    submission = ctx.params.boolean("submission", True)
    player = ctx.params.get("c")
    for track_id in ctx.params.getlist("id"):
        if not await acl.can_access(ctx.db, ctx.user, "track", track_id):
            continue
        if submission:
            await record_listen(ctx.db, str(ctx.user.id), track_id)
        else:
            now_playing.record(str(ctx.user.id), ctx.user.username, track_id, player)
    await ctx.db.commit()
    return {}


@_endpoint("setRating")
async def _set_rating(ctx: _Ctx) -> Dict[str, Any]:
    # Songhive has no rating system; accept and ignore.
    return {}


@_endpoint("savePlayQueue")
async def _save_play_queue(ctx: _Ctx) -> Dict[str, Any]:
    # Play queues are client-side state; accept and ignore.
    return {}


@_endpoint("getPlayQueue")
async def _get_play_queue(ctx: _Ctx) -> Dict[str, Any]:
    return {"playQueue": {}}


@_endpoint("getVideos")
async def _get_videos(ctx: _Ctx) -> Dict[str, Any]:
    return {"videos": {"video": []}}


@_endpoint("getBookmarks")
async def _get_bookmarks(ctx: _Ctx) -> Dict[str, Any]:
    return {"bookmarks": {"bookmark": []}}


@_endpoint("getLyrics")
@_endpoint("getLyricsBySongId")
async def _get_lyrics(ctx: _Ctx) -> Dict[str, Any]:
    return {"lyrics": {}}


# ---------------------------------------------------------------------------
# Unknown methods
# ---------------------------------------------------------------------------


async def _unimplemented_view(request: Request, method: str, db: AsyncSession = Depends(get_db)) -> Response:
    """Return a protocol-level error for ``.view`` methods we do not implement."""

    async def _fail(ctx: _Ctx) -> Dict[str, Any]:
        raise SubsonicError(GENERIC_ERROR, f"Method '{method}' is not implemented")

    return await _dispatch(request, db, _fail)


def register_fallback_route() -> None:
    """
    Register the ``/rest/{method}.view`` catch-all.

    FastAPI matches routes in registration order, so this must run *after*
    every concrete ``.view`` route — including the media endpoints in
    ``media.py``, which attach to this same router — or the catch-all would
    shadow them. ``adapter.router()`` invokes it once imports are complete.
    """
    router.api_route("/rest/{method}.view", methods=["GET", "POST"], include_in_schema=False)(_unimplemented_view)
