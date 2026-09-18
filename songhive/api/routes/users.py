"""
User profile routes.
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...federation.actors import get_federation_storage, sync_user_actor
from ...models.audit_log import AuditTargetType
from ...models.follow import FOLLOW_STATE_ACCEPTED, Follow
from ...models.user import FollowersApproval, ProfileVisibility, User, UserRole
from ...models.user_link import UserLink
from ...services import activities as activity_service
from ...services import audit
from ...services import federation as federation_service
from ...services import follows as follows_service
from ...services import notifications as notifications_service
from ...services.auth import get_user_by_username, list_public_users
from ...services.federation import unpublish_track_activity
from ...services.storage import StorageService
from ...services.tags import get_items_for_tag, list_tags, validate_tag_name
from ...users import manager as user_manager
from ...users.manager import DELETE_ACCOUNT_CONFIRMATION, PasswordChangeError, change_user_password, update_profile
from ...users.tokens import revoke_all_user_refresh_tokens
from .._common import Pagination, client_ip, get_pagination
from .._sorting import SortParams, get_sort
from ..deps import get_config, get_current_user, get_current_user_optional, get_db, get_redis, get_storage_service
from ..middleware.rate_limit import rate_limit_account
from .activities import ActivityListResponse, _build_activity_response
from .tags import TaggedItemResponse, TagSummaryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users")


class UserLinkBase(BaseModel):
    """Shared link schema with validation."""

    name: str = Field(..., min_length=1, max_length=64)
    url: str = Field(..., min_length=1, max_length=512)

    @field_validator("name", "url", mode="before")
    @classmethod
    def _strip_whitespace(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("url")
    @classmethod
    def _validate_url_scheme(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("Link URL must start with http:// or https://")
        return value


class UserLinkInput(UserLinkBase):
    """Link payload used in profile update requests."""


class UserLinkOutput(UserLinkBase):
    """Link item as it appears in profile responses."""

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    """Authenticated user profile response, including the internal user id."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    email_verified: Optional[bool] = None
    role: Optional[UserRole] = None
    status_content_type: str = "text/markdown"
    preview_cards_enabled: bool = True
    profile_visibility: ProfileVisibility = ProfileVisibility.PUBLIC
    followers_approval: FollowersApproval = FollowersApproval.ACCEPT
    links: List[UserLinkOutput] = Field(default_factory=list)


class PublicUserResponse(BaseModel):
    """Public user profile response (internal id excluded)."""

    model_config = ConfigDict(from_attributes=True)

    username: str
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[UserRole] = None
    created_at: datetime
    links: List[UserLinkOutput] = Field(default_factory=list)
    followers_count: int = 0
    follows_count: int = 0
    # Viewer-relative follow state on this user (``pending``/``accepted``),
    # only set for authenticated viewers other than the user themself.
    follow_state: Optional[str] = None
    # Whether the authenticated viewer receives ``activity`` notifications
    # for this user (the profile bell); always ``false`` for anonymous
    # viewers and for the user themself.
    activity_subscribed: bool = False


class FollowerResponse(BaseModel):
    """A follower entry on a user's public followers page."""

    actor_url: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    followed_at: Optional[datetime] = None


class FollowRequestResponse(BaseModel):
    """A pending follow request, visible to the followed user only."""

    actor_url: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    requested_at: Optional[datetime] = None


class FollowingResponse(BaseModel):
    """An actor followed by a user, local or remote."""

    actor_url: str
    handle: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    # ``pending`` while the target has not approved the follow yet.
    state: str
    followed_at: Optional[datetime] = None
    # Set when the followed actor is a user on this instance.
    local_username: Optional[str] = None


class FollowTargetRequest(BaseModel):
    """Payload identifying the actor to follow or unfollow."""

    # A local username, ``@user@domain`` handle, or actor/profile URL.
    actor_url: str = Field(..., min_length=1, max_length=1024)


class ActivitySubscriptionState(BaseModel):
    """The authenticated user's activity-subscription state on a profile."""

    activity_subscribed: bool
    # The viewer's resulting follow state on the target (``pending`` /
    # ``accepted``) — subscribing also follows the actor so their
    # activities are delivered. ``None`` when no follow exists.
    follow_state: Optional[str] = None


def _follower_response(follower) -> FollowerResponse:
    """Map a stored pubby ``Follower`` to its public representation."""
    actor_data = follower.actor_data or {}
    return FollowerResponse(
        actor_url=follower.actor_id,
        display_name=activity_service._actor_doc_display_name(actor_data),
        avatar_url=activity_service._actor_doc_avatar_url(actor_data),
        followed_at=follower.followed_at,
    )


def _follow_request_response(request) -> FollowRequestResponse:
    """Map a stored pubby ``FollowRequest`` to its representation."""
    actor_data = request.actor_data or {}
    return FollowRequestResponse(
        actor_url=request.actor_id,
        display_name=activity_service._actor_doc_display_name(actor_data),
        avatar_url=activity_service._actor_doc_avatar_url(actor_data),
        requested_at=request.requested_at,
    )


def _following_response(row: Follow, local_users: dict) -> FollowingResponse:
    """Map a stored ``Follow`` row to its representation."""
    actor_data = row.actor_data or {}
    local = local_users.get(row.target_user_id) if row.target_user_id else None
    return FollowingResponse(
        actor_url=row.target_actor_url,
        handle=follows_service.follow_row_handle(row),
        display_name=(local.display_name if local else None) or activity_service._actor_doc_display_name(actor_data),
        avatar_url=(local.avatar_url if local else None) or activity_service._actor_doc_avatar_url(actor_data),
        state=row.state,
        followed_at=row.created_at,
        local_username=local.username if local else None,
    )


async def _following_responses(db: AsyncSession, rows: list) -> List[FollowingResponse]:
    """Serialize follow rows, bulk-loading their local target users."""
    ids = [row.target_user_id for row in rows if row.target_user_id]
    local_users = {}
    if ids:
        result = await db.execute(select(User).where(User.id.in_(ids)))
        local_users = {user.id: user for user in result.scalars()}
    return [_following_response(row, local_users) for row in rows]


async def _load_followers(user: User, config: SonghiveConfig) -> list:
    """
    Return the user's stored followers (newest first).

    Followers live in pubby's ``federation_followers`` storage — populated by
    incoming ``Follow`` activities and dropped by ``Undo(Follow)`` — so the
    list is empty when federation is disabled or the user has no actor URL.
    Blocking storage calls run in a thread.
    """
    if not config.federation.enabled or not config.federation.instance_domain or not user.actor_url:
        return []
    storage = await asyncio.to_thread(get_federation_storage, config.database.url)
    try:
        return await asyncio.to_thread(federation_service.get_actor_followers, storage, user.actor_url)
    except Exception:
        # Broad catch is intentional: the public profile must not fail when
        # the federation storage is unavailable or inconsistent.
        logger.warning("Failed to load followers for %s", user.username, exc_info=True)
        return []


def _with_followers_count(
    item: PublicUserResponse, counts: dict[str, int], actor_url: Optional[str]
) -> PublicUserResponse:
    """
    Set ``followers_count`` on a serialized user from a count map.

    Followers without a target actor (legacy unassigned rows) count toward
    every actor, mirroring pubby's ``get_followers`` semantics.
    """
    unassigned = counts.get("", 0)
    item.followers_count = counts.get(actor_url, 0) + unassigned if actor_url else unassigned
    return item


async def _followers_count_map(config: SonghiveConfig) -> dict[str, int]:
    """Return per-actor follower counts (empty when federation is off)."""
    if not config.federation.enabled or not config.federation.instance_domain:
        return {}
    storage = await asyncio.to_thread(get_federation_storage, config.database.url)
    try:
        return await asyncio.to_thread(federation_service.count_followers_by_actor, storage)
    except Exception:
        # Broad catch is intentional: see _load_followers.
        logger.warning("Failed to load follower counts", exc_info=True)
        return {}


class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=128)
    bio: Optional[str] = None
    avatar_url: Optional[str] = Field(None, max_length=512)
    status_content_type: Optional[str] = None
    preview_cards_enabled: Optional[bool] = None
    profile_visibility: Optional[ProfileVisibility] = None
    followers_approval: Optional[FollowersApproval] = None
    links: Optional[List[UserLinkInput]] = None

    @field_validator("status_content_type")
    @classmethod
    def _validate_status_content_type(cls, value: Optional[str]) -> Optional[str]:
        from ...services.mentions import STATUS_CONTENT_TYPES

        if value is not None and value not in STATUS_CONTENT_TYPES:
            raise ValueError(f"Invalid status_content_type: {value}")
        return value

    @field_validator("avatar_url", mode="before")
    @classmethod
    def _strip_avatar_url(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("avatar_url")
    @classmethod
    def _validate_avatar_url_scheme(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not value.startswith(("https://", "http://")):
            raise ValueError("Avatar URL must start with http:// or https://")
        return value


class FollowRequestDecision(BaseModel):
    """Payload for accepting or rejecting a pending follow request."""

    actor_url: str = Field(..., min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    """Request body for changing the authenticated user's password."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


class ChangePasswordResponse(BaseModel):
    """Response returned after a successful password change."""

    success: bool = True


class DeleteAccountRequest(BaseModel):
    """Request body for deleting the authenticated user's account."""

    confirmation: str = Field(..., min_length=1)
    recursive: bool = False


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(current_user: User = Depends(get_current_user)):
    """Get the current authenticated user's profile."""
    return UserResponse.model_validate(current_user)


@router.patch(
    "/me",
    response_model=UserResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def update_current_user_profile(
    update: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Update the current authenticated user's profile."""
    updates = update.model_dump(exclude_unset=True)
    if "links" in updates and updates["links"] is not None:
        updates["links"] = [UserLink(name=link["name"], url=link["url"]) for link in updates["links"]]

    await update_profile(db, current_user, updates)
    await db.commit()  # Persist profile changes before optional actor sync

    if config.federation.enabled:
        await sync_user_actor(current_user, config)

    return UserResponse.model_validate(current_user)


async def _load_follow_requests(user: User, config: SonghiveConfig) -> list:
    """
    Return the user's pending follow requests (newest first).

    Requests live in pubby's ``federation_follow_requests`` storage —
    populated by incoming ``Follow`` activities while the user's approval
    policy is ``manual`` — so the list is empty when federation is
    disabled or the user has no actor URL. Blocking storage calls run in
    a thread.
    """
    if not config.federation.enabled or not config.federation.instance_domain or not user.actor_url:
        return []
    storage = await asyncio.to_thread(get_federation_storage, config.database.url)
    try:
        return await asyncio.to_thread(federation_service.get_actor_follow_requests, storage, user.actor_url)
    except Exception:
        # Broad catch is intentional: see _load_followers.
        logger.warning("Failed to load follow requests for %s", user.username, exc_info=True)
        return []


@router.get("/me/follow-requests", response_model=List[FollowRequestResponse])
async def list_my_follow_requests(
    response: Response,
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """List the current user's pending follow requests, newest first."""
    requests = await _load_follow_requests(current_user, config)
    pagination.set_total(response, len(requests))
    page = requests[pagination.offset : pagination.offset + pagination.limit]
    return [_follow_request_response(r) for r in page]


async def _decide_follow_request(
    body: FollowRequestDecision,
    current_user: User,
    db: AsyncSession,
    config: SonghiveConfig,
    *,
    accept: bool,
) -> None:
    """Resolve a pending follow request and update its notification."""
    from ...federation.notifications import resolve_follow_request_notification

    if not config.federation.enabled or not config.federation.instance_domain or not current_user.actor_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    storage = await asyncio.to_thread(get_federation_storage, config.database.url)
    request = await asyncio.to_thread(
        federation_service.get_follow_request, storage, current_user.actor_url, body.actor_url
    )
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    try:
        await asyncio.to_thread(
            federation_service.resolve_follow_request,
            storage,
            request,
            current_user,
            accept=accept,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # When the requester is a local user their stored ``Follow`` row tracks
    # the decision directly — the enqueued ``Accept``/``Reject`` only
    # reaches remote requesters' instances.
    await follows_service.apply_local_follow_decision(
        db,
        actor_url=body.actor_url,
        target_actor_url=current_user.actor_url or "",
        accept=accept,
    )

    await resolve_follow_request_notification(
        db,
        recipient=current_user,
        actor_url=body.actor_url,
        status="accepted" if accept else "rejected",
    )
    await db.commit()


@router.post("/me/follow-requests/accept", status_code=status.HTTP_204_NO_CONTENT)
async def accept_my_follow_request(
    body: FollowRequestDecision,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Approve a pending follow request and send the ``Accept``."""
    await _decide_follow_request(body, current_user, db, config, accept=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/follow-requests/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_my_follow_request(
    body: FollowRequestDecision,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Decline a pending follow request and send the ``Reject``."""
    await _decide_follow_request(body, current_user, db, config, accept=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/follows", response_model=List[FollowingResponse])
async def list_my_follows(
    response: Response,
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List actors the current user follows, newest first — all states."""
    rows = await follows_service.list_user_follows(db, current_user.id)
    pagination.set_total(response, len(rows))
    page = rows[pagination.offset : pagination.offset + pagination.limit]
    return await _following_responses(db, page)


@router.post(
    "/me/follows",
    response_model=FollowingResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def follow_actor(
    body: FollowTargetRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Follow a local or remote actor.

    The target may be a local username, a ``@user@domain`` handle or an
    actor/profile URL. Remote follows stay ``pending`` until the remote
    instance answers the delivered ``Follow`` activity; local follows
    resolve immediately per the target's approval policy.
    """
    row = await follows_service.follow_user(db, config, current_user, body.actor_url)
    await db.commit()
    rows = await _following_responses(db, [row])
    return rows[0]


@router.delete(
    "/me/follows",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def unfollow_actor(
    body: FollowTargetRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Unfollow an actor — delivers ``Undo(Follow)`` for remote targets."""
    target_url = await follows_service.resolve_unfollow_target(db, config, body.actor_url)
    await follows_service.unfollow_user(db, config, current_user, target_url)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/me/password",
    response_model=ChangePasswordResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def change_my_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """Change the authenticated user's password."""
    try:
        await change_user_password(
            db,
            current_user,
            body.current_password,
            body.new_password,
        )
    except PasswordChangeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    config = get_config(request)
    access_token_ttl = (config.auth.access_token_expiry_minutes or 0) * 60
    await revoke_all_user_refresh_tokens(redis, current_user.id, access_token_ttl)
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="user.change_password",
        target_type=AuditTargetType.USER,
        target_id=current_user.id,
        details={},
        ip_address=client_ip(request),
    )
    await db.commit()

    return ChangePasswordResponse()


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_current_user(
    request: Request,
    body: DeleteAccountRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    redis: Redis = Depends(get_redis),
):
    """Delete the authenticated user's account."""
    if body.confirmation.strip() != DELETE_ACCOUNT_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation text does not match",
        )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="user.delete",
        target_type=AuditTargetType.USER,
        target_id=str(current_user.id),
        details={
            "recursive": body.recursive,
            "username": current_user.username,
        },
        ip_address=client_ip(request),
    )

    try:
        unpublish = await user_manager.delete_user(
            db,
            str(current_user.id),
            recursive=body.recursive,
            storage=storage,
        )
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    config = get_config(request)
    access_token_ttl = (config.auth.access_token_expiry_minutes or 0) * 60
    await revoke_all_user_refresh_tokens(redis, str(current_user.id), access_token_ttl)

    if unpublish:
        for info in unpublish:
            if info.artist is not None and info.owner is not None:
                background_tasks.add_task(
                    unpublish_track_activity,
                    info.track,
                    info.artist,
                    info.owner,
                    config,
                    info.federation_object_id,
                )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("", response_model=List[PublicUserResponse])
async def list_public_users_route(
    response: Response,
    q: Optional[str] = Query(None, description="Search users by username or display name"),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"username", "created_at"}, "username")),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List active directory-visible users, optionally filtered and sorted."""
    users, total = await list_public_users(
        db,
        q=q,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)

    counts = await _followers_count_map(config)
    follows_counts = await follows_service.follows_count_map(db, [str(u.id) for u in users])
    items = []
    for u in users:
        item = _with_followers_count(PublicUserResponse.model_validate(u), counts, u.actor_url)
        item.follows_count = follows_counts.get(str(u.id), 0)
        items.append(item)
    return items


@router.get("/{user_id}/tags", response_model=List[TagSummaryResponse])
async def list_user_tags(
    response: Response,
    user_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"name", "item_count", "first_used", "last_used"}, "name")),
    db: AsyncSession = Depends(get_db),
):
    """List tags for resources owned by a specific user."""
    target = await db.get(User, user_id)
    if target is None or not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    summaries, total = await list_tags(
        db,
        user=user,
        target_user_id=target.id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    return [TagSummaryResponse.model_validate(s) for s in summaries]


@router.get("/{user_id}/tags/{tag}", response_model=List[TaggedItemResponse])
async def list_user_tag_items(
    response: Response,
    user_id: str,
    tag: str,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"type", "created_at"}, "created_at")),
    db: AsyncSession = Depends(get_db),
):
    """List items for a tag on resources owned by a specific user."""
    target = await db.get(User, user_id)
    if target is None or not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    try:
        validate_tag_name(tag)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from None

    items, total = await get_items_for_tag(
        db,
        tag_name=tag,
        user=user,
        target_user_id=target.id,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    return [TaggedItemResponse.model_validate(i) for i in items]


@router.get("/{username}/activities", response_model=ActivityListResponse)
async def list_user_activities_route(
    username: str,
    mode: str = Query("posts", description="Activity feed mode (posts or all)"),
    include_boosts: bool = Query(True, description="Include boosts in posts mode"),
    include_replies: bool = Query(False, description="Include replies in posts mode"),
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    cursor: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List a user's visible activities."""
    target = await get_user_by_username(db, username)
    if target is None or not target.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if mode not in ("posts", "all"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid mode")

    activities, next_cursor = await activity_service.list_user_activities(
        db,
        owner_user_id=str(target.id),
        user=user,
        mode=mode,
        include_boosts=include_boosts,
        include_replies=include_replies,
        source_type=source_type,
        cursor=cursor,
        limit=limit,
    )
    profile_map = await activity_service.resolve_source_actor_profiles(db, activities, config)
    summary_map = await activity_service.resolve_interaction_summaries(db, activities, user, config)
    return ActivityListResponse(
        activities=[
            _build_activity_response(
                a,
                profile_map.get(str(a.id), activity_service.ActorProfile()),
                summary_map.get(str(a.id)),
            )
            for a in activities
        ],
        next_cursor=next_cursor,
    )


@router.get("/{username}/followers", response_model=List[FollowerResponse])
async def list_user_followers(
    response: Response,
    username: str,
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List a user's followers, newest first."""
    user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    followers = await _load_followers(user, config)
    pagination.set_total(response, len(followers))
    page = followers[pagination.offset : pagination.offset + pagination.limit]
    return [_follower_response(f) for f in page]


@router.get("/{username}/follows", response_model=List[FollowingResponse])
async def list_user_follows(
    response: Response,
    username: str,
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    """
    List the actors a user follows, newest first.

    Only ``accepted`` follows are listed publicly; the owner additionally
    sees their ``pending`` outbound requests.
    """
    user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    states = None if viewer is not None and viewer.id == user.id else (FOLLOW_STATE_ACCEPTED,)
    rows = await follows_service.list_user_follows(db, user.id, states=states)
    pagination.set_total(response, len(rows))
    page = rows[pagination.offset : pagination.offset + pagination.limit]
    return await _following_responses(db, page)


@router.get("/{username}", response_model=PublicUserResponse)
async def get_user(
    username: str,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    viewer: Optional[User] = Depends(get_current_user_optional),
):
    """Get a user profile by username."""
    user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    result = PublicUserResponse.model_validate(user)
    counts = await _followers_count_map(config)
    result.follows_count = await follows_service.count_user_follows(db, user.id, states=(FOLLOW_STATE_ACCEPTED,))
    if viewer is not None and viewer.id != user.id:
        result.activity_subscribed = await notifications_service.is_subscribed_to_user_activity(
            db, str(viewer.id), str(user.id)
        )
        if user.actor_url:
            states = await follows_service.follow_states_for(db, viewer.id, [user.actor_url])
            result.follow_state = states.get(user.actor_url)
    return _with_followers_count(result, counts, user.actor_url)


@router.post(
    "/{username}/activity-subscription",
    response_model=ActivitySubscriptionState,
    dependencies=[Depends(rate_limit_account)],
)
async def subscribe_to_user_activity(
    username: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Subscribe to a local user's activity notifications (the profile bell).

    Subscribed users receive an ``activity`` notification — delivered per
    their notification preferences — for every activity the target authors
    that they may view. Subscribing is idempotent and also follows the
    target on a best-effort basis: being notified of every activity of a
    user one does not follow makes little sense. A failed follow (e.g. the
    target rejects followers, or federation is off) does not fail the
    subscription.
    """
    target = await get_user_by_username(db, username)
    if target is None or not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if target.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot subscribe to your own activity",
        )
    # The follow runs first: ``_follow_local`` writes through pubby's sync
    # storage, which must happen before the session accumulates writes.
    try:
        await follows_service.follow_user(db, config, current_user, target.actor_url or target.username)
    except Exception as exc:
        logger.info("Follow alongside activity subscription on %s failed: %s", username, exc)
    await notifications_service.subscribe_to_user_activity(db, str(current_user.id), str(target.id))
    await db.commit()
    follow_state = None
    if target.actor_url:
        states = await follows_service.follow_states_for(db, current_user.id, [target.actor_url])
        follow_state = states.get(target.actor_url)
    return ActivitySubscriptionState(activity_subscribed=True, follow_state=follow_state)


@router.delete(
    "/{username}/activity-subscription",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def unsubscribe_from_user_activity(
    username: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove the authenticated user's activity subscription on a user."""
    target = await get_user_by_username(db, username)
    if target is None or not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    await notifications_service.unsubscribe_from_user_activity(db, str(current_user.id), str(target.id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
