"""
Artist routes.
"""

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/artists")


class ArtistResponse(BaseModel):
    id: str
    name: str
    musicbrainz_id: Optional[str] = None
    bio: Optional[str] = None
    image_url: Optional[str] = None


@router.get("/", response_model=List[ArtistResponse])
async def list_artists(
    q: Optional[str] = Query(None, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List or search artists."""
    # TODO: implement
    return []


@router.get("/{artist_id}", response_model=ArtistResponse)
async def get_artist(artist_id: str):
    """Get an artist by ID."""
    # TODO: implement
