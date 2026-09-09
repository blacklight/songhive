"""
Global and admin tag endpoints.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.user import User
from ...services import activities as activity_service
from ...services import audit
from ...services.tags import (
    TAG_ITEM_TYPES,
    TagSummary,
    delete_tag_globally,
    get_items_for_tag,
    list_tags,
    validate_tag_name,
)
from .._common import Pagination, client_ip, get_pagination
from .._sorting import SortParams, get_sort
from ..deps import get_config, get_current_user_optional, get_db, require_admin
from .activities import ActivityListResponse, _build_activity_response

router = APIRouter(prefix="/tags")


class TagSummaryResponse(BaseModel):
    """Tag summary for list responses."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    item_count: int
    first_used: Optional[datetime] = None
    last_used: Optional[datetime] = None


class TaggedItemResponse(BaseModel):
    """A single item associated with a tag."""

    model_config = ConfigDict(from_attributes=True)

    type: str
    id: str


def _summaries_response(summaries: List[TagSummary]) -> List[TagSummaryResponse]:
    return [
        TagSummaryResponse(
            name=s.name,
            item_count=s.item_count,
            first_used=s.first_used,
            last_used=s.last_used,
        )
        for s in summaries
    ]


@router.get("/", response_model=List[TagSummaryResponse])
async def list_all_tags(
    response: Response,
    q: Optional[str] = Query(None, description="Search tag names"),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"name", "item_count", "first_used", "last_used"}, "name")),
    db: AsyncSession = Depends(get_db),
):
    """List tags linked to resources visible to the requester."""
    summaries, total = await list_tags(
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


@router.get("/{tag}", response_model=List[TaggedItemResponse])
async def list_tag_items(
    response: Response,
    tag: str,
    type: Optional[str] = Query(None, description="Filter by item type"),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"type", "created_at"}, "created_at")),
    db: AsyncSession = Depends(get_db),
):
    """List visible items for a specific tag."""
    try:
        validate_tag_name(tag)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from None

    if type is not None and type not in TAG_ITEM_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid item type: {type}",
        )

    items, total = await get_items_for_tag(
        db,
        tag_name=tag,
        user=user,
        item_type=type,
        limit=pagination.limit,
        offset=pagination.offset,
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    return [TaggedItemResponse(type=i.type, id=i.id) for i in items]


@router.get("/{tag}/activities", response_model=ActivityListResponse)
async def list_tag_activities(
    tag: str,
    cursor: Optional[str] = Query(None, description="Pagination cursor from the previous page"),
    limit: int = Query(20, ge=1, le=100),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List the visible activities that include ``tag`` as a hashtag."""
    try:
        validate_tag_name(tag)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from None

    activities, next_cursor = await activity_service.list_activities_for_tag(
        db,
        tag_name=tag,
        user=user,
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


@router.delete("/{tag}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_global_tag(
    request: Request,
    tag: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a tag and all its associations (admin only)."""
    try:
        validate_tag_name(tag)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from None

    deleted = await delete_tag_globally(db, tag)
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="tag.delete",
        target_type="tag",
        target_id=deleted.id,
        details={"name": tag},
        ip_address=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
