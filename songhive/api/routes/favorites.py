"""
Favorites routes.
"""

from typing import List

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/favorites")


class FavoriteResponse(BaseModel):
    id: str
    track_id: str
    created_at: str


@router.get("/", response_model=List[FavoriteResponse])
async def list_favorites(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List the current user's favorites."""
    # TODO: implement
    return []


@router.post("/{track_id}", status_code=201)
async def add_favorite(track_id: str):
    """Add a track to favorites."""
    # TODO: implement


@router.delete("/{track_id}", status_code=204)
async def remove_favorite(track_id: str):
    """Remove a track from favorites."""
    # TODO: implement
