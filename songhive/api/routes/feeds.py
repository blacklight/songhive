"""
RSS/Atom feed routes, mounted at ``/feeds`` outside the versioned API.

Feeds are public protocol endpoints (like ``/webmentions`` and ``/ap``):
they serve XML to feed readers and are advertised through
``<link rel="alternate">`` tags on the SPA object pages. Anonymous
requests receive only public content; authenticated requests also cover
items visible to the requester.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.genre import Genre
from ...models.tag import Tag
from ...models.user import User
from ...services import acl
from ...services import feeds as feeds_service
from ...services import music as music_service
from ...services.activities import resolve_entity
from ...services.auth import get_user_by_username
from ...services.genres import validate_genre_name
from ...services.tags import validate_tag_name
from ..deps import _get_share_token, get_config, get_current_user_optional, get_db
from ..semantic_meta import public_base_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feeds", include_in_schema=False)

# SPA collection path prefixes that expose an activities feed.
_COLLECTION_ENTITY_TYPES = {
    "tracks": "track",
    "albums": "album",
    "artists": "artist",
    "playlists": "playlist",
    "libraries": "library",
    "radios": "radio",
}


def _check_feeds(config: SonghiveConfig, fmt: str) -> None:
    """Raise 404 when feeds are disabled or the format is unknown."""
    if not config.feeds.enabled or fmt not in feeds_service.FEED_FORMATS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _effective_limit(limit: Optional[int], config: SonghiveConfig) -> int:
    """Clamp ``limit`` to the configured feed size."""
    return min(limit or config.feeds.max_items, config.feeds.max_items)


def _feed_url(request: Request, base_url: str) -> str:
    """Return the absolute URL of the feed document being served."""
    url = f"{base_url}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    return url


def _render(feed: feeds_service.Feed, fmt: str) -> Response:
    """Serialise ``feed`` in the requested format."""
    if fmt == "rss":
        body = feeds_service.render_rss(feed)
    else:
        body = feeds_service.render_atom(feed)
    return Response(content=body, media_type=feeds_service.FEED_MEDIA_TYPES[fmt])


@router.get("/users/{username}.{fmt}")
async def user_feed(
    username: str,
    fmt: str,
    request: Request,
    limit: Optional[int] = Query(None, ge=1),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve a user's latest posts as an RSS or Atom feed."""
    _check_feeds(config, fmt)
    profile = await get_user_by_username(db, username)
    if profile is None or not await acl.can_access(db, user, "user", str(profile.id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    base_url = public_base_url(request, config)
    feed = await feeds_service.user_feed(
        db,
        profile,
        user,
        config,
        base_url,
        _feed_url(request, base_url),
        _effective_limit(limit, config),
    )
    return _render(feed, fmt)


@router.get("/artists/{artist_id}.{fmt}")
async def artist_feed(
    artist_id: str,
    fmt: str,
    request: Request,
    limit: Optional[int] = Query(None, ge=1),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve an artist's latest releases (albums and standalone tracks)."""
    _check_feeds(config, fmt)
    artist = await music_service.get_artist(db, artist_id)
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    base_url = public_base_url(request, config)
    feed = await feeds_service.artist_feed(
        db,
        artist,
        user,
        base_url,
        _feed_url(request, base_url),
        _effective_limit(limit, config),
    )
    return _render(feed, fmt)


async def _collection_feed(
    kind: str,
    entity_id: str,
    fmt: str,
    request: Request,
    limit: Optional[int],
    token: Optional[str],
    user: Optional[User],
    db: AsyncSession,
    config: SonghiveConfig,
) -> Response:
    """Shared handler for the playlist and library track feeds."""
    _check_feeds(config, fmt)
    entity: object
    if kind == "playlist":
        entity = await music_service.get_playlist(db, entity_id, include={"owner"})
    else:
        entity = await music_service.get_library(db, entity_id, include={"owner"})
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_access(db, user, kind, entity_id, share_token=token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    base_url = public_base_url(request, config)
    feed = await feeds_service.collection_feed(
        db,
        kind,
        entity,
        user,
        base_url,
        _feed_url(request, base_url),
        _effective_limit(limit, config),
    )
    return _render(feed, fmt)


@router.get("/playlists/{playlist_id}.{fmt}")
async def playlist_feed(
    playlist_id: str,
    fmt: str,
    request: Request,
    limit: Optional[int] = Query(None, ge=1),
    token: Optional[str] = Depends(_get_share_token),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve the tracks most recently added to a playlist."""
    return await _collection_feed("playlist", playlist_id, fmt, request, limit, token, user, db, config)


@router.get("/libraries/{library_id}.{fmt}")
async def library_feed(
    library_id: str,
    fmt: str,
    request: Request,
    limit: Optional[int] = Query(None, ge=1),
    token: Optional[str] = Depends(_get_share_token),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve the tracks most recently added to a library."""
    return await _collection_feed("library", library_id, fmt, request, limit, token, user, db, config)


@router.get("/tags/{name}.{fmt}")
async def tag_feed(
    name: str,
    fmt: str,
    request: Request,
    limit: Optional[int] = Query(None, ge=1),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve the most recent entities and activities carrying a tag."""
    _check_feeds(config, fmt)
    try:
        normalised = validate_tag_name(name)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from None
    result = await db.execute(select(Tag).where(Tag.name == normalised).limit(1))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    base_url = public_base_url(request, config)
    feed = await feeds_service.tag_feed(
        db,
        normalised,
        user,
        config,
        base_url,
        _feed_url(request, base_url),
        _effective_limit(limit, config),
    )
    return _render(feed, fmt)


@router.get("/genres/{name}.{fmt}")
async def genre_feed(
    name: str,
    fmt: str,
    request: Request,
    limit: Optional[int] = Query(None, ge=1),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve the most recent tracks and albums in a genre."""
    _check_feeds(config, fmt)
    try:
        normalised = validate_genre_name(name)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from None
    result = await db.execute(select(Genre).where(Genre.name == normalised).limit(1))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    base_url = public_base_url(request, config)
    feed = await feeds_service.genre_feed(
        db,
        normalised,
        user,
        base_url,
        _feed_url(request, base_url),
        _effective_limit(limit, config),
    )
    return _render(feed, fmt)


@router.get("/{collection}/{entity_id}/activities.{fmt}")
async def entity_activities_feed(
    collection: str,
    entity_id: str,
    fmt: str,
    request: Request,
    limit: Optional[int] = Query(None, ge=1),
    token: Optional[str] = Depends(_get_share_token),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve the activities received for an entity (its ``/activities`` page)."""
    _check_feeds(config, fmt)
    entity_type = _COLLECTION_ENTITY_TYPES.get(collection)
    if entity_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    entity = await resolve_entity(db, entity_type, entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_access(db, user, entity_type, entity_id, share_token=token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    base_url = public_base_url(request, config)
    feed = await feeds_service.entity_activities_feed(
        db,
        entity_type,
        entity,
        user,
        config,
        base_url,
        _feed_url(request, base_url),
        _effective_limit(limit, config),
    )
    return _render(feed, fmt)
