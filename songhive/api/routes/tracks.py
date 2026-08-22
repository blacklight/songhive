"""
Track routes.
"""

from typing import List, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models._enums import Visibility
from ...models.user import User
from ...services import acl, audit, music
from ...services.storage import StorageService
from .._common import client_ip
from ..deps import (
    get_current_user,
    get_current_user_optional,
    get_db,
    get_storage_service,
    require_access,
)
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
    disc_number: Optional[int] = None
    duration: Optional[float] = None
    genre: Optional[str] = None
    audio_url: Optional[str] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


class TrackUpdate(BaseModel):
    """Track partial update."""

    title: Optional[str] = None
    genre: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    visibility: Optional[Visibility] = None


@router.get("/", response_model=List[TrackResponse])
async def list_tracks(
    q: Optional[str] = Query(None, description="Search query"),
    artist_id: Optional[str] = Query(None),
    album_id: Optional[str] = Query(None),
    genre: Optional[str] = Query(None),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    library_id: Optional[str] = Query(None),
    user: Optional[User] = Depends(get_current_user_optional),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """List or search tracks visible to the requester."""
    rows = await music.list_tracks(
        db,
        query=q,
        artist_id=artist_id,
        album_id=album_id,
        genre=genre,
        year_from=year_from,
        year_to=year_to,
        library_id=library_id,
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
            disc_number=t.disc_number,
            duration=t.duration,
            genre=t.genre,
            audio_url=await storage.get_url(t.audio_file) if t.audio_file_id else None,
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
    storage: StorageService = Depends(get_storage_service),
):
    """Get a track by ID."""
    track = await music.get_track(db, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return TrackResponse(
        id=str(track.id),
        title=track.title,
        artist_id=track.artist_id,
        album_id=track.album_id,
        track_number=track.track_number,
        disc_number=track.disc_number,
        duration=track.duration,
        genre=track.genre,
        audio_url=await storage.get_url(track.audio_file) if track.audio_file_id else None,
        owner_id=redact_owner(cast(HasOwnerId, track), user),
        visibility=track.visibility,
    )


@router.patch("/{track_id}", response_model=TrackResponse)
async def update_track(
    track_id: str,
    body: TrackUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Partially update a track."""
    track = await music.get_track(db, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "track", track_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if body.title is not None:
        track.title = body.title
    if body.genre is not None:
        track.genre = body.genre
    if body.track_number is not None:
        track.track_number = body.track_number
    if body.disc_number is not None:
        track.disc_number = body.disc_number
    if body.visibility is not None:
        track.visibility = body.visibility.value

    await db.commit()

    return TrackResponse(
        id=str(track.id),
        title=track.title,
        artist_id=track.artist_id,
        album_id=track.album_id,
        track_number=track.track_number,
        disc_number=track.disc_number,
        duration=track.duration,
        genre=track.genre,
        audio_url=await storage.get_url(track.audio_file) if track.audio_file_id else None,
        owner_id=redact_owner(cast(HasOwnerId, track), current_user),
        visibility=track.visibility,
    )


@router.delete("/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_track(
    track_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a track and its library memberships."""
    track = await music.get_track(db, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "track", track_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if current_user.is_admin and track.owner_id != current_user.id:
        await audit.log_action(
            db,
            actor_id=current_user.id,
            action="track.admin_delete",
            target_type="track",
            target_id=track_id,
            details={"title": track.title, "owner_id": track.owner_id},
            ip_address=client_ip(request),
        )

    await db.delete(track)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
