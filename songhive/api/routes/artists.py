"""
Artist routes.
"""

from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User
from ...services import acl, audit, deletion, music
from ...services.federation import unpublish_track_activity
from ...services.storage import StorageService
from .._common import Pagination, client_ip, get_pagination
from .._include import IncludeQuery, get_include
from .._sorting import SortParams, get_sort
from ..deps import get_current_user, get_db, get_storage_service
from ..middleware.rate_limit import rate_limit_account
from ..responses import (
    AlbumSummary,
    TrackSummary,
    build_album_summary,
    build_track_summary,
)
from ._images import remove_entity_image, upload_entity_image
from .tracks import _enqueue_track_tag_sync

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
    cover_url: Optional[str] = None
    albums: Optional[List[AlbumSummary]] = None
    tracks: Optional[List[TrackSummary]] = None


class ArtistUpdate(BaseModel):
    """Artist partial update."""

    name: Optional[str] = None
    bio: Optional[str] = None


async def _image_url(artist, storage: StorageService) -> Optional[str]:
    """Resolve an artist image URL from a stored file or remote URL."""
    if artist.image_file_id and artist.image_file:
        return await storage.get_url(artist.image_file)
    return artist.image_url


async def _cover_url(artist, storage: StorageService) -> Optional[str]:
    """Resolve an artist cover art URL from a stored file."""
    if artist.cover_file_id and artist.cover_file:
        return await storage.get_url(artist.cover_file)
    return None


def _artist_album_sort_key(album):
    """Return a sort key for an artist's albums."""
    return (album.release_year or 0, album.created_at)


def _artist_track_sort_key(track):
    """Return a sort key for an artist's tracks."""
    return (track.album_id or "", track.disc_number or 0, track.track_number or 0, track.created_at)


async def _build_artist_response(
    artist,
    storage: StorageService,
    include: IncludeQuery,
) -> ArtistResponse:
    """Build an ArtistResponse with optional nested summaries."""
    albums = None
    if "albums" in include:
        artist_albums = getattr(artist, "albums", None)
        if artist_albums is not None:
            album_list: List[AlbumSummary] = []
            for a in sorted(artist_albums, key=_artist_album_sort_key):
                album_summary = await build_album_summary(a, storage)
                if album_summary is not None:
                    album_list.append(album_summary)
            albums = album_list
    tracks = None
    if "tracks" in include:
        artist_tracks = getattr(artist, "tracks", None)
        if artist_tracks is not None:
            track_list: List[TrackSummary] = []
            for t in sorted(artist_tracks, key=_artist_track_sort_key):
                track_summary = await build_track_summary(t, storage)
                if track_summary is not None:
                    track_list.append(track_summary)
            tracks = track_list

    return ArtistResponse(
        id=str(artist.id),
        name=artist.name,
        musicbrainz_id=artist.musicbrainz_id,
        bio=artist.bio,
        image_file_id=artist.image_file_id,
        image_url=await _image_url(artist, storage),
        cover_url=await _cover_url(artist, storage),
        albums=albums,
        tracks=tracks,
    )


@router.get("/", response_model=List[ArtistResponse])
async def list_artists(
    response: Response,
    q: Optional[str] = Query(None, description="Search query"),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"name", "created_at", "updated_at"}, "name")),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"albums", "tracks"})),
):
    """List or search artists."""
    total = await music.count_artists(db, query=q)
    rows = await music.list_artists(
        db,
        query=q,
        limit=pagination.limit,
        offset=pagination.offset,
        include=set(include.values),
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    return [await _build_artist_response(a, storage, include) for a in rows]


@router.get("/{artist_id}", response_model=ArtistResponse)
async def get_artist(
    artist_id: str,
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"albums", "tracks"})),
):
    """Get an artist by ID."""
    artist = await music.get_artist(db, artist_id, include=set(include.values))
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return await _build_artist_response(artist, storage, include)


@router.patch("/{artist_id}", response_model=ArtistResponse)
async def update_artist(
    artist_id: str,
    body: ArtistUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"albums", "tracks"})),
):
    """Partially update an artist."""
    artist = await music.get_artist(db, artist_id, include=set(include.values))
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "artist", artist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if body.name is not None:
        artist.name = body.name
    if body.bio is not None:
        artist.bio = body.bio

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="artist.update",
        target_type="artist",
        target_id=artist_id,
        details={
            "name": artist.name,
            "image_file_id": artist.image_file_id,
            "cover_file_id": artist.cover_file_id,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    if "name" in body.model_dump(exclude_unset=True):
        track_ids = await music.get_track_ids_for_artist(db, artist_id, user=current_user)
        for track_id in track_ids:
            _enqueue_track_tag_sync(track_id)

    return await _build_artist_response(artist, storage, include)


@router.post("/{artist_id}/image", response_model=ArtistResponse)
async def upload_artist_image(
    artist_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"albums", "tracks"})),
):
    """Upload an artist image."""
    artist = await music.get_artist(db, artist_id, include=set(include.values))
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "artist", artist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        artist,
        "image_file_id",
        file,
        current_user,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="artist.update",
        target_type="artist",
        target_id=artist_id,
        details={"image_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_artist_response(artist, storage, include)


@router.post("/{artist_id}/cover", response_model=ArtistResponse)
async def upload_artist_cover(
    artist_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"albums", "tracks"})),
):
    """Upload artist cover art."""
    artist = await music.get_artist(db, artist_id, include=set(include.values))
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "artist", artist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        artist,
        "cover_file_id",
        file,
        current_user,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="artist.update",
        target_type="artist",
        target_id=artist_id,
        details={"cover_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_artist_response(artist, storage, include)


@router.delete("/{artist_id}/image", response_model=ArtistResponse)
async def delete_artist_image(
    artist_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"albums", "tracks"})),
):
    """Remove an artist image."""
    artist = await music.get_artist(db, artist_id, include=set(include.values))
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "artist", artist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(artist, "image_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="artist.update",
        target_type="artist",
        target_id=artist_id,
        details={"image_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_artist_response(artist, storage, include)


@router.delete("/{artist_id}/cover", response_model=ArtistResponse)
async def delete_artist_cover(
    artist_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"albums", "tracks"})),
):
    """Remove artist cover art."""
    artist = await music.get_artist(db, artist_id, include=set(include.values))
    if artist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "artist", artist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(artist, "cover_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="artist.update",
        target_type="artist",
        target_id=artist_id,
        details={"cover_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_artist_response(artist, storage, include)


@router.delete("/{artist_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(rate_limit_account)])
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
