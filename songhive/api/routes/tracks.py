"""
Track routes.
"""

from typing import List, Optional, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models._enums import Visibility
from ...models.user import User
from ...services import music
from ..deps import get_current_user_optional, get_db, require_access
from ._common import HasOwnerId, redact_owner

router = APIRouter(prefix="/tracks")


class TrackResponse(BaseModel):
    """Public track response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    artist_id: str
    album_id: Optional[str] = None
    track_number: Optional[int] = None
    duration: Optional[float] = None
    genre: Optional[str] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


@router.get("/", response_model=List[TrackResponse])
async def list_tracks(
    q: Optional[str] = Query(None, description="Search query"),
    artist_id: Optional[str] = Query(None),
    album_id: Optional[str] = Query(None),
    user: Optional[User] = Depends(get_current_user_optional),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List or search tracks visible to the requester."""
    rows = await music.list_tracks(
        db,
        query=q,
        artist_id=artist_id,
        album_id=album_id,
        user=user,
        limit=limit,
        offset=offset,
    )
    return [
        TrackResponse(
            id=str(t.id),
            title=t.title,
            artist_id=t.artist_id,
            album_id=t.album_id,
            track_number=t.track_number,
            duration=t.duration,
            genre=t.genre,
            owner_id=redact_owner(cast(HasOwnerId, t), user),
            visibility=t.visibility,
        )
        for t in rows
    ]


@router.get(
    "/{track_id}",
    response_model=TrackResponse,
    dependencies=[Depends(require_access("track"))],
)
async def get_track(
    track_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get a track by ID."""
    track = await music.get_track(db, track_id)
    # ``require_access`` already loads the row and raises 404 when missing.
    assert track is not None

    return TrackResponse(
        id=str(track.id),
        title=track.title,
        artist_id=track.artist_id,
        album_id=track.album_id,
        track_number=track.track_number,
        duration=track.duration,
        genre=track.genre,
        owner_id=redact_owner(cast(HasOwnerId, track), user),
        visibility=track.visibility,
    )
