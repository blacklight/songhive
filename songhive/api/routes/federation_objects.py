"""
Object, activity and track federation documents.
"""

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...federation import doc_cache
from ...federation.actors import get_federation_storage
from ...models import Activity, Track, User, Visibility
from ...models.base import get_session
from ...services import moderation as moderation_service
from ...services.auth import get_user_by_id
from ...services.federation import ensure_user_actor
from ...services.storage import StorageService
from ..deps import get_current_user_optional, get_db, get_storage_service
from ..semantic_meta import entity_head_tags
from .federation_common import (
    _activity_object_document,
    _document_ttl,
    _earliest_track_post,
    _federating_user_or_none,
    _federation_config,
    _ordered_collection,
    _render_object_document,
    _Rendered,
    _serve_cached_document,
    _track_object_document,
    _visibility_federates,
)
from .profile_pages import _accepts_activitypub, _accepts_html, _get_federating_user, _spa_response

router = APIRouter(include_in_schema=False)


@router.get("/users/{username}/objects/{object_id}")
async def get_object(
    username: str,
    object_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the ActivityPub document for a federated object.

    Public tracks resolve through ``Track.federation_object_id`` to their
    ``Audio`` object; activities resolve through ``Activity.local_object_id``
    or ``Activity.source_id`` and are served as their stored payload (or a
    synthesized ``Note`` when no payload was recorded). Soft-deleted
    activities are served as ``Tombstone`` objects matching the shape
    embedded in ``Delete(Tombstone)`` deliveries. Live activities are only
    served for visibilities that federate — ``private`` and ``local``
    objects never left the instance and answer 404.

    Object URLs double as the objects' own ``url`` (e.g. a ``Note`` share's
    permalink on remote servers), so clients not accepting an
    ActivityStreams media type are redirected to the SPA: a track-resolved
    object goes to the track page, an activity-resolved object to the
    activity's own ``/activities/{id}`` page.

    ActivityPub fetches go through the shared document cache
    (``federation.document_cache_ttl_seconds``): a boosted post makes
    hundreds of remote instances dereference the same object at once, and
    the cache folds that stampede into a single render.
    """
    config = _federation_config(request)
    ttl = _document_ttl(config)

    if _accepts_activitypub(request):
        document = await doc_cache.get_or_render(
            ("obj", username, object_id),
            ttl,
            lambda: _render_object_document(config, username, object_id),
        )
        return _serve_cached_document(request, ("obj", username, object_id), ttl, document)

    user = await _get_federating_user(db, username)
    ensure_user_actor(user, config)
    result = await db.execute(
        select(Track)
        .options(selectinload(Track.artist), selectinload(Track.audio_file))
        .where(
            Track.federation_object_id == object_id,
            Track.owner_id == str(user.id),
            Track.visibility == Visibility.PUBLIC.value,
        )
    )
    track = result.scalar_one_or_none()
    if track is not None:
        # The object's ``url`` is its own dereferenceable id: browsers
        # opening it (e.g. a remote status's "open original" link) land
        # on the track's page.
        return RedirectResponse(url=f"/tracks/{track.id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    activity_result = await db.execute(
        select(Activity).where(
            or_(
                Activity.local_object_id == object_id,
                Activity.source_id == object_id,
            ),
            Activity.owner_user_id == str(user.id),
        )
    )
    activity = activity_result.scalar_one_or_none()
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Shares carry their own object id as ``url``; redirect browsers to
    # the activity's own page instead of serving raw JSON.
    return RedirectResponse(
        url=f"/activities/{activity.id}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.get("/users/{username}/objects/{object_id}/followers")
async def get_object_followers(
    username: str,
    object_id: str,
    request: Request,
):
    """
    Return the followers collection of a federated object.

    Remote actors may ``Follow`` a local object rather than an actor —
    e.g. Friendica sends ``Follow`` on a thread's root item to subscribe
    to the conversation (FEP-efda followable objects). Pubby stores those
    rows scoped to the object's id; this collection dereferences them so
    the ``followers`` field advertised on served object documents stays
    resolvable. Only objects that ``get_object`` would serve answer — the
    collection follows the object's own dereferenceability.
    """
    config = _federation_config(request)
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            user = await _federating_user_or_none(db, username)
            if user is None:
                return None
            ensure_user_actor(user, config)
            object_url = f"{user.actor_url}/objects/{object_id}"
            wanted = {object_url}
            track_id = await db.scalar(
                select(Track.id).where(
                    Track.federation_object_id == object_id,
                    Track.owner_id == str(user.id),
                    Track.visibility == Visibility.PUBLIC.value,
                )
            )
            if track_id is None:
                activity_result = await db.execute(
                    select(Activity).where(
                        or_(
                            Activity.local_object_id == object_id,
                            Activity.source_id == object_id,
                        ),
                        Activity.owner_user_id == str(user.id),
                    )
                )
                activity = activity_result.scalar_one_or_none()
                if (
                    activity is None
                    or activity.deleted_at is not None
                    or not _visibility_federates(activity.visibility)
                ):
                    return None
                if activity.source_id:
                    wanted.add(activity.source_id)

        storage = await asyncio.to_thread(get_federation_storage, config.database)
        followers = await asyncio.to_thread(storage.get_followers_of_targets, wanted)
        return _ordered_collection(
            f"{object_url}/followers",
            [f.actor_id for f in followers],
        )

    document = await doc_cache.get_or_render(("obj", username, object_id, "followers"), ttl, _render)
    return _serve_cached_document(request, ("obj", username, object_id, "followers"), ttl, document)


@router.get("/users/{username}/quote_authorizations/{auth_id:path}")
async def get_quote_authorization(
    username: str,
    auth_id: str,
    request: Request,
):
    """
    Dereference a ``QuoteAuthorization`` issued for this actor (FEP-044f).

    Pubby's inbox processor auto-approves incoming ``QuoteRequest``
    activities and stores the authorization under the addressed actor's URL
    — ``{actor_url}/quote_authorizations/{id}`` — so remote servers can
    fetch it to clear the pending state on their quote posts. Requests for
    the instance actor's authorizations are served by pubby's own adapter
    route (``/ap/actor/quote_authorizations/{id}``).
    """
    config = _federation_config(request)
    ttl = _document_ttl(config)

    async def _render() -> Optional[dict]:
        async with get_session() as db:
            user = await _federating_user_or_none(db, username)
            if user is None:
                return None
            ensure_user_actor(user, config)
            actor_url = user.actor_url
        storage = await asyncio.to_thread(get_federation_storage, config.database)
        return await asyncio.to_thread(
            storage.get_quote_authorization,
            f"{actor_url}/quote_authorizations/{auth_id}",
        )

    document = await doc_cache.get_or_render(("qa", username, auth_id), ttl, _render)
    return _serve_cached_document(request, ("qa", username, auth_id), ttl, document)


@router.get("/activities/{activity_id}")
async def get_activity_page(
    activity_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Dereference the activity permalink URL.

    ``/activities/{id}`` is primarily a Vue SPA route, but it is also the
    URL the frontend offers for copying (``ActivityCard``'s permalink), so
    remote servers fetch it when a user pastes the link into a remote
    search box (e.g. Mastodon's URL lookup). Clients accepting an
    ActivityStreams media type receive the same document the canonical
    ``/users/{username}/objects/{id}`` route serves for local activities;
    remote-sourced activities redirect (303 See Other) to their origin's
    object id, which stays authoritative. Browsers receive the SPA shell
    annotated with a ``Link``/``<link rel="alternate">`` discovery hint
    pointing at the canonical object URL.

    Like the object route, a local activity is only dereferenceable while
    its owner is an active local user. Browser requests always receive the
    SPA shell — including for unknown or non-dereferenceable ids, since the
    SPA renders its own not-found state.
    """
    config = _federation_config(request)
    ttl = _document_ttl(config)

    if _accepts_activitypub(request):
        # The render returns the document for local activities, the redirect
        # target (a URL string) for remote activities, or None for a 404.
        async def _render():
            async with get_session() as db:
                result = await db.execute(
                    select(Activity)
                    .options(selectinload(Activity.in_reply_to_activity))
                    .where(Activity.id == activity_id)
                )
                activity = result.scalar_one_or_none()
                if activity is None:
                    return None
                if activity.source_type != "local":
                    # Remote objects are authoritative on their origin instance.
                    return _Rendered(redirect=activity.source_id)
                owner = await get_user_by_id(db, activity.owner_user_id) if activity.owner_user_id else None
                if (
                    owner is None
                    or not owner.is_active
                    or await moderation_service.user_is_suspended(db, str(owner.id))
                ):
                    return None
                return _Rendered(document=_activity_object_document(activity))

        rendered = await doc_cache.get_or_render(("act", activity_id), ttl, _render)
        if rendered is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if rendered.redirect is not None:
            return RedirectResponse(url=rendered.redirect, status_code=status.HTTP_303_SEE_OTHER)
        return _serve_cached_document(request, ("act", activity_id), ttl, rendered.document)

    result = await db.execute(select(Activity).where(Activity.id == activity_id))
    activity = result.scalar_one_or_none()

    remote = activity is not None and activity.source_type != "local"
    owner = None
    if activity is not None and not remote and activity.owner_user_id:
        owner = await get_user_by_id(db, activity.owner_user_id)
    owner_suspended = owner is not None and await moderation_service.user_is_suspended(db, str(owner.id))
    dereferenceable = remote or (owner is not None and owner.is_active and not owner_suspended)
    federates = remote or (activity is not None and _visibility_federates(activity.visibility))

    alternate_url = activity.source_id if activity is not None and dereferenceable and federates else None
    return _spa_response(alternate_url)


@router.get("/tracks/{track_id}")
async def get_track_page(
    track_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
    storage: StorageService = Depends(get_storage_service),
):
    """
    Dereference the canonical track page URL.

    ``/tracks/{id}`` is primarily a Vue SPA route, but it is also advertised
    as the ``text/html`` ``url`` of every published ``Audio`` object, so
    remote servers fetch it when a user pastes the link into a remote search
    box (e.g. Mastodon's URL lookup). Clients accepting an ActivityStreams
    media type receive the track's ``Audio`` object; browsers receive the SPA
    shell annotated with ``Link``/``<link rel="alternate">`` discovery hints
    pointing at the object.

    The object is only served while the track is published to the fediverse
    (``federation_object_id`` set). When no ``Audio`` exists, remote fetches
    are instead redirected (303 See Other) to the track's earliest surviving
    local share — the object-dereference route then serves that activity's
    ``Note`` document — so the track URL still resolves to a post. Tracks
    with neither a published object nor live shares answer 404, so a remote
    fetch cannot resurrect a retracted post under a different id — unless
    the client also accepts HTML (card crawlers offer both media types),
    in which case it receives the SPA page like any browser request.
    """
    config = _federation_config(request)

    if _accepts_activitypub(request):
        # The render returns the track's document, the earliest surviving
        # share's URL (a string) to redirect to, or None for a 404.
        async def _render():
            async with get_session() as db:
                result = await db.execute(
                    select(Track)
                    .options(selectinload(Track.artist), selectinload(Track.audio_file))
                    .where(
                        Track.id == track_id,
                        Track.visibility == Visibility.PUBLIC.value,
                        Track.federation_object_id.isnot(None),
                    )
                )
                track = result.scalar_one_or_none()
                owner: Any = track.owner if track is not None else None
                if (
                    track is None
                    or track.artist is None
                    or owner is None
                    or not owner.is_active
                    or await moderation_service.user_is_suspended(db, str(owner.id))
                ):
                    document = None
                else:
                    ensure_user_actor(owner, config)
                    document = await _track_object_document(db, track, owner, config)

                if document is not None:
                    return _Rendered(document=document)
                share = await _earliest_track_post(db, track_id)
                if share is not None:
                    return _Rendered(redirect=share.source_id)
                return None

        ttl = _document_ttl(config)
        rendered = await doc_cache.get_or_render(("trackpage", track_id), ttl, _render)
        if rendered is not None and rendered.document is not None:
            return _serve_cached_document(request, ("trackpage", track_id), ttl, rendered.document)
        if rendered is not None and rendered.redirect is not None:
            return RedirectResponse(url=rendered.redirect, status_code=status.HTTP_303_SEE_OTHER)
        # Clients that also accept HTML (e.g. Mastodon's card crawler, which
        # offers activity+json + text/html) still get the SPA page below —
        # only pure ActivityPub dereferences 404 on an unfederated track.
        if not _accepts_html(request):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    result = await db.execute(
        select(Track)
        .options(selectinload(Track.artist), selectinload(Track.audio_file))
        .where(
            Track.id == track_id,
            Track.visibility == Visibility.PUBLIC.value,
            Track.federation_object_id.isnot(None),
        )
    )
    track = result.scalar_one_or_none()
    owner: Any = track.owner if track is not None else None
    if (
        track is None
        or track.artist is None
        or owner is None
        or not owner.is_active
        or await moderation_service.user_is_suspended(db, str(owner.id))
    ):
        track = None
        owner = None
    else:
        ensure_user_actor(owner, config)

    alternate_url = f"{owner.actor_url}/objects/{track.federation_object_id}" if track is not None else None
    og_tags = await entity_head_tags(request, db, user, storage, "track", track_id)
    return _spa_response(alternate_url, extra_tags=og_tags)
