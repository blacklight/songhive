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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pubby import AttributionMismatch
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...federation.fetch import FetchError, FetchNotFound
from ...models.user import User
from ...services import acl
from ...services import activities as activity_service
from ...services import follows as follows_service
from ...services import remote_content
from ..deps import get_config, get_current_user_optional, get_db
from .activities import (
    ActivityResponse,
    _build_activity_response,
    _viewable_activity_response,
)

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
    visibility: str
    fetched_at: Optional[datetime] = None
    unavailable: bool = False
    url: str  # internal SPA route


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


class RemoteActorActivitiesResponse(BaseModel):
    """Cached activities materialized for a remote actor."""

    activities: List[ActivityResponse]
    total: int


def _remote_policy(config: SonghiveConfig, user: Optional[User]) -> None:
    """Enforce the ``remote_search_access`` policy for remote endpoints."""
    remote_content.check_remote_access(user, remote_content.remote_search_policy(config))


def _actor_response(actor: remote_content.RemoteActorResult, follow_state: Optional[str] = None) -> RemoteActorResponse:
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
    )


async def _viewer_follow_state(db: AsyncSession, user: Optional[User], actor_url: str) -> Optional[str]:
    """Return the caller's follow state on ``actor_url``, if authenticated."""
    if user is None:
        return None
    states = await follows_service.follow_states_for(db, user.id, [actor_url])
    return states.get(actor_url)


def _object_response(row) -> RemoteObjectResponse:
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
        visibility=row.visibility,
        fetched_at=row.fetched_at,
        unavailable=row.unavailable_at is not None,
        url=remote_content.remote_object_page_url(row),
    )


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
            return RemoteLookupResponse(
                kind="actor",
                url=f"/@{actor.handle}",
                actor=_actor_response(actor, follow_state),
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
            object=_object_response(row),
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
    return _actor_response(actor, follow_state)


@router.get("/actors/{handle}/activities", response_model=RemoteActorActivitiesResponse)
async def list_remote_actor_activities(
    handle: str,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
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
        db, actor.actor_url, user=user, limit=limit, offset=offset
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

    activity = await remote_content.get_remote_object_activity(db, row)
    return RemoteObjectDetailResponse(
        object=_object_response(row),
        activity=await _activity_response_or_none(request, db, user, activity),
    )


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
    return _object_response(row)
