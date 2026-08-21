"""
Artist routes.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...services import music
from ...services.storage import StorageService
from ..deps import get_db, get_storage_service

router = APIRouter(prefix="/artists")


class ArtistResponse(BaseModel):
    """Public artist response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    musicbrainz_id: Optional[str] = None
    bio: Optional[str] = None
    image_file_id: Optional[str] = None
    image_url: Optional[str] = None


async def _image_url(artist, storage: StorageService) -> Optional[str]:
    """Resolve an artist image URL from a stored file or remote URL."""
    if artist.image_file_id and artist.image_file:
        return await storage.get_url(artist.image_file)
    return artist.image_url


@router.get("/", response_model=List[ArtistResponse])
async def list_artists(
    q: Optional[str] = Query(None, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """List or search artists."""
    rows = await music.list_artists(db, query=q, limit=limit, offset=offset)
    return [
        ArtistResponse(
            id=str(a.id),
            name=a.name,
            musicbrainz_id=a.musicbrainz_id,
            bio=a.bio,
            image_file_id=a.image_file_id,
            image_url=await _image_url(a, storage),
        )
        for a in rows
    ]


@router.get("/{artist_id}", response_model=ArtistResponse)
async def get_artist(
    artist_id: str,
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Get an artist by ID."""
    artist = await music.get_artist(db, artist_id)
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return ArtistResponse(
        id=str(artist.id),
        name=artist.name,
        musicbrainz_id=artist.musicbrainz_id,
        bio=artist.bio,
        image_file_id=artist.image_file_id,
        image_url=await _image_url(artist, storage),
    )
