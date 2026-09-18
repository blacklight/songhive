"""
Binary Subsonic endpoints: ``stream``, ``download``, ``getCoverArt``,
``getAvatar``.

Under the Tornado bootstrap, ``stream.view``/``download.view`` are served by
the native handlers in ``handlers.py`` (chunked delivery, live transcoding,
external-stream proxying); the FastAPI routes here are the uvicorn fallback
and the path exercised by the ASGI test client. ``getCoverArt``/``getAvatar``
are FastAPI-only.

Errors still answer with the standard Subsonic envelope, so these handlers
share the routes module's param collection and auth machinery.
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

from fastapi import Depends, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.deps import get_db, get_storage_service
from ...external.errors import ExternalItemNotFound, ExternalLibraryError, UnsupportedExternalOperation
from ...external.types import ExternalStream
from ...models.artist import Artist
from ...models.stored_file import StoredFile
from ...services import acl, music
from ...services.auth import get_user_by_username
from ...services.streaming import (
    collect_external_stream,
    get_cached_transcode,
    resolve_external_stream,
    resolve_track_file,
)
from ...storage import get_storage
from ...streaming.transcoder import Transcoder
from . import now_playing
from .auth import authenticate_subsonic
from .errors import GENERIC_ERROR, NOT_AUTHORIZED, NOT_FOUND, SubsonicError, missing_parameter
from .responses import error_envelope, render
from .routes import _check_version, _collect_params, _Ctx, router
from .serializers import parse_cover_art_id

logger = logging.getLogger(__name__)

_FORMAT_BY_MIMETYPE = {fmt["mimetype"]: key for key, fmt in Transcoder.FORMAT_MAP.items()}


async def _dispatch_binary(request: Request, db: AsyncSession, handler) -> Response:
    """Authenticate and invoke a binary handler; errors render as envelopes."""
    params = await _collect_params(request)
    try:
        config = request.app.state.config
        redis = getattr(request.app.state, "redis", None)
        user = await authenticate_subsonic(db, params, config.auth.secret_key, redis)
        _check_version(params)
        ctx = _Ctx(request, db, user, params, config, redis, get_storage_service(request))
        return await handler(ctx)
    except SubsonicError as exc:
        data = error_envelope(exc.code, exc.message)
    except Exception:
        logger.exception("Subsonic binary endpoint failed")
        data = error_envelope(GENERIC_ERROR, "Internal server error")
    body, content_type = render(data, params)
    return Response(content=body, headers={"Content-Type": content_type})


def _requested_transcode(ctx: _Ctx, stored_file: StoredFile) -> Optional[Tuple[str, Optional[str]]]:
    """Map ``format``/``maxBitRate`` params to a transcode target, if any."""
    fmt = ctx.params.get("format")
    if fmt == "raw":
        fmt = None
    bitrate = ctx.params.get("maxBitRate")
    if fmt is None and bitrate is None:
        return None

    source_format = _FORMAT_BY_MIMETYPE.get((stored_file.content_type or "").split(";")[0].strip())
    if fmt is None:
        fmt = source_format
    if fmt is None:
        return None
    if fmt == source_format and bitrate is None:
        return None
    return fmt, bitrate


async def _stream(ctx: _Ctx, *, download: bool = False) -> Response:
    """Serve a track's audio bytes (original or a cached transcode)."""
    track_id = ctx.params.get("id")
    if not track_id:
        raise missing_parameter("id")
    track = await music.get_track(ctx.db, track_id, include={"artist", "album"})
    if track is None:
        raise SubsonicError(NOT_FOUND, "Song not found")
    if not await acl.can_access(ctx.db, ctx.user, "track", track_id):
        raise SubsonicError(NOT_AUTHORIZED, "Access denied")
    config = ctx.config
    backend = get_storage(config.storage)
    range_header = ctx.request.headers.get("Range")

    stored_file = await resolve_track_file(ctx.db, str(track.id))
    if stored_file is not None:
        # Prefer a cached transcode when format/bitrate was requested.
        transcode = _requested_transcode(ctx, stored_file)
        if transcode is not None:
            fmt, bitrate = transcode
            cached = await get_cached_transcode(ctx.db, str(track.id), fmt, bitrate or "")
            if cached is not None:
                stored_file = cached

        try:
            local_path = await backend.retrieve(stored_file.storage_path)
        except ValueError:
            local_path = None
        if local_path is None:
            raise SubsonicError(NOT_FOUND, "Media file not found")

        now_playing.record(str(ctx.user.id), ctx.user.username, str(track.id), ctx.params.get("c"))
        headers = {}
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{track.title}"'
        return FileResponse(
            local_path,
            media_type=stored_file.content_type or "application/octet-stream",
            headers=headers,
        )

    stream = await _resolve_external(ctx, str(track.id), range_header)
    if stream is None:
        raise SubsonicError(NOT_FOUND, "Media file not found")

    now_playing.record(str(ctx.user.id), ctx.user.username, str(track.id), ctx.params.get("c"))
    return await _serve_external_stream(ctx, stream, download=download)


async def _resolve_external(ctx: _Ctx, track_id: str, range_header: Optional[str]) -> Optional[ExternalStream]:
    """Resolve an external stream, translating adapter errors to protocol codes."""
    try:
        return await resolve_external_stream(ctx.db, track_id, range_header)
    except ExternalItemNotFound as exc:
        raise SubsonicError(NOT_FOUND, str(exc) or "Media file not found") from exc
    except UnsupportedExternalOperation as exc:
        raise SubsonicError(GENERIC_ERROR, str(exc) or "Streaming unsupported") from exc
    except ExternalLibraryError as exc:
        raise SubsonicError(GENERIC_ERROR, str(exc) or "External library error") from exc


async def _serve_external_stream(ctx: _Ctx, stream: ExternalStream, *, download: bool = False) -> Response:
    """Serve a resolved external stream via redirect, file, or iterator."""
    headers = {"Content-Disposition": 'attachment; filename="track"'} if download else {}

    if stream.kind == "url" and stream.safe_to_redirect and stream.url:
        return RedirectResponse(stream.url)

    if stream.kind == "path" and stream.path is not None:
        return FileResponse(
            stream.path,
            media_type=stream.content_type or "application/octet-stream",
            headers=headers,
        )

    if stream.kind == "iterator" and stream.iterator is not None:
        return StreamingResponse(
            stream.iterator,
            media_type=stream.content_type or "application/octet-stream",
            headers=headers,
        )

    # URLs that are unsafe to redirect must be proxied through the backend.
    materialized = await collect_external_stream(stream, ctx.config)
    if isinstance(materialized, Path):
        return FileResponse(
            materialized,
            media_type=stream.content_type or "application/octet-stream",
            headers=headers,
        )
    return Response(
        content=bytes(materialized),
        media_type=stream.content_type or "application/octet-stream",
        headers=headers,
    )


@router.api_route("/rest/stream.view", methods=["GET", "POST"], include_in_schema=False)
async def stream_view(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    return await _dispatch_binary(request, db, lambda ctx: _stream(ctx, download=False))


@router.api_route("/rest/download.view", methods=["GET", "POST"], include_in_schema=False)
async def download_view(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    return await _dispatch_binary(request, db, lambda ctx: _stream(ctx, download=True))


# ---------------------------------------------------------------------------
# Cover art
# ---------------------------------------------------------------------------


async def _entity_cover(ctx: _Ctx) -> Tuple[Optional[StoredFile], Optional[str]]:
    """Resolve ``(stored_file, remote_url)`` for a coverArt id."""
    raw_id = ctx.params.get("id")
    if not raw_id:
        raise missing_parameter("id")

    kind, entity_id = parse_cover_art_id(raw_id)
    kinds = [kind] if kind is not None else ["track", "album", "artist", "playlist"]
    for candidate in kinds:
        resolved = await _cover_for_kind(ctx, candidate, entity_id)
        if resolved != (None, None):
            return resolved
    raise SubsonicError(NOT_FOUND, "Cover art not found")


async def _cover_for_kind(ctx: _Ctx, kind: str, entity_id: str) -> Tuple[Optional[StoredFile], Optional[str]]:
    """Resolve the cover for one entity kind; ``(None, None)`` when absent."""
    if kind == "track":
        track = await music.get_track(ctx.db, entity_id, include={"artist", "album"})
        if track is None or not await acl.can_access(ctx.db, ctx.user, "track", entity_id):
            return None, None
        if track.image_file is not None:
            return track.image_file, None
        album = track.album
        if album is not None:
            if album.cover_file is not None:
                return album.cover_file, None
            if album.cover_url:
                return None, album.cover_url
        return None, None

    if kind == "album":
        album = await music.get_album(ctx.db, entity_id, include={"artist"})
        if album is None or not await acl.can_access(ctx.db, ctx.user, "album", entity_id):
            return None, None
        if album.cover_file is not None:
            return album.cover_file, None
        if album.cover_url:
            return None, album.cover_url
        return None, None

    if kind == "artist":
        artist = await ctx.db.get(Artist, entity_id)
        if artist is None:
            return None, None
        if artist.image_file is not None:
            return artist.image_file, None
        if artist.cover_file is not None:
            return artist.cover_file, None
        if artist.image_url:
            return None, artist.image_url
        return None, None

    if kind == "playlist":
        playlist = await music.get_playlist(ctx.db, entity_id)
        if playlist is None or not await acl.can_access(ctx.db, ctx.user, "playlist", entity_id):
            return None, None
        if playlist.cover_file is not None:
            return playlist.cover_file, None
        if playlist.image_file is not None:
            return playlist.image_file, None
        return None, None

    return None, None


@router.api_route("/rest/getCoverArt.view", methods=["GET", "POST"], include_in_schema=False)
async def cover_art_view(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    async def _handler(ctx: _Ctx) -> Response:
        stored_file, remote_url = await _entity_cover(ctx)
        if remote_url:
            return RedirectResponse(remote_url)
        assert stored_file is not None
        backend = get_storage(ctx.config.storage)
        try:
            local_path = await backend.retrieve(stored_file.storage_path)
        except ValueError:
            local_path = None
        if local_path is None:
            raise SubsonicError(NOT_FOUND, "Cover art file not found")
        return FileResponse(local_path, media_type=stored_file.content_type or "image/jpeg")

    return await _dispatch_binary(request, db, _handler)


@router.api_route("/rest/getAvatar.view", methods=["GET", "POST"], include_in_schema=False)
async def avatar_view(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    async def _handler(ctx: _Ctx) -> Response:
        username = ctx.params.get("username")
        if not username:
            raise missing_parameter("username")
        user = await get_user_by_username(ctx.db, username)
        if user is None or not user.avatar_url:
            raise SubsonicError(NOT_FOUND, "Avatar not found")
        return RedirectResponse(user.avatar_url)

    return await _dispatch_binary(request, db, _handler)
