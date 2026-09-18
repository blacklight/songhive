"""
Mentions routes: the permanent archive of activities that mention the
current user, independent of dismissible notifications.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.mention_record import MentionSource
from ...models.user import User
from ...services import mention_records
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_db

router = APIRouter(prefix="/mentions")


class MentionResponse(BaseModel):
    """A single archived mention."""

    id: str
    source: MentionSource
    actor_url: Optional[str] = None
    source_url: Optional[str] = None
    activity_id: Optional[str] = None
    visibility: Optional[str] = None
    payload: Optional[dict] = None
    created_at: Optional[str] = None


def _parse_sources(value: Optional[str]) -> Optional[List[str]]:
    """Parse the comma-separated ``source`` allowlist; ``None`` when absent."""
    if value is None or not value.strip():
        return None
    requested = {item.strip().lower() for item in value.split(",")}
    return [s.value for s in MentionSource if s.value in requested]


@router.get("/", response_model=List[MentionResponse])
async def list_mentions(
    response: Response,
    source: Optional[str] = Query(None, description="Comma-separated source allowlist"),
    visibility: Optional[str] = Query(None, description="``private`` restricts to non-public mentions"),
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's mention records, newest first."""
    rows, total = await mention_records.list_mentions(
        db,
        current_user.id,
        sources=_parse_sources(source),
        private_only=visibility == "private",
        limit=pagination.limit,
        offset=pagination.offset,
    )
    pagination.set_total(response, total)
    return [MentionResponse(**mention_records.mention_to_dict(row)) for row in rows]
