"""
Playlist routes.
"""

from typing import List, Optional, cast

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models._enums import Visibility
from ...models.playlist import Playlist
from ...models.user import User
from ...services import music
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_current_user_optional, get_db, require_access
from ._common import HasOwnerId, redact_owner

router = APIRouter(prefix="/playlists")


class PlaylistResponse(BaseModel):
    """Public playlist response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_id: Optional[str] = None
    description: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


class PlaylistCreate(BaseModel):
    """Playlist creation payload."""

    name: str
    description: Optional[str] = None


@router.get("/", response_model=List[PlaylistResponse])
async def list_playlists(
    response: Response,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List playlists visible to the requester."""
    total = await music.count_playlists(db, user=user)
    rows = await music.list_playlists(db, user=user, limit=pagination.limit, offset=pagination.offset)
    pagination.set_total(response, total)
    return [
        PlaylistResponse(
            id=str(p.id),
            name=p.name,
            owner_id=redact_owner(cast(HasOwnerId, p), user),
            description=p.description,
            visibility=p.visibility,
        )
        for p in rows
    ]


@router.post("/", response_model=PlaylistResponse, status_code=201)
async def create_playlist(
    body: PlaylistCreate,
    visibility: Visibility = Query(Visibility.PRIVATE),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new playlist owned by the current user."""
    playlist = Playlist(
        name=body.name,
        owner_id=current_user.id,
        description=body.description,
        visibility=visibility.value,
    )
    db.add(playlist)
    await db.commit()

    return PlaylistResponse(
        id=str(playlist.id),
        name=playlist.name,
        owner_id=playlist.owner_id,
        description=playlist.description,
        visibility=playlist.visibility,
    )


@router.get(
    "/{playlist_id}",
    response_model=PlaylistResponse,
    dependencies=[Depends(require_access("playlist"))],
)
async def get_playlist(
    playlist_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Get a playlist by ID."""
    playlist = await music.get_playlist(db, playlist_id)
    # ``require_access`` already loads the row and raises 404 when missing.
    assert playlist is not None

    return PlaylistResponse(
        id=str(playlist.id),
        name=playlist.name,
        owner_id=redact_owner(cast(HasOwnerId, playlist), user),
        description=playlist.description,
        visibility=playlist.visibility,
    )
