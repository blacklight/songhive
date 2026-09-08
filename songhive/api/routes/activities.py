"""
Activity interaction routes.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models import Visibility
from ...models.activity import Activity
from ...models.user import User
from ...services import acl
from ...services import activities as activity_service
from ...services import audit
from ...services.federation import ensure_user_actor
from .._common import client_ip
from ..deps import get_config, get_current_user, get_current_user_optional, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/activities")
entity_router = APIRouter()


class ActivityUpdate(BaseModel):
    """Activity partial update."""

    content: Optional[str] = None
    visibility: Optional[Visibility] = None


class ActivityMentionResponse(BaseModel):
    """Serialized activity mention."""

    model_config = ConfigDict(from_attributes=True)

    handle: str
    actor_url: Optional[str] = None
    user_id: Optional[str] = None


class ActivityResponse(BaseModel):
    """Serialized activity."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    entity_type: str
    entity_id: str
    activity_type: str
    source_type: str
    source_actor: str
    source_id: str
    local_object_id: Optional[str] = None
    owner_user_id: Optional[str] = None
    source_actor_avatar_url: Optional[str] = None
    source_actor_display_name: Optional[str] = None
    visibility: Visibility
    in_reply_to_activity_id: Optional[str] = None
    content: Optional[str] = None
    content_source: Optional[str] = None
    content_type: Optional[str] = None
    published_at: datetime
    mentions: List[ActivityMentionResponse] = []


class ActivityListResponse(BaseModel):
    """A page of activities."""

    activities: List[ActivityResponse]
    next_cursor: Optional[str] = None


@entity_router.get("/{entity_type}/{entity_id}/activities", response_model=ActivityListResponse)
async def list_entity_activities(
    entity_type: str,
    entity_id: str,
    activity_type: Optional[str] = None,
    source_type: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    List the activities attached to an entity, newest first.

    Anonymous requesters see only ``public`` activities on publicly
    accessible entities; authenticated users additionally see ``local`` and
    ``followers`` activities, activities they own, and ``mentioned``
    activities that name them. Pass the returned ``next_cursor`` back as
    ``cursor`` to fetch the next page.
    """
    if entity_type not in activity_service.ACTIVITY_ENTITY_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid entity_type")

    entity = await activity_service.resolve_entity(db, entity_type, entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")

    if not await acl.can_access(db, user, entity_type, entity_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this entity",
        )

    activities, next_cursor = await activity_service.list_activities(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        user=user,
        activity_type=activity_type,
        source_type=source_type,
        cursor=cursor,
        limit=limit,
    )
    profile_map = await activity_service.resolve_source_actor_profiles(db, activities, config)
    return ActivityListResponse(
        activities=[
            _build_activity_response(a, profile_map.get(str(a.id), activity_service.ActorProfile())) for a in activities
        ],
        next_cursor=next_cursor,
    )


def _build_activity_response(activity: Activity, profile: Optional[activity_service.ActorProfile]) -> ActivityResponse:
    """Build an ``ActivityResponse`` with the resolved source actor profile."""
    response = ActivityResponse.model_validate(activity)
    response.source_actor_avatar_url = profile.avatar_url if profile else None
    response.source_actor_display_name = profile.display_name if profile else None
    return response


@router.patch("/{activity_id}", response_model=ActivityResponse)
async def update_activity(
    activity_id: str,
    body: ActivityUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update an activity's content and/or visibility.

    Content edits fan an ``Update`` carrying the rebuilt object out to the
    inboxes that already received the activity; visibility edits cascade an
    ``Update`` (new audience) or a ``Delete(Tombstone)`` (non-federating
    visibility) through ``cascade_visibility_update``.
    """
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await acl.can_manage(db, current_user, activity.entity_type, activity.entity_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to edit this activity",
        )

    config = get_config(request)
    old_visibility = activity.visibility
    if body.content is not None:
        await activity_service.update_activity(db, activity, content_source=body.content, config=config)
    if body.visibility is not None:
        # cascade_visibility_update resolves the entity and enforces
        # containment internally (404 missing entity, 422 violation).
        await activity_service.VisibilityRules.cascade_visibility_update(db, activity, body.visibility)
    if body.content is not None:
        # Fan out after the visibility edit so the Update carries the final
        # audience; it no-ops when the (new) visibility does not federate.
        try:
            await activity_service.fan_out_activity_update(db, activity, config)
        except Exception as e:
            # Fan-out is best-effort: a broker or resolution failure must not
            # fail the edit itself.
            logger.exception("Failed to fan out update for activity %s: %s: %s", activity_id, type(e), e)

    details: dict = {"entity_type": activity.entity_type, "entity_id": activity.entity_id}
    if body.visibility is not None:
        details["visibility"] = {"old": old_visibility, "new": activity.visibility}
    if body.content is not None:
        details["content_changed"] = True

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="activity.update",
        target_type="activity",
        target_id=str(activity.id),
        details=details,
        ip_address=client_ip(request),
    )

    await db.refresh(activity, ["mentions"])
    profile_map = await activity_service.resolve_source_actor_profiles(db, [activity], config)
    await db.commit()
    return _build_activity_response(activity, profile_map.get(str(activity.id), activity_service.ActorProfile()))


@router.delete("/{activity_id}")
async def delete_activity(
    activity_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retract an activity.

    Local activities are soft-deleted and a ``Delete(Tombstone)`` is fanned
    out to every inbox the activity previously reached; remote activities
    are simply removed locally.
    """
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await acl.can_manage(db, current_user, activity.entity_type, activity.entity_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this activity",
        )

    await activity_service.retract_activity(db, activity)
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="activity.delete",
        target_type="activity",
        target_id=str(activity.id),
        details={
            "entity_type": activity.entity_type,
            "entity_id": activity.entity_id,
            "visibility": activity.visibility,
        },
        ip_address=client_ip(request),
    )
    await db.commit()
    return {"status": "ok"}


@router.post("/{activity_id}/like", status_code=status.HTTP_201_CREATED)
async def like_activity(
    activity_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Like an activity."""
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await activity_service.can_view_activity(db, current_user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    config = get_config(request)
    ensure_user_actor(current_user, config)
    like = await activity_service.like_activity(db, activity=activity, author=current_user)
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="activity.like",
        target_type="activity",
        target_id=str(activity.id),
        details={
            "entity_type": activity.entity_type,
            "entity_id": activity.entity_id,
            "visibility": activity.visibility,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    if config.federation.enabled and current_user.private_key_pem:
        try:
            await activity_service.fan_out_like_activity(
                db, like=like, target=activity, author=current_user, config=config
            )
            await db.commit()
        except Exception as e:
            # Fan-out is best-effort: a broker or resolution failure must not
            # fail the like itself.
            logger.exception("Failed to fan out like for activity %s: %s: %s", activity_id, type(e), e)

    return {"status": "ok", "activity_id": str(like.id)}
