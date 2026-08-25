"""
Album routes.
"""

from typing import List, Optional, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models._enums import Visibility
from ...models.user import User
from ...services import acl, audit, deletion, music
from ...services.federation import unpublish_track_activity
from ...services.storage import StorageService
from .._common import Pagination, client_ip, get_pagination
from ..deps import (
    get_current_user,
    get_current_user_optional,
    get_db,
    get_storage_service,
    require_access,
)
from ..middleware.rate_limit import rate_limit_account
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
    description: Optional[str] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


class AlbumUpdate(BaseModel):
    """Album partial update."""

    title: Optional[str] = None
    release_year: Optional[int] = None
    description: Optional[str] = None
    visibility: Optional[Visibility] = None


async def _cover_url(storage: StorageService, album) -> Optional[str]:
    """Resolve an album's cover URL from its stored cover file if available."""
    if album.cover_file_id and album.cover_file:
        return await storage.get_url(album.cover_file)
    return album.cover_url


@router.get("/", response_model=List[AlbumResponse])
async def list_albums(
    response: Response,
    q: Optional[str] = Query(None, description="Search query"),
    artist_id: Optional[str] = Query(None),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """List or search albums visible to the requester."""
    total = await music.count_albums(
        db,
        query=q,
        artist_id=artist_id,
        year_from=year_from,
        year_to=year_to,
        user=user,
    )
    rows = await music.list_albums(
        db,
        query=q,
        artist_id=artist_id,
        year_from=year_from,
        year_to=year_to,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    pagination.set_total(response, total)
    return [
        AlbumResponse(
            id=str(a.id),
            title=a.title,
            artist_id=a.artist_id,
            musicbrainz_id=a.musicbrainz_id,
            release_year=a.release_year,
            cover_url=await _cover_url(storage, a),
            description=a.description,
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
    storage: StorageService = Depends(get_storage_service),
):
    """Get an album by ID."""
    album = await music.get_album(db, album_id)
    if album is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return AlbumResponse(
        id=str(album.id),
        title=album.title,
        artist_id=album.artist_id,
        musicbrainz_id=album.musicbrainz_id,
        release_year=album.release_year,
        cover_url=await _cover_url(storage, album),
        description=album.description,
        owner_id=redact_owner(cast(HasOwnerId, album), user),
        visibility=album.visibility,
    )


@router.patch("/{album_id}", response_model=AlbumResponse)
async def update_album(
    album_id: str,
    body: AlbumUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Partially update an album."""
    album = await music.get_album(db, album_id)
    if album is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "album", album_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if body.title is not None:
        album.title = body.title
    if body.release_year is not None:
        album.release_year = body.release_year
    if body.description is not None:
        album.description = body.description
    if body.visibility is not None:
        album.visibility = body.visibility.value

    await db.commit()

    return AlbumResponse(
        id=str(album.id),
        title=album.title,
        artist_id=album.artist_id,
        musicbrainz_id=album.musicbrainz_id,
        release_year=album.release_year,
        cover_url=await _cover_url(storage, album),
        description=album.description,
        owner_id=redact_owner(cast(HasOwnerId, album), current_user),
        visibility=album.visibility,
    )


@router.delete("/{album_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(rate_limit_account)])
async def delete_album(
    album_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    recursive: bool = Query(True, description="Also delete the album's tracks and uploads"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Delete an album and, by default, its tracks and uploads."""
    album = await music.get_album(db, album_id)
    if album is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "album", album_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    admin_deletion = current_user.is_admin and album.owner_id != current_user.id
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="album.admin_delete" if admin_deletion else "album.delete",
        target_type="album",
        target_id=album_id,
        details={
            "title": album.title,
            "recursive": recursive,
            "owner_id": album.owner_id,
        },
        ip_address=client_ip(request),
    )

    try:
        result = await deletion.delete_album(
            db,
            storage,
            album_id,
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
