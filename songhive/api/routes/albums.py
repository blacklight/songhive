"""
Album routes.
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

router = APIRouter(prefix="/albums")


class AlbumResponse(BaseModel):
    """Public album response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    artist_id: str
    musicbrainz_id: Optional[str] = None
    release_year: Optional[int] = None
    cover_url: Optional[str] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


@router.get("/", response_model=List[AlbumResponse])
async def list_albums(
    q: Optional[str] = Query(None, description="Search query"),
    artist_id: Optional[str] = Query(None),
    user: Optional[User] = Depends(get_current_user_optional),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List or search albums visible to the requester."""
    rows = await music.list_albums(
        db,
        query=q,
        artist_id=artist_id,
        user=user,
        limit=limit,
        offset=offset,
    )
    return [
        AlbumResponse(
            id=str(a.id),
            title=a.title,
            artist_id=a.artist_id,
            musicbrainz_id=a.musicbrainz_id,
            release_year=a.release_year,
            cover_url=a.cover_url,
            owner_id=redact_owner(cast(HasOwnerId, a), user),
            visibility=a.visibility,
        )
        for a in rows
    ]


@router.get(
    "/{album_id}",
    response_model=AlbumResponse,
    dependencies=[Depends(require_access("album"))],
)
async def get_album(
    album_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get an album by ID."""
    album = await music.get_album(db, album_id)
    # ``require_access`` already loads the row and raises 404 when missing.
    assert album is not None

    return AlbumResponse(
        id=str(album.id),
        title=album.title,
        artist_id=album.artist_id,
        musicbrainz_id=album.musicbrainz_id,
        release_year=album.release_year,
        cover_url=album.cover_url,
        owner_id=redact_owner(cast(HasOwnerId, album), user),
        visibility=album.visibility,
    )
