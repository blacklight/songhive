"""
Favorites routes.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.favorite import Favorite
from ...models.track import Track
from ...models.user import User
from ...services import acl
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_db

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

    return FavoriteResponse(
        id=str(favorite.id),
        track_id=str(favorite.track_id),
        created_at=favorite.created_at.isoformat(),
    )


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

    return Response(status_code=status.HTTP_204_NO_CONTENT)
