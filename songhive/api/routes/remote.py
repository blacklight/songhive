"""
Explicit remote (federated) content lookup routes.

These endpoints implement bounded remote discovery: they dereference a
single user-supplied handle or URL under the ``remote_search_access``
policy, the federation domain allow/block lists, and the SSRF-guarded
fetcher. Remote actors are cached in pubby's actor cache; remote objects
in ``remote_objects`` with a materialized ``Activity`` mirror. Generic
search never reaches the network — it only surfaces these cached rows.
"""

import logging
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pubby import AttributionMismatch
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...federation.fetch import FetchError, FetchNotFound
from ...models.moderation import USER_MODERATION_BLOCK, USER_MODERATION_MUTE
from ...models.user import User
from ...services import acl
from ...services import activities as activity_service
from ...services import collection as collection_service
from ...services import follows as follows_service
from ...services import moderation as moderation_service
from ...services import notifications as notifications_service
from ...services import remote_content
from .._common import Pagination, get_pagination
from ..deps import get_config, get_current_user, get_current_user_optional, get_db
from ..middleware.rate_limit import rate_limit_account
from .activities import (
    ActivityResponse,
    _build_activity_response,
    _viewable_activity_response,
)
from .users import ActivitySubscriptionState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/remote")

_REMOTE_RESOURCE_KINDS = ("track", "album", "artist", "playlist", "library")


class RemoteActorResponse(BaseModel):
    """A normalized remote ActivityPub actor."""

    handle: str
    username: str
    domain: str
    actor_url: str
    display_name: Optional[str] = None
    summary: Optional[str] = None
    avatar_url: Optional[str] = None
    header_url: Optional[str] = None
    profile_url: Optional[str] = None
    fetched_at: Optional[datetime] = None
    unavailable: bool = False
    url: str  # internal SPA route
    # Viewer-relative follow state (``pending``/``accepted``) when the
    # caller is authenticated and follows this actor.
    follow_state: Optional[str] = None
    # Whether the caller subscribed to this actor's activity notifications
    # (the profile bell).
    activity_subscribed: bool = False
    # Viewer-relative user moderation state.
    muted: bool = False
    blocked: bool = False
    # Instance-level admin moderation state on this actor.
    limited: bool = False
    suspended: bool = False


class RemoteObjectResponse(BaseModel):
    """A cached remote object — content post or resource."""

    id: str
    canonical_url: str
    object_type: str
    resource_type: Optional[str] = None
    domain: str
    actor_url: str
    actor_handle: Optional[str] = None
    name: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    image_url: Optional[str] = None
    audio_url: Optional[str] = None
    # Playback entry point: the internal stream endpoint resolves the
    # object's own media link or a cached rendition at play time (so
    # expiring/provider-resolved links stay fresh). ``None`` when nothing
    # playable is cached — never a stale remote URL.
    stream_url: Optional[str] = None
    # Playback hints for music resources, extracted from the cached
    # document payload (embedded ``track`` on ``Audio`` docs).
    duration: Optional[int] = None
    artist_name: Optional[str] = None
    album_name: Optional[str] = None
    visibility: str
    fetched_at: Optional[datetime] = None
    unavailable: bool = False
    url: str  # internal SPA route
    # Viewer-relative state: collection membership and object follow
    # (``pending``/``accepted``) when the caller is authenticated.
    in_collection: bool = False
    follow_state: Optional[str] = None
    # Containment for federated music resources: ``parent`` is the
    # containing resource (album of a track, artist of an album, library
    # of an upload), ``items`` its cached children.
    parent: Optional["RemoteObjectResponse"] = None
    items: List["RemoteObjectResponse"] = Field(default_factory=list)


class RemoteLookupResponse(BaseModel):
    """Result of an explicit remote lookup (``/remote/lookup``)."""

    kind: Literal["actor", "object", "resource", "local"]
    url: str  # internal SPA route to navigate to
    actor: Optional[RemoteActorResponse] = None
    object: Optional[RemoteObjectResponse] = None
    activity: Optional[ActivityResponse] = None


class RemoteObjectDetailResponse(BaseModel):
    """A cached remote object with its materialized activity, if any."""

    object: RemoteObjectResponse
    activity: Optional[ActivityResponse] = None


class RemoteObjectListResponse(BaseModel):
    """A page of cached remote resources."""

    items: List[RemoteObjectResponse]


class RemoteActorActivitiesResponse(BaseModel):
    """Cached activities materialized for a remote actor."""

    activities: List[ActivityResponse]
    total: int


class RemoteObjectFollowResponse(BaseModel):
    """Result of following a remote object/resource."""

    follow_state: Optional[str] = None


def _remote_policy(config: SonghiveConfig, user: Optional[User]) -> None:
    """Enforce the ``remote_search_access`` policy for remote endpoints."""
    remote_content.check_remote_access(user, remote_content.remote_search_policy(config))


def _actor_response(
    actor: remote_content.RemoteActorResult,
    follow_state: Optional[str] = None,
    activity_subscribed: bool = False,
    moderation: Optional[dict] = None,
) -> RemoteActorResponse:
    flags = moderation or {}
    return RemoteActorResponse(
        handle=actor.handle,
        username=actor.username,
        domain=actor.domain,
        actor_url=actor.actor_url,
        display_name=actor.display_name,
        summary=actor.summary,
        avatar_url=actor.avatar_url,
        header_url=actor.header_url,
        profile_url=actor.profile_url,
        fetched_at=actor.fetched_at,
        unavailable=actor.unavailable,
        url=f"/@{actor.handle}",
        follow_state=follow_state,
        activity_subscribed=activity_subscribed,
        muted=flags.get("muted", False),
        blocked=flags.get("blocked", False),
        limited=flags.get("limited", False),
        suspended=flags.get("suspended", False),
    )


async def _viewer_follow_state(db: AsyncSession, user: Optional[User], actor_url: str) -> Optional[str]:
    """Return the caller's follow state on ``actor_url``, if authenticated."""
    if user is None:
        return None
    states = await follows_service.follow_states_for(db, user.id, [actor_url])
    return states.get(actor_url)


async def _viewer_activity_subscribed(db: AsyncSession, user: Optional[User], actor_url: str) -> bool:
    """Return whether the caller subscribed to ``actor_url``'s activity."""
    if user is None:
        return False
    local_id = await db.scalar(select(User.id).where(User.actor_url == actor_url))
    if local_id is not None:
        return await notifications_service.is_subscribed_to_user_activity(db, str(user.id), str(local_id))
    return await notifications_service.is_subscribed_to_actor_activity(db, str(user.id), actor_url)


async def _actor_moderation_flags(db: AsyncSession, user: Optional[User], actor_url: str) -> dict:
    """Return the viewer-relative and admin moderation flags for ``actor_url``."""
    flags = {"muted": False, "blocked": False, "limited": False, "suspended": False}
    row = await moderation_service.get_admin_user_moderation(db, actor_url)
    if row is not None:
        flags["limited"] = row.action == moderation_service.ADMIN_ACTION_LIMIT
        flags["suspended"] = row.action == moderation_service.ADMIN_ACTION_SUSPEND
    if user is not None:
        kinds = await moderation_service.user_moderation_map(db, user.id, [actor_url])
        kset = kinds.get(actor_url) or set()
        flags["muted"] = USER_MODERATION_MUTE in kset
        flags["blocked"] = USER_MODERATION_BLOCK in kset
    return flags


def _object_response(
    row,
    *,
    in_collection: bool = False,
    follow_state: Optional[str] = None,
    parent: Optional[RemoteObjectResponse] = None,
    items: Optional[List[RemoteObjectResponse]] = None,
    playable: bool = False,
) -> RemoteObjectResponse:
    actor_handle = remote_content.actor_handle_from_url(row.actor_url) if row.actor_url else None
    return RemoteObjectResponse(
        id=str(row.id),
        canonical_url=row.canonical_url,
        object_type=row.object_type,
        resource_type=row.resource_type,
        domain=row.domain,
        actor_url=row.actor_url,
        actor_handle=actor_handle,
        name=row.name,
        summary=row.summary,
        content=row.content,
        image_url=row.image_url,
        audio_url=row.audio_url,
        stream_url=remote_content.remote_object_stream_url(row) if playable else None,
        **remote_content.remote_object_music_fields(row),
        visibility=row.visibility,
        fetched_at=row.fetched_at,
        unavailable=row.unavailable_at is not None,
        url=remote_content.remote_object_page_url(row),
        in_collection=in_collection,
        follow_state=follow_state,
        parent=parent,
        items=items or [],
    )


async def _playable_object_ids(db: AsyncSession, rows) -> set:
    """
    Return the ids of ``rows`` currently playable through the stream endpoint.

    A row is playable when it carries its own ``audio_url`` or is a
    media-entity row (``Track``/``Audio``/``Video``, or a ``track``
    resource) with a cached rendition — resolved in one batched query via
    ``media_of_url`` (with a payload fallback for rows cached before the
    column existed).
    """
    ids = {str(row.id) for row in rows if row.audio_url and row.unavailable_at is None}
    candidates = [
        row.canonical_url
        for row in rows
        if not row.audio_url
        and row.unavailable_at is None
        and (row.object_type in ("Audio", "Track", "Video") or row.resource_type == "track")
    ]
    if candidates:
        resolved = await remote_content.rendition_audio_map(db, candidates)
        ids |= {str(row.id) for row in rows if row.canonical_url in resolved}
    return ids


async def _object_viewer_state(
    db: AsyncSession,
    user: Optional[User],
    row,
) -> dict:
    """Return the caller's collection/follow state on a remote object."""
    if user is None:
        return {"in_collection": False, "follow_state": None}
    saved = await collection_service.saved_item_ids(db, user, "remote", [str(row.id)])
    states = await follows_service.follow_states_for(db, user.id, [row.canonical_url])
    return {
        "in_collection": str(row.id) in saved,
        "follow_state": states.get(row.canonical_url),
    }


async def _activity_response_or_none(
    request: Request,
    db: AsyncSession,
    user: Optional[User],
    activity,
) -> Optional[ActivityResponse]:
    """Serialize ``activity`` when viewable, else ``None``."""
    if activity is None:
        return None
    try:
        return await _viewable_activity_response(activity, request, db, user)
    except HTTPException:
        return None


def _fetch_error(exc: FetchError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/lookup", response_model=RemoteLookupResponse)
async def remote_lookup(
    request: Request,
    input: str = Query(..., max_length=2048, description="Handle, actor URL, or remote object URL"),
    refresh: bool = Query(False, description="Bypass caches and re-fetch"),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Resolve an explicit remote lookup to an internal URL.

    ``@user@domain`` handles and actor-shaped URLs resolve through
    WebFinger + the actor cache; object/activity/resource URLs are
    dereferenced (one bounded fetch) into the ``remote_objects`` cache with
    a materialized ``Activity``. Local-domain inputs need no fetch — they
    map straight to their SPA route. Returns the internal SPA route to
    navigate to.
    """
    _remote_policy(config, user)
    target = remote_content.parse_remote_target(input, config)

    if target.kind in (
        remote_content.RemoteTargetKind.HANDLE,
        remote_content.RemoteTargetKind.ACTOR_URL,
    ):
        try:
            actor = await remote_content.lookup_remote_actor(db, config, input, refresh=refresh)
        except FetchError as exc:
            # An actor-shaped URL that doesn't resolve to an actor document
            # may still be a content object — fall back to dereferencing.
            if target.kind != remote_content.RemoteTargetKind.ACTOR_URL or exc.status_code != 422:
                raise _fetch_error(exc) from exc
        else:
            follow_state = await _viewer_follow_state(db, user, actor.actor_url)
            activity_subscribed = await _viewer_activity_subscribed(db, user, actor.actor_url)
            moderation = await _actor_moderation_flags(db, user, actor.actor_url)
            return RemoteLookupResponse(
                kind="actor",
                url=f"/@{actor.handle}",
                actor=_actor_response(actor, follow_state, activity_subscribed, moderation),
            )

    if target.kind in (
        remote_content.RemoteTargetKind.ACTOR_URL,
        remote_content.RemoteTargetKind.OBJECT_URL,
        remote_content.RemoteTargetKind.SONGHIVE_ACTIVITY_URL,
        remote_content.RemoteTargetKind.SONGHIVE_RESOURCE_URL,
    ):
        try:
            result = await remote_content.dereference_remote_object(db, config, target.url or input, refresh=refresh)
        except AttributionMismatch as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Remote object attribution could not be verified: {exc}",
            ) from exc
        except FetchError as exc:
            raise _fetch_error(exc) from exc
        await db.commit()
        row = result.remote_object
        kind: str = "resource" if row.resource_type else "object"
        activity = await _activity_response_or_none(request, db, user, result.activity)
        return RemoteLookupResponse(
            kind=kind,  # type: ignore[arg-type]
            url=remote_content.remote_object_page_url(row),
            object=_object_response(row, playable=str(row.id) in await _playable_object_ids(db, [row])),
            activity=activity,
        )

    if target.kind == remote_content.RemoteTargetKind.LOCAL:
        # A local URL/handle needs no dereference — map it straight to the
        # SPA route (object permalinks resolve through the local tables).
        return RemoteLookupResponse(
            kind="local",
            url=await remote_content.resolve_local_target(db, target),
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported remote lookup input",
    )


@router.get("/actors/{handle}", response_model=RemoteActorResponse)
async def get_remote_actor(
    handle: str,
    refresh: bool = Query(False, description="Bypass the actor cache and re-fetch"),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Resolve ``user@domain`` to a remote actor (fetch-on-miss).

    Powers the ``/@user@domain`` SPA deep link: navigating to a remote
    profile is itself the explicit lookup, so a cache miss dereferences the
    actor under the policy/domain/SSRF guards.
    """
    _remote_policy(config, user)
    try:
        actor = await remote_content.lookup_remote_actor(db, config, handle, refresh=refresh)
    except FetchError as exc:
        raise _fetch_error(exc) from exc
    follow_state = await _viewer_follow_state(db, user, actor.actor_url)
    activity_subscribed = await _viewer_activity_subscribed(db, user, actor.actor_url)
    moderation = await _actor_moderation_flags(db, user, actor.actor_url)
    return _actor_response(actor, follow_state, activity_subscribed, moderation)


@router.post(
    "/actors/{handle}/activity-subscription",
    response_model=ActivitySubscriptionState,
    dependencies=[Depends(rate_limit_account)],
)
async def subscribe_to_remote_actor_activity(
    handle: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """Subscribe to a remote actor's activity notifications (the profile bell).

    Resolves ``user@domain`` fetch-on-miss like the profile endpoint, then
    records an ``ActivitySubscription`` keyed on the actor URL. Subscribing
    also follows the actor on a best-effort basis: remote activities only
    reach the instance while a local user follows the actor, and a failed
    follow does not fail the subscription. Actor URLs belonging to a local
    user subscribe through the local target instead. Idempotent.
    """
    _remote_policy(config, user)
    try:
        actor = await remote_content.lookup_remote_actor(db, config, handle)
    except FetchError as exc:
        raise _fetch_error(exc) from exc

    local_id = await db.scalar(select(User.id).where(User.actor_url == actor.actor_url))
    if (local_id is not None and str(local_id) == str(user.id)) or (
        local_id is None and user.actor_url == actor.actor_url
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot subscribe to your own activity",
        )
    # The follow runs first: ``_follow_local`` writes through pubby's sync
    # storage, which must happen before the session accumulates writes.
    try:
        await follows_service.follow_user(db, config, user, actor.actor_url)
    except Exception as exc:
        logger.info("Follow alongside activity subscription on %s failed: %s", actor.actor_url, exc)
    if local_id is not None:
        await notifications_service.subscribe_to_user_activity(db, str(user.id), str(local_id))
    else:
        await notifications_service.subscribe_to_actor_activity(db, str(user.id), actor.actor_url)
    await db.commit()
    states = await follows_service.follow_states_for(db, user.id, [actor.actor_url])
    return ActivitySubscriptionState(activity_subscribed=True, follow_state=states.get(actor.actor_url))


@router.delete(
    "/actors/{handle}/activity-subscription",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def unsubscribe_from_remote_actor_activity(
    handle: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """Remove the caller's activity subscription on a remote actor.

    The follow is intentionally kept — it may predate the bell — so
    unfollowing stays an explicit action on the follow button.
    """
    _remote_policy(config, user)
    try:
        actor = await remote_content.lookup_remote_actor(db, config, handle)
    except FetchError as exc:
        raise _fetch_error(exc) from exc

    local_id = await db.scalar(select(User.id).where(User.actor_url == actor.actor_url))
    if local_id is not None:
        await notifications_service.unsubscribe_from_user_activity(db, str(user.id), str(local_id))
    else:
        await notifications_service.unsubscribe_from_actor_activity(db, str(user.id), actor.actor_url)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/actors/{handle}/activities", response_model=RemoteActorActivitiesResponse)
async def list_remote_actor_activities(
    handle: str,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    reveal: bool = Query(False, description="Reveal this actor's followers-only-gated activities"),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
    config: SonghiveConfig = Depends(get_config),
):
    """
    List already-cached activities for a remote actor.

    Strictly reads the local cache — remote outboxes are never fetched and
    timelines are never crawled.
    """
    _remote_policy(config, user)
    actor = await remote_content.get_cached_remote_actor(db, config, handle)
    if actor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote actor not cached")

    activities, total = await remote_content.list_cached_actor_activities(
        db, actor.actor_url, user=user, limit=limit, offset=offset, reveal=reveal
    )
    profiles = await activity_service.resolve_source_actor_profiles(db, activities, config)
    summaries = await activity_service.resolve_interaction_summaries(db, activities, user, config)
    return RemoteActorActivitiesResponse(
        activities=[
            _build_activity_response(
                activity,
                profiles.get(str(activity.id), activity_service.ActorProfile()),
                summaries.get(str(activity.id)),
            )
            for activity in activities
        ],
        total=total,
    )


@router.get("/objects", response_model=RemoteObjectListResponse)
async def list_remote_objects(
    response: Response,
    resource_type: Optional[str] = Query(None, description="Filter by resource kind"),
    collection: bool = Query(False, description="Restrict to the caller's collected remote objects"),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
    config: SonghiveConfig = Depends(get_config),
    pagination: Pagination = Depends(get_pagination),
):
    """
    Browse cached remote resources — never reaches the network.

    ``collection=true`` restricts the listing to ``remote_objects`` rows
    the caller saved through ``item_type="remote"`` collection entries;
    anonymous callers have no collection and get an empty page.
    """
    _remote_policy(config, user)
    if resource_type is not None and resource_type not in _REMOTE_RESOURCE_KINDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid resource type")
    rows, total = await remote_content.list_cached_remote_objects(
        db,
        config,
        resource_type=resource_type,
        user=user,
        collection_only=collection,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    pagination.set_total(response, total)
    saved = (
        await collection_service.saved_item_ids(db, user, "remote", [str(row.id) for row in rows])
        if user is not None
        else set()
    )
    playable = await _playable_object_ids(db, rows)
    return RemoteObjectListResponse(
        items=[
            _object_response(row, in_collection=str(row.id) in saved, playable=str(row.id) in playable) for row in rows
        ]
    )


@router.get("/objects/{object_id}", response_model=RemoteObjectDetailResponse)
async def get_remote_object(
    object_id: str,
    request: Request,
    refresh: bool = Query(False, description="Re-fetch the canonical URL to confirm remote state"),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Return a cached remote object and its materialized activity.

    ``object_id`` is the ``remote_objects`` row id — the same id the
    ``/activities/@user@domain/{id}`` SPA route carries. With ``refresh``
    the canonical URL is re-dereferenced under the usual guards to confirm
    the object still exists remotely.
    """
    _remote_policy(config, user)
    row = await remote_content.get_cached_remote_object(db, object_id)
    if row is None or not remote_content.remote_domain_allowed(row.domain, config):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote object not found")
    if not await acl.can_access(db, user, "remote", str(row.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if refresh:
        try:
            await remote_content.dereference_remote_object(db, config, row.canonical_url, refresh=True)
        except FetchNotFound:
            pass  # tombstoned — the cached row below reflects it
        except FetchError as exc:
            raise _fetch_error(exc) from exc
        await db.commit()
        await db.refresh(row)

    viewer = await _object_viewer_state(db, user, row)
    parent_row = await remote_content.get_remote_object_parent(db, row)
    children = await remote_content.get_remote_object_children(db, row)
    activity = await remote_content.get_remote_object_activity(db, row)
    related = [row, *children, *([parent_row] if parent_row is not None else [])]
    playable = await _playable_object_ids(db, related)
    return RemoteObjectDetailResponse(
        object=_object_response(
            row,
            in_collection=viewer["in_collection"],
            follow_state=viewer["follow_state"],
            parent=(
                _object_response(parent_row, playable=str(parent_row.id) in playable)
                if parent_row is not None
                else None
            ),
            items=[_object_response(child, playable=str(child.id) in playable) for child in children],
            playable=str(row.id) in playable,
        ),
        activity=await _activity_response_or_none(request, db, user, activity),
    )


@router.get("/objects/{object_id}/stream")
async def stream_remote_object(
    object_id: str,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Redirect to the object's playable media URL.

    Resolution happens at play time, never at cache time: the object's own
    ``audio_url`` when present, else a cached rendition embedding the
    entity (``media_of_url``). Providers whose links expire or need
    resolution (e.g. future YouTube/Spotify adapters) plug into
    ``resolve_media_url`` — the client always hits this endpoint.
    """
    _remote_policy(config, user)
    row = await remote_content.get_cached_remote_object(db, object_id)
    if row is None or not remote_content.remote_domain_allowed(row.domain, config):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote object not found")
    if not await acl.can_access(db, user, "remote", str(row.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    media_url = await remote_content.resolve_media_url(db, row)
    if media_url is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No playable media for this remote object")
    return RedirectResponse(media_url)


@router.post(
    "/objects/{object_id}/follow",
    response_model=RemoteObjectFollowResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def follow_remote_object(
    object_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """Follow a cached remote resource (e.g. a federated library).

    Delivers a signed object-scoped ``Follow`` to the resource's
    controlling actor and records the pending relationship; the remote
    ``Accept``/``Reject`` folds back into the row's ``state``. Following a
    remote library subscribes the instance to new items published into it.
    """
    _remote_policy(config, user)
    row = await remote_content.get_cached_remote_object(db, object_id)
    if row is None or not remote_content.remote_domain_allowed(row.domain, config):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote object not found")
    try:
        follow = await follows_service.follow_remote_object(db, config, user, row.canonical_url)
    except FetchError as exc:
        raise _fetch_error(exc) from exc
    await db.commit()
    return RemoteObjectFollowResponse(follow_state=follow.state)


@router.delete(
    "/objects/{object_id}/follow",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def unfollow_remote_object(
    object_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """Unfollow a remote resource — delivers ``Undo(Follow)`` remotely."""
    _remote_policy(config, user)
    row = await remote_content.get_cached_remote_object(db, object_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote object not found")
    await follows_service.unfollow_user(db, config, user, row.canonical_url)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{kind}/{object_id}", response_model=RemoteObjectResponse)
async def get_remote_resource(
    kind: str,
    object_id: str,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Return a cached remote resource (track/album/artist/playlist/library).

    ``{object_id}`` is the ``remote_objects`` row id; the ``kind`` path
    segment must match the normalized ``resource_type``. Remote resources
    are never copied into local resource tables.
    """
    _remote_policy(config, user)
    if kind not in _REMOTE_RESOURCE_KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown remote resource kind")
    row = await remote_content.get_cached_remote_object(db, object_id)
    if row is None or row.resource_type != kind:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote resource not found")
    if not remote_content.remote_domain_allowed(row.domain, config):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote resource not found")
    if not await acl.can_access(db, user, "remote", str(row.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    viewer = await _object_viewer_state(db, user, row)
    parent_row = await remote_content.get_remote_object_parent(db, row)
    children = await remote_content.get_remote_object_children(db, row)
    related = [row, *children, *([parent_row] if parent_row is not None else [])]
    playable = await _playable_object_ids(db, related)
    return _object_response(
        row,
        in_collection=viewer["in_collection"],
        follow_state=viewer["follow_state"],
        parent=(
            _object_response(parent_row, playable=str(parent_row.id) in playable) if parent_row is not None else None
        ),
        items=[_object_response(child, playable=str(child.id) in playable) for child in children],
        playable=str(row.id) in playable,
    )
