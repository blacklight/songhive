"""
Listening history routes.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_serializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...models.history import ListeningHistory
from ...models.track import Track
from ...models.user import User
from ...services import acl
from ...services.streaming import record_listen as record_listen_service
from ..deps import get_current_user, get_db

router = APIRouter(prefix="/history")


class HistoryEntry(BaseModel):
    """A single listening-history entry."""

    id: str
    track_id: str
    title: Optional[str] = None
    artist: Optional[str] = None
    created_at: datetime

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str:
        """Serialize the timestamp as an ISO 8601 string."""
        return value.isoformat()


@router.get("/", response_model=List[HistoryEntry])
async def list_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List listening history for the current user, newest first."""

    result = await db.execute(
        select(ListeningHistory)
        .where(ListeningHistory.user_id == current_user.id)
        .order_by(ListeningHistory.created_at.desc())
        .limit(limit)
        .offset(offset)
        .options(selectinload(ListeningHistory.track).selectinload(Track.artist))
    )
    rows = result.scalars().all()

    return [
        HistoryEntry(
            id=str(entry.id),
            track_id=str(entry.track_id),
            title=entry.track.title if entry.track is not None else None,
            artist=entry.track.artist.name if entry.track is not None and entry.track.artist is not None else None,
            created_at=entry.created_at,
        )
        for entry in rows
    ]


@router.post("/{track_id}", status_code=status.HTTP_201_CREATED)
async def record_listen(
    track_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record a track listen event for the current user."""
    track = await db.get(Track, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_access(db, current_user, "track", track_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    await record_listen_service(db, str(current_user.id), track_id)

    return {"track_id": track_id, "recorded": True}
