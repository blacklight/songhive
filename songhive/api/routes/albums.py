"""
Album routes.
"""

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/albums")


class AlbumResponse(BaseModel):
    id: str
    title: str
    artist_id: str
    musicbrainz_id: Optional[str] = None
    release_year: Optional[int] = None
    cover_url: Optional[str] = None


@router.get("/", response_model=List[AlbumResponse])
async def list_albums(
    q: Optional[str] = Query(None, description="Search query"),
    artist_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List or search albums."""
    # TODO: implement
    return []


@router.get("/{album_id}", response_model=AlbumResponse)
async def get_album(album_id: str):
    """Get an album by ID."""
    # TODO: implement
