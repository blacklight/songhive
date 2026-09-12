"""
Favorites routes.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.favorite import Favorite
from ...models.notification import NotificationType
from ...models.track import Track
from ...models.user import User
from ...services import acl
from ...services import notifications as notifications_service
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorites")


class FavoriteResponse(BaseModel):
    id: str
    track_id: str
    created_at: str


@router.get("/", response_model=List[FavoriteResponse])
async def list_favorites(
    response: Response,
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's favorites."""
    total = (await db.execute(select(func.count(Favorite.id)).where(Favorite.user_id == current_user.id))).scalar() or 0
    result = await db.execute(
        select(Favorite)
        .where(Favorite.user_id == current_user.id)
        .order_by(Favorite.created_at.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
    )
    rows = result.scalars().all()
    pagination.set_total(response, total)
    return [
        FavoriteResponse(
            id=str(f.id),
            track_id=str(f.track_id),
            created_at=f.created_at.isoformat(),
        )
        for f in rows
    ]


@router.post("/{track_id}", response_model=FavoriteResponse, status_code=status.HTTP_201_CREATED)
async def add_favorite(
    track_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a track to the current user's favorites."""
    track = await db.get(Track, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_access(db, current_user, "track", track_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    result = await db.execute(
        select(Favorite).where(Favorite.user_id == current_user.id, Favorite.track_id == track_id)
    )
    favorite = result.scalar_one_or_none()
    if favorite is None:
        favorite = Favorite(user_id=current_user.id, track_id=track_id)
        db.add(favorite)
        await db.commit()
        await db.refresh(favorite)
        await _notify_favorite(db, track, current_user)

    return FavoriteResponse(
        id=str(favorite.id),
        track_id=str(favorite.track_id),
        created_at=favorite.created_at.isoformat(),
    )


async def _notify_favorite(db: AsyncSession, track: Track, current_user: User) -> None:
    """Create a like notification for the track owner, never failing the route."""
    try:
        if track.owner_id is None or track.owner_id == current_user.id:
            return
        await notifications_service.create_notification(
            db,
            user_id=track.owner_id,  # type: ignore
            type=NotificationType.LIKE,
            actor_url=current_user.actor_url or f"/users/{current_user.username}",
            source_url=f"/tracks/{track.id}",
            payload={
                "track_title": track.title,
                "actor_name": current_user.display_name or current_user.username,
                "actor_avatar_url": current_user.avatar_url,
                "item_type": "track",
                "item_id": str(track.id),
            },
        )
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to create like notification for track %s: %s", track.id, exc)


@router.delete("/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    track_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a track from the current user's favorites."""
    result = await db.execute(
        select(Favorite).where(Favorite.user_id == current_user.id, Favorite.track_id == track_id)
    )
    favorite = result.scalar_one_or_none()
    if favorite is not None:
        await db.delete(favorite)
        await db.commit()
        await _retract_favorite_notification(db, track_id, current_user)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _retract_favorite_notification(db: AsyncSession, track_id: str, current_user: User) -> None:
    """Retract the like notification this favorite produced, never failing."""
    try:
        track = await db.get(Track, track_id)
        if track is None or track.owner_id is None or track.owner_id == current_user.id:
            return
        await notifications_service.retract_notifications(
            db,
            user_id=track.owner_id,
            type=NotificationType.LIKE,
            actor_urls=[u for u in (current_user.actor_url, f"/users/{current_user.username}") if u],  # type: ignore
            source_url=f"/tracks/{track_id}",
        )
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to retract like notification for track %s: %s", track_id, exc)
