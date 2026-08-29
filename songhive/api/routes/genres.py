"""
Global and admin genre endpoints.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import audit
from ...services.genres import (
    GenreSummary,
    delete_genre_globally,
    get_items_for_genre,
    list_genres,
    validate_genre_name,
)
from .._common import Pagination, client_ip, get_pagination
from .._sorting import SortParams, get_sort
from ..deps import get_current_user_optional, get_db, require_admin

router = APIRouter(prefix="/genres")


class GenreSummaryResponse(BaseModel):
    """Genre summary for list responses."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    item_count: int
    first_used: Optional[datetime] = None
    last_used: Optional[datetime] = None


class GenreItemResponse(BaseModel):
    """A single item associated with a genre."""

    model_config = ConfigDict(from_attributes=True)

    type: str
    id: str


def _summaries_response(summaries: List[GenreSummary]) -> List[GenreSummaryResponse]:
    return [
        GenreSummaryResponse(
            name=s.name,
            item_count=s.item_count,
            first_used=s.first_used,
            last_used=s.last_used,
        )
        for s in summaries
    ]


@router.get("/", response_model=List[GenreSummaryResponse])
async def list_all_genres(
    response: Response,
    q: Optional[str] = Query(None, description="Search genre names"),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"name", "item_count", "first_used", "last_used"}, "name")),
    db: AsyncSession = Depends(get_db),
):
    """List genres linked to resources visible to the requester."""
    summaries, total = await list_genres(
        db,
        user=user,
        query=q,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    return _summaries_response(summaries)


@router.get("/{genre}", response_model=List[GenreItemResponse])
async def list_genre_items(
    response: Response,
    genre: str,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"type", "created_at"}, "created_at")),
    db: AsyncSession = Depends(get_db),
):
    """List visible items for a specific genre."""
    try:
        validate_genre_name(genre)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Genre not found",
        ) from None

    items, total = await get_items_for_genre(
        db,
        genre_name=genre,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    return [GenreItemResponse(type=i.type, id=i.id) for i in items]


@router.delete("/{genre}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_global_genre(
    request: Request,
    genre: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a genre and all its associations (admin only)."""
    try:
        validate_genre_name(genre)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Genre not found",
        ) from None

    deleted = await delete_genre_globally(db, genre)
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Genre not found")

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="genre.delete",
        target_type="genre",
        target_id=deleted.id,
        details={"name": genre},
        ip_address=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
