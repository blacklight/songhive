"""
Artist routes.
"""

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import acl, audit, deletion, music
from ...services.federation import unpublish_track_activity
from ...services.storage import StorageService
from .._common import Pagination, client_ip, get_pagination
from ..deps import get_current_user, get_db, get_storage_service

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
    response: Response,
    q: Optional[str] = Query(None, description="Search query"),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """List or search artists."""
    total = await music.count_artists(db, query=q)
    rows = await music.list_artists(db, query=q, limit=pagination.limit, offset=pagination.offset)
    pagination.set_total(response, total)
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


@router.delete("/{artist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artist(
    artist_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    recursive: bool = Query(False, description="Also delete the artist's albums and tracks"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Delete an artist.  Recursively deletes albums and tracks when requested."""
    artist = await music.get_artist(db, artist_id)
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "artist", artist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="artist.delete",
        target_type="artist",
        target_id=artist_id,
        details={
            "name": artist.name,
            "recursive": recursive,
        },
        ip_address=client_ip(request),
    )

    try:
        result = await deletion.delete_artist(
            db,
            storage,
            artist_id,
            recursive=recursive,
            user=current_user,
            is_admin=current_user.is_admin,
        )
    except deletion.DeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.args[0]) from exc

    await db.commit()

    for info in result.unpublish:
        if info.owner is not None and info.artist is not None:
            background_tasks.add_task(
                unpublish_track_activity,
                info.track,
                info.artist,
                info.owner,
                request.app.state.config,
                info.federation_object_id,
            )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
