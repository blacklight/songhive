"""
Activity interaction routes.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.activity import Activity
from ...models.user import User
from ...services import activities as activity_service
from ...services import federation as federation_service
from ...services.federation import ensure_user_actor
from ..deps import get_config, get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/activities")


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
