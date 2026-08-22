"""
Favorites routes.
"""

from typing import List

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.favorite import Favorite
from ...models.user import User
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


@router.post("/{track_id}", status_code=201)
async def add_favorite(track_id: str):
    """Add a track to favorites."""
    # TODO: implement


@router.delete("/{track_id}", status_code=204)
async def remove_favorite(track_id: str):
    """Remove a track from favorites."""
    # TODO: implement
