"""
Listening history routes.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, field_serializer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...models.album import Album
from ...models.history import ListeningHistory
from ...models.track import Track
from ...models.user import User
from ...services import acl
from ...services.storage import StorageService
from ...services.streaming import record_listen as record_listen_service
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_db, get_storage_service
from ..responses import _track_image_url

router = APIRouter(prefix="/history")


class HistoryEntry(BaseModel):
    """A single listening-history entry."""

    id: str
    track_id: str
    title: Optional[str] = None
    artist: Optional[str] = None
    image_url: Optional[str] = None
    created_at: datetime

    @field_serializer("created_at")
    def _serialize_created_at(self, value: datetime) -> str:
        """Serialize the timestamp as an ISO 8601 string."""
        return value.isoformat()


@router.get("/", response_model=List[HistoryEntry])
async def list_history(
    response: Response,
    pagination: Pagination = Depends(get_pagination),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """List listening history for the current user, newest first."""
    total = (
        await db.execute(select(func.count(ListeningHistory.id)).where(ListeningHistory.user_id == current_user.id))
    ).scalar() or 0

    result = await db.execute(
        select(ListeningHistory)
        .where(ListeningHistory.user_id == current_user.id)
        .order_by(ListeningHistory.created_at.desc())
        .limit(pagination.limit)
        .offset(pagination.offset)
        .options(
            selectinload(ListeningHistory.track).selectinload(Track.artist),
            selectinload(ListeningHistory.track).selectinload(Track.image_file),
            selectinload(ListeningHistory.track).selectinload(Track.album).selectinload(Album.cover_file),
        )
    )
    rows = result.scalars().all()
    pagination.set_total(response, total)

    entries = []
    for entry in rows:
        track = entry.track
        entries.append(
            HistoryEntry(
                id=str(entry.id),
                track_id=str(entry.track_id),
                title=track.title if track is not None else None,
                artist=track.artist.name if track is not None and track.artist is not None else None,
                image_url=await _track_image_url(track, storage) if track is not None else None,
                created_at=entry.created_at,
            )
        )
    return entries


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
