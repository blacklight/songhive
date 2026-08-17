"""
Track routes.
"""

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/tracks")


class TrackResponse(BaseModel):
    id: str
    title: str
    artist_id: str
    album_id: Optional[str] = None
    track_number: Optional[int] = None
    duration: Optional[float] = None
    genre: Optional[str] = None


@router.get("/", response_model=List[TrackResponse])
async def list_tracks(
    q: Optional[str] = Query(None, description="Search query"),
    artist_id: Optional[str] = Query(None),
    album_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List or search tracks."""
    # TODO: implement
    return []


@router.get("/{track_id}", response_model=TrackResponse)
async def get_track(track_id: str):
    """Get a track by ID."""
    # TODO: implement
