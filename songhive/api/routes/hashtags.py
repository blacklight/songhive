"""
Global and admin hashtag endpoints.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import audit
from ...services.hashtags import (
    HashtagSummary,
    delete_hashtag_globally,
    get_items_for_hashtag,
    list_hashtags,
)
from .._common import Pagination, client_ip, get_pagination
from .._sorting import SortParams, get_sort
from ..deps import get_current_user_optional, get_db, require_admin

router = APIRouter(prefix="/hashtags")


class HashtagSummaryResponse(BaseModel):
    """Hashtag summary for list responses."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    item_count: int
    first_used: Optional[datetime] = None
    last_used: Optional[datetime] = None


class TaggedItemResponse(BaseModel):
    """A single item associated with a hashtag."""

    model_config = ConfigDict(from_attributes=True)

    type: str
    id: str


def _summaries_response(summaries: List[HashtagSummary]) -> List[HashtagSummaryResponse]:
    return [
        HashtagSummaryResponse(
            name=s.name,
            item_count=s.item_count,
            first_used=s.first_used,
            last_used=s.last_used,
        )
        for s in summaries
    ]


@router.get("/", response_model=List[HashtagSummaryResponse])
async def list_all_hashtags(
    response: Response,
    q: Optional[str] = Query(None, description="Search hashtag names"),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"name", "item_count", "first_used", "last_used"}, "name")),
    db: AsyncSession = Depends(get_db),
):
    """List hashtags linked to resources visible to the requester."""
    summaries, total = await list_hashtags(
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


@router.get("/{hashtag}", response_model=List[TaggedItemResponse])
async def list_hashtag_items(
    response: Response,
    hashtag: str,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"type", "created_at"}, "created_at")),
    db: AsyncSession = Depends(get_db),
):
    """List visible items for a specific hashtag."""
    items, total = await get_items_for_hashtag(
        db,
        hashtag_name=hashtag,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    return [TaggedItemResponse(type=i.type, id=i.id) for i in items]


@router.delete("/{hashtag}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_global_hashtag(
    request: Request,
    hashtag: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a hashtag and all its associations (admin only)."""
    from ...services.hashtags import _get_hashtag_by_name

    target = await _get_hashtag_by_name(db, hashtag)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hashtag not found")

    deleted = await delete_hashtag_globally(db, hashtag)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hashtag not found")

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="hashtag.delete",
        target_type="hashtag",
        target_id=target.id,
        details={"name": hashtag},
        ip_address=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
