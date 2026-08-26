"""
Album routes.
"""

from typing import List, Optional, cast

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

from ...models._enums import Visibility
from ...models.track import Track
from ...models.user import User
from ...services import acl, audit, deletion, music
from ...services.federation import unpublish_track_activity
from ...services.storage import StorageService
from .._common import Pagination, client_ip, get_pagination
from .._include import IncludeQuery, get_include
from ..deps import (
    get_current_user,
    get_current_user_optional,
    get_db,
    get_storage_service,
    require_access,
)
from ..middleware.rate_limit import rate_limit_account
from ..responses import (
    ArtistSummary,
    TrackSummary,
    UserSummary,
    build_artist_summary,
    build_track_summary,
    build_user_summary,
)
from ._common import HasOwnerId, redact_owner
from ._images import remove_entity_image, upload_entity_image
from .tracks import _enqueue_track_enrichment

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
    artist: Optional[ArtistSummary] = None
    owner: Optional[UserSummary] = None
    tracks: Optional[List[TrackSummary]] = None


class AlbumUpdate(BaseModel):
    """Album partial update."""

    title: Optional[str] = None
    release_year: Optional[int] = None
    description: Optional[str] = None
    visibility: Optional[Visibility] = None


class AlbumEnrichResponse(BaseModel):
    """Album metadata enrichment result."""

    album_id: str
    enqueued: int


async def _cover_url(storage: StorageService, album) -> Optional[str]:
    """Resolve an album's cover URL from its stored cover file if available."""
    if album.cover_file_id and album.cover_file:
        return await storage.get_url(album.cover_file)
    return album.cover_url


def _album_track_sort_key(t: Track):
    """Return a sort key for an album's tracks."""
    return (t.disc_number or 0, t.track_number or 0, t.created_at)


async def _build_album_response(
    album,
    user: Optional[User],
    storage: StorageService,
    include: IncludeQuery,
) -> AlbumResponse:
    """Build an AlbumResponse with optional nested summaries."""
    artist = None
    if "artist" in include and album.artist:
        artist = await build_artist_summary(album.artist, storage)
    owner = None
    owner_id = redact_owner(cast(HasOwnerId, album), user)
    if "owner" in include and owner_id is not None and album.owner:
        owner = await build_user_summary(album.owner)
    tracks = None
    if "tracks" in include:
        album_tracks = getattr(album, "tracks", None)
        if album_tracks is not None:
            track_list: List[TrackSummary] = []
            for t in sorted(album_tracks, key=_album_track_sort_key):
                summary = await build_track_summary(t, storage)
                if summary is not None:
                    track_list.append(summary)
            tracks = track_list

    return AlbumResponse(
        id=str(album.id),
        title=album.title,
        artist_id=album.artist_id,
        musicbrainz_id=album.musicbrainz_id,
        release_year=album.release_year,
        cover_url=await _cover_url(storage, album),
        description=album.description,
        owner_id=owner_id,
        visibility=album.visibility,
        artist=artist,
        owner=owner,
        tracks=tracks,
    )


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
    include: IncludeQuery = Depends(get_include({"artist", "owner", "tracks"})),
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
        include=set(include.values),
    )
    pagination.set_total(response, total)
    return [await _build_album_response(a, user, storage, include) for a in rows]


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
    include: IncludeQuery = Depends(get_include({"artist", "owner", "tracks"})),
):
    """Get an album by ID."""
    album = await music.get_album(db, album_id, include=set(include.values))
    if album is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return await _build_album_response(album, user, storage, include)


@router.patch("/{album_id}", response_model=AlbumResponse)
async def update_album(
    album_id: str,
    body: AlbumUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "owner", "tracks"})),
):
    """Partially update an album."""
    album = await music.get_album(db, album_id, include=set(include.values))
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

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="album.update",
        target_type="album",
        target_id=album_id,
        details={
            "title": album.title,
            "release_year": album.release_year,
            "visibility": album.visibility,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_album_response(album, current_user, storage, include)


@router.post("/{album_id}/cover", response_model=AlbumResponse)
async def upload_album_cover(
    album_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "owner", "tracks"})),
):
    """Upload album cover art."""
    album = await music.get_album(db, album_id, include=set(include.values))
    if album is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "album", album_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        album,
        "cover_file_id",
        file,
        current_user,
        owner_id=album.owner_id,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="album.update",
        target_type="album",
        target_id=album_id,
        details={"cover_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_album_response(album, current_user, storage, include)


@router.delete("/{album_id}/cover", response_model=AlbumResponse)
async def delete_album_cover(
    album_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "owner", "tracks"})),
):
    """Remove album cover art."""
    album = await music.get_album(db, album_id, include=set(include.values))
    if album is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "album", album_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(album, "cover_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="album.update",
        target_type="album",
        target_id=album_id,
        details={"cover_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_album_response(album, current_user, storage, include)


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


@router.post("/{album_id}/enrich", response_model=AlbumEnrichResponse)
async def enrich_album(
    album_id: str,
    request: Request,
    _: bool = Depends(require_access("album")),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue MusicBrainz enrichment for all tracks in an album."""
    album = await music.get_album(db, album_id)
    if album is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "album", album_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    track_ids = await music.get_track_ids_for_album(db, album_id, user=current_user)
    enqueued = 0
    for track_id in track_ids:
        track = await db.get(Track, track_id)
        if track is None:
            continue
        if (current_user.is_admin or track.owner_id == current_user.id) and _enqueue_track_enrichment(
            track_id, force=True
        ):
            enqueued += 1

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="album.enrich",
        target_type="album",
        target_id=album_id,
        details={"title": album.title, "enqueued": enqueued},
        ip_address=client_ip(request),
    )
    await db.commit()

    return AlbumEnrichResponse(album_id=album_id, enqueued=enqueued)
