"""
Track routes.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, cast

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

from ...models import Track
from ...models._enums import Visibility
from ...models.user import User
from ...services import acl, audit, deletion, music
from ...services.auth import get_user_by_id
from ...services.federation import publish_track_activity, unpublish_track_activity
from ...services.storage import StorageService
from .._common import Pagination, client_ip, get_pagination
from .._include import IncludeQuery, get_include
from .._sorting import SortParams, get_sort
from ..deps import (
    get_current_user,
    get_current_user_optional,
    get_db,
    get_storage_service,
    require_access,
)
from ..middleware.rate_limit import rate_limit_account
from ..responses import (
    AlbumSummary,
    ArtistSummary,
    UserSummary,
    _track_image_url,
    _track_release_year,
    build_album_summary,
    build_artist_summary,
    build_user_summary,
)
from ._common import HasOwnerId, redact_owner
from ._images import remove_entity_image, upload_entity_image

router = APIRouter(prefix="/tracks")
logger = logging.getLogger(__name__)


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
    image_url: Optional[str] = None
    release_year: Optional[int] = None
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value
    artist: Optional[ArtistSummary] = None
    album: Optional[AlbumSummary] = None
    owner: Optional[UserSummary] = None


class TrackUpdate(BaseModel):
    """Track partial update."""

    title: Optional[str] = None
    artist_name: Optional[str] = None
    album_title: Optional[str] = None
    genre: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    release_year: Optional[int] = None
    visibility: Optional[Visibility] = None


class BulkTrackDeleteRequest(BaseModel):
    """Bulk track deletion payload."""

    track_ids: List[str]


class BulkTrackDeleteResponse(BaseModel):
    """Bulk track deletion result."""

    deleted: int
    track_ids: List[str]


class TrackEnrichResponse(BaseModel):
    """Track metadata enrichment result."""

    track_id: str
    enqueued: bool


def _enqueue_track_enrichment(track_id: str, force: bool = True) -> bool:
    """Enqueue a MusicBrainz enrichment task and ignore broker errors."""
    try:
        from ...tasks.musicbrainz import enrich_track

        enrich_track.delay(track_id, force=force)  # type: ignore
        return True
    except Exception as exc:
        logger.warning("Could not enqueue MusicBrainz enrichment for %s: %s", track_id, exc)
        return False


_TAG_SYNC_FIELDS = {
    "title",
    "artist_name",
    "album_title",
    "genre",
    "track_number",
    "disc_number",
    "release_year",
}


def _should_sync_tags(body: TrackUpdate) -> bool:
    """Return True when the update payload contains tag-relevant metadata."""
    return any(field in body.model_dump(exclude_unset=True) for field in _TAG_SYNC_FIELDS)


def _enqueue_track_tag_sync(track_id: str) -> bool:
    """Enqueue a tag-sync task and ignore broker errors."""
    try:
        from ...tasks.tags import sync_track_tags

        sync_track_tags.delay(track_id)  # type: ignore
        return True
    except Exception as exc:
        logger.warning("Could not enqueue tag sync for %s: %s", track_id, exc)
        return False


async def _handle_visibility_changes(
    track: Track,
    previous_visibility: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
):
    """Partially update a track."""
    if (
        previous_visibility != Visibility.PUBLIC.value
        and track.visibility == Visibility.PUBLIC.value
        and track.owner_id
    ):
        owner = await get_user_by_id(db, track.owner_id)
        artist = track.artist
        if owner is not None and artist is not None:
            if not track.federation_object_id:
                track.federation_object_id = str(uuid.uuid4())
                await db.commit()
            background_tasks.add_task(
                publish_track_activity,
                track,
                artist,
                owner,
                request.app.state.config,
                track.federation_object_id,
            )

    if (
        previous_visibility == Visibility.PUBLIC.value
        and track.visibility != Visibility.PUBLIC.value
        and track.owner_id
    ):
        owner = await get_user_by_id(db, track.owner_id)
        artist = track.artist
        if owner is not None and artist is not None:
            object_id = track.federation_object_id
            background_tasks.add_task(
                unpublish_track_activity,
                track,
                artist,
                owner,
                request.app.state.config,
                object_id,
            )
            track.federation_object_id = None
            await db.commit()


async def _build_track_response(
    track,
    user: Optional[User],
    storage: StorageService,
    include: IncludeQuery,
) -> TrackResponse:
    """Build a TrackResponse with optional nested summaries."""
    artist = None
    if "artist" in include and track.artist:
        artist = await build_artist_summary(track.artist, storage)
    album = None
    if "album" in include and track.album:
        album = await build_album_summary(track.album, storage)
    owner = None
    owner_id = redact_owner(cast(HasOwnerId, track), user)
    if "owner" in include and owner_id is not None and track.owner:
        owner = await build_user_summary(track.owner)

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
        image_url=await _track_image_url(track, storage),
        release_year=_track_release_year(track),
        owner_id=owner_id,
        visibility=track.visibility,
        artist=artist,
        album=album,
        owner=owner,
    )


@router.get("/", response_model=List[TrackResponse])
async def list_tracks(
    response: Response,
    q: Optional[str] = Query(None, description="Search query"),
    artist_id: Optional[str] = Query(None),
    album_id: Optional[str] = Query(None),
    genre: Optional[str] = Query(None),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    library_id: Optional[str] = Query(None),
    favorited: Optional[bool] = Query(None, description="Only return tracks favorited by the current user"),
    around_track_id: Optional[str] = Query(None, description="Center the returned chunk on this track"),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(
        get_sort(
            {"created_at", "title", "artist_name", "album_title", "updated_at", "release_year"},
            "created_at",
            "desc",
        )
    ),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "album", "owner"})),
):
    """List or search tracks visible to the requester."""
    total = await music.count_tracks(
        db,
        query=q,
        artist_id=artist_id,
        album_id=album_id,
        genre=genre,
        year_from=year_from,
        year_to=year_to,
        library_id=library_id,
        user=user,
        favorited=favorited,
    )
    rows, effective_offset = await music.list_tracks(
        db,
        query=q,
        artist_id=artist_id,
        album_id=album_id,
        genre=genre,
        year_from=year_from,
        year_to=year_to,
        library_id=library_id,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        include=set(include.values),
        around_track_id=around_track_id,
        sort_by=sort.field,
        sort_dir=sort.direction,
        favorited=favorited,
    )
    pagination.set_total(response, total)
    response.headers["X-List-Offset"] = str(effective_offset)
    return [await _build_track_response(t, user, storage, include) for t in rows]


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
    include: IncludeQuery = Depends(get_include({"artist", "album", "owner"})),
):
    """Get a track by ID."""
    track = await music.get_track(db, track_id, include=set(include.values))
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return await _build_track_response(track, user, storage, include)


@router.patch("/{track_id}", response_model=TrackResponse)
async def update_track(
    track_id: str,
    body: TrackUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "album", "owner"})),
):
    """Partially update a track."""
    service_include = set(include.values) | {"artist"}
    track = await music.get_track(db, track_id, include=service_include)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "track", track_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    previous_artist_id = track.artist_id
    previous_album_id = track.album_id

    previous_visibility = track.visibility
    if body.title is not None:
        track.title = body.title

    artist = None
    if body.artist_name is not None:
        name = (body.artist_name or "").strip()
        if name:
            artist = await music.find_or_create_artist(db, name)
            track.artist_id = str(artist.id)

    album = None
    if body.album_title is not None:
        title = (body.album_title or "").strip()
        if title:
            album = await music.find_or_create_album(
                db,
                title=title,
                artist_id=track.artist_id,
                owner_id=track.owner_id,
                visibility=track.visibility,
            )
            track.album_id = str(album.id)
        else:
            track.album_id = None

    if body.genre is not None:
        track.genre = body.genre
    if body.track_number is not None:
        track.track_number = body.track_number
    if body.disc_number is not None:
        track.disc_number = body.disc_number
    if body.release_year is not None:
        track.release_year = body.release_year
    if body.visibility is not None:
        track.visibility = body.visibility.value

    if body.artist_name is not None or body.album_title is not None:
        await db.flush()
        await db.refresh(track, ["artist", "album"])
        await deletion.cleanup_empty_artist_and_album(
            db,
            storage,
            previous_artist_id,
            previous_album_id,
        )

    details: Dict[str, Any] = {
        "title": track.title,
        "release_year": track.release_year,
        "visibility": track.visibility,
    }
    if body.artist_name is not None:
        details["artist_name"] = artist.name if artist is not None else None
    if body.album_title is not None:
        details["album_title"] = album.title if album is not None else None

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="track.update",
        target_type="track",
        target_id=track_id,
        details=details,
        ip_address=client_ip(request),
    )
    await db.commit()
    await _handle_visibility_changes(
        track, previous_visibility=previous_visibility, request=request, background_tasks=background_tasks, db=db
    )

    if _should_sync_tags(body):
        _enqueue_track_tag_sync(track_id)

    return await _build_track_response(track, current_user, storage, include)


@router.post("/{track_id}/image", response_model=TrackResponse)
async def upload_track_image(
    track_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "album", "owner"})),
):
    """Upload a track image."""
    track = await music.get_track(db, track_id, include=set(include.values) | {"artist"})
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "track", track_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        track,
        "image_file_id",
        file,
        current_user,
        owner_id=track.owner_id,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="track.update",
        target_type="track",
        target_id=track_id,
        details={"image_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()
    _enqueue_track_tag_sync(track_id)

    return await _build_track_response(track, current_user, storage, include)


@router.delete("/{track_id}/image", response_model=TrackResponse)
async def delete_track_image(
    track_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "album", "owner"})),
):
    """Remove a track image."""
    track = await music.get_track(db, track_id, include=set(include.values) | {"artist"})
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "track", track_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(track, "image_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="track.update",
        target_type="track",
        target_id=track_id,
        details={"image_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()
    _enqueue_track_tag_sync(track_id)

    return await _build_track_response(track, current_user, storage, include)


@router.delete("/bulk", status_code=status.HTTP_200_OK, dependencies=[Depends(rate_limit_account)])
async def delete_tracks_bulk_route(
    body: BulkTrackDeleteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Delete multiple tracks in a single request."""
    try:
        unpublish, deleted_ids = await deletion.delete_tracks_bulk(
            db,
            storage,
            body.track_ids,
            user=current_user,
        )
    except deletion.DeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.args[0]) from exc

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="track.bulk_delete",
        target_type="track",
        target_id=None,
        details={
            "count": len(deleted_ids),
            "track_ids": deleted_ids,
            "admin_deletion": current_user.is_admin,
        },
        ip_address=client_ip(request),
    )

    await db.commit()

    for info in unpublish:
        if info.owner is not None and info.artist is not None:
            background_tasks.add_task(
                unpublish_track_activity,
                info.track,
                info.artist,
                info.owner,
                request.app.state.config,
                info.federation_object_id,
            )

    return BulkTrackDeleteResponse(deleted=len(deleted_ids), track_ids=deleted_ids)


@router.delete("/{track_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(rate_limit_account)])
async def delete_track(
    track_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Delete a track, its upload, and all playlist/library memberships."""
    track = await music.get_track(db, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "track", track_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    admin_deletion = current_user.is_admin and track.owner_id != current_user.id
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="track.admin_delete" if admin_deletion else "track.delete",
        target_type="track",
        target_id=track_id,
        details={"title": track.title, "owner_id": track.owner_id},
        ip_address=client_ip(request),
    )

    try:
        unpublish = await deletion.delete_track(db, storage, track_id)
    except deletion.DeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.args[0]) from exc

    await db.commit()

    if unpublish is not None and unpublish.owner is not None and unpublish.artist is not None:
        background_tasks.add_task(
            unpublish_track_activity,
            unpublish.track,
            unpublish.artist,
            unpublish.owner,
            request.app.state.config,
            unpublish.federation_object_id,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{track_id}/enrich", response_model=TrackEnrichResponse)
async def enrich_track_route(
    track_id: str,
    request: Request,
    _: bool = Depends(require_access("track")),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue MusicBrainz enrichment for a single track."""
    track = await music.get_track(db, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "track", track_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    enqueued = _enqueue_track_enrichment(track_id, force=True)
    if not enqueued:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not enqueue enrichment",
        )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="track.enrich",
        target_type="track",
        target_id=track_id,
        details={"title": track.title, "musicbrainz_id": track.musicbrainz_id},
        ip_address=client_ip(request),
    )
    await db.commit()

    return TrackEnrichResponse(track_id=track_id, enqueued=True)
