"""
Activity interaction routes.
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...models._enums import Visibility
from ...models.activity import Activity
from ...models.user import User
from ...services import acl
from ...services import activities as activity_service
from ...services import federation as federation_service
from ...services.federation import ensure_user_actor
from ..deps import get_config, get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/activities")


class ActivityUpdate(BaseModel):
    """Activity partial update."""

    content: Optional[str] = None
    visibility: Optional[Visibility] = None


@router.patch("/{activity_id}")
async def update_activity(
    activity_id: str,
    body: ActivityUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an activity's content and/or visibility."""
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await acl.can_manage(db, current_user, activity.entity_type, activity.entity_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to edit this activity",
        )

    config = get_config(request)
    if body.content is not None:
        await activity_service.update_activity(db, activity, content_source=body.content, config=config)
    if body.visibility is not None:
        # cascade_visibility_update resolves the entity and enforces
        # containment internally (404 missing entity, 422 violation).
        await activity_service.VisibilityRules.cascade_visibility_update(db, activity, body.visibility)

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
    await db.commit()

    if config.federation.enabled and current_user.private_key_pem:
        try:
            await asyncio.to_thread(federation_service.publish_like_activity, current_user, activity, like, config)
        except Exception as e:
            # Fan-out is best-effort: a broker or resolution failure must not
            # fail the like itself.
            logger.exception("Failed to fan out like for activity %s: %s: %s", activity_id, type(e), e)

    return {"status": "ok", "activity_id": str(like.id)}
