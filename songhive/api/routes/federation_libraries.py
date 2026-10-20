"""
Library federation documents and followers.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...federation import doc_cache
from ...federation.actors import get_federation_storage
from ...federation.serializers import (
    MUSIC_ENTITY_CONTEXT,
    library_to_music_object,
    music_collection_page,
)
from ...models import Library, Track, Visibility
from ...models.base import get_session
from ..deps import get_config
from .federation_common import (
    _document_ttl,
    _federating_user_or_none,
    _serve_cached_document,
)
from .federation_music import (
    _audio_items,
    _followers_collection_dict,
    _library_track_count,
    _library_track_query,
    _owner_actor_url,
)
from .profile_pages import _accepts_activitypub, _accepts_html, _spa_response

_LIBRARY_PAGE_SIZE = 100

router = APIRouter(include_in_schema=False)


@router.get("/libraries/{library_id}")
async def get_library_document(
    library_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """Serve a public Library as a federated music ``Library`` collection.

    ``GET /libraries/{id}`` returns the collection index; ``?page=N``
    returns a ``CollectionPage`` of ``Audio`` items — the same shapes
    Funkwhale exposes under ``/federation/music/libraries/{uuid}``, so a
    remote Funkwhale or Songhive instance can scan the whole library.
    Non-public libraries answer 404 so private data is never hinted at.
    """
    html_only = _accepts_html(request) and not _accepts_activitypub(request)
    page = request.query_params.get("page")
    if html_only and not page:
        return _spa_response()

    domain = config.federation.instance_domain
    ttl = _document_ttl(config)
    # Normalize the requested page up front so every spelling of the same
    # page ("", "0", "abc", "1") shares one cache key; no ``page`` param at
    # all means the collection index document.
    page_num: Optional[int] = None
    if page is not None:
        try:
            page_num = max(1, int(page))
        except ValueError:
            page_num = 1

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            library = (
                await db.execute(
                    select(Library)
                    .options(selectinload(Library.owner))
                    .where(
                        Library.id == library_id,
                        Library.visibility == Visibility.PUBLIC.value,
                    )
                )
            ).scalar_one_or_none()
            if library is None:
                return None

            actor_url = _owner_actor_url(library.owner, config)
            library_url = f"https://{domain}/libraries/{library.id}"
            total = (await db.execute(_library_track_count(library.id))).scalar_one()
            if page_num is None:
                doc = library_to_music_object(
                    library_url,
                    library.name,
                    actor_url,
                    total,
                    summary=library.description,
                    page_size=_LIBRARY_PAGE_SIZE,
                )
                doc["@context"] = MUSIC_ENTITY_CONTEXT
                doc["published"] = library.created_at.isoformat()
                return doc
            tracks = (
                (
                    await db.execute(
                        _library_track_query(library.id)
                        .offset((page_num - 1) * _LIBRARY_PAGE_SIZE)
                        .limit(_LIBRARY_PAGE_SIZE)
                    )
                )
                .scalars()
                .all()
            )
            return music_collection_page(
                library_url,
                page_num,
                total,
                await _audio_items(db, tracks, domain, actor_url, library_url, config=config),
                actor_url,
                page_size=_LIBRARY_PAGE_SIZE,
            )

    document = await doc_cache.get_or_render(("lib", library_id, page_num), ttl, _render)
    if document is None:
        if html_only:
            return _spa_response()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _serve_cached_document(request, ("lib", library_id, page_num), ttl, document)


@router.get("/libraries/{library_id}/followers")
async def get_library_followers(
    library_id: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """Serve the follower collection of a public federated library."""
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            exists = (
                await db.execute(
                    select(Library.id).where(
                        Library.id == library_id,
                        Library.visibility == Visibility.PUBLIC.value,
                    )
                )
            ).scalar_one_or_none()
        if exists is None:
            return None
        storage = await asyncio.to_thread(get_federation_storage, config.database)
        library_url = f"https://{config.federation.instance_domain}/libraries/{library_id}"
        return await _followers_collection_dict(storage, library_url)

    document = await doc_cache.get_or_render(("libfol", library_id), ttl, _render)
    return _serve_cached_document(request, ("libfol", library_id), ttl, document)


@router.get("/users/{username}/library")
async def get_user_library_document(
    username: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """Serve a user's implicit library of public tracks as a ``Library``.

    ``GET /users/{u}/library`` returns the collection index; ``?page=N``
    returns a ``CollectionPage`` of ``Audio`` items. This gives every
    Songhive user a followable federated library without a ``Library``
    row — the same shape Funkwhale publishes per-channel. The implicit
    library only contains public tracks, matching the visibility of a
    public ``Library``.
    """
    domain = config.federation.instance_domain
    ttl = _document_ttl(config)
    page = request.query_params.get("page")
    # As above: ``page`` absent → collection index; present (even empty or
    # unparseable) → page 1.
    page_num: Optional[int] = None
    if page is not None:
        try:
            page_num = max(1, int(page))
        except ValueError:
            page_num = 1

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            user = await _federating_user_or_none(db, username)
            if user is None:
                return None
            actor_url = _owner_actor_url(user, config)
            library_url = f"{actor_url}/library"
            count_q = select(func.count(Track.id)).where(
                Track.owner_id == user.id,
                Track.visibility == Visibility.PUBLIC.value,
            )
            total = (await db.execute(count_q)).scalar_one()

            if page_num is None:
                doc = library_to_music_object(
                    library_url,
                    f"{user.display_name or user.username}'s library",
                    actor_url,
                    total,
                    page_size=_LIBRARY_PAGE_SIZE,
                )
                doc["@context"] = MUSIC_ENTITY_CONTEXT
                return doc

            tracks = (
                (
                    await db.execute(
                        select(Track)
                        .where(
                            Track.owner_id == user.id,
                            Track.visibility == Visibility.PUBLIC.value,
                        )
                        .options(
                            selectinload(Track.artist),
                            selectinload(Track.album),
                            selectinload(Track.audio_file),
                            selectinload(Track.genre_associations),
                        )
                        .order_by(Track.created_at.desc())
                        .offset((page_num - 1) * _LIBRARY_PAGE_SIZE)
                        .limit(_LIBRARY_PAGE_SIZE)
                    )
                )
                .scalars()
                .all()
            )
            return music_collection_page(
                library_url,
                page_num,
                total,
                await _audio_items(db, tracks, domain, actor_url, library_url, config=config),
                actor_url,
                page_size=_LIBRARY_PAGE_SIZE,
            )

    document = await doc_cache.get_or_render(("ulib", username, page_num), ttl, _render)
    return _serve_cached_document(request, ("ulib", username, page_num), ttl, document)


@router.get("/users/{username}/library/followers")
async def get_user_library_followers(
    username: str,
    request: Request,
    config: SonghiveConfig = Depends(get_config),
) -> Response:
    """Serve the follower collection of a user's implicit library."""
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            user = await _federating_user_or_none(db, username)
            if user is None:
                return None
            actor_url = _owner_actor_url(user, config)
        storage = await asyncio.to_thread(get_federation_storage, config.database)
        return await _followers_collection_dict(storage, f"{actor_url}/library")

    document = await doc_cache.get_or_render(("ulibfol", username), ttl, _render)
    return _serve_cached_document(request, ("ulibfol", username), ttl, document)
