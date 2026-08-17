"""
Playlist routes.
"""

from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter(prefix="/playlists")


class PlaylistResponse(BaseModel):
    id: str
    name: str
    owner_id: str
    description: Optional[str] = None
    is_public: bool = False


@router.get("/", response_model=List[PlaylistResponse])
async def list_playlists(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List playlists for the current user."""
    # TODO: implement
    return []


@router.post("/", response_model=PlaylistResponse, status_code=201)
async def create_playlist():
    """Create a new playlist."""
    # TODO: implement


@router.get("/{playlist_id}", response_model=PlaylistResponse)
async def get_playlist(playlist_id: str):
    """Get a playlist by ID."""
    # TODO: implement
