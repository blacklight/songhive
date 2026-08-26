"""
Playlist routes.
"""

import asyncio
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
from ...models.playlist import Playlist
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
from ..responses import TrackSummary, UserSummary, build_track_summary, build_user_summary
from ._common import HasOwnerId, redact_owner
from ._images import remove_entity_image, upload_entity_image
from .tracks import TrackResponse, _build_track_response

router = APIRouter(prefix="/playlists")


class PlaylistResponse(BaseModel):
    """Public playlist response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_id: Optional[str] = None
    description: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value
    image_url: Optional[str] = None
    cover_url: Optional[str] = None
    owner: Optional[UserSummary] = None
    tracks: Optional[List[TrackSummary]] = None


class PlaylistCreate(BaseModel):
    """Playlist creation payload."""

    name: str
    description: Optional[str] = None


class PlaylistUpdate(BaseModel):
    """Playlist partial update."""

    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[Visibility] = None


class AddPlaylistTracksRequest(BaseModel):
    """Request body for adding tracks, albums, or artists to a playlist."""

    track_ids: Optional[List[str]] = None
    album_id: Optional[str] = None
    artist_id: Optional[str] = None


class RemovePlaylistTracksRequest(BaseModel):
    """Request body for removing tracks from a playlist."""

    track_ids: List[str]


async def _playlist_image_url(playlist: Playlist, storage: StorageService) -> Optional[str]:
    """Resolve a playlist's image URL from its stored file."""
    if playlist.image_file_id and playlist.image_file:
        return await storage.get_url(playlist.image_file)
    return None


async def _playlist_cover_url(playlist: Playlist, storage: StorageService) -> Optional[str]:
    """Resolve a playlist's cover art URL from its stored file."""
    if playlist.cover_file_id and playlist.cover_file:
        return await storage.get_url(playlist.cover_file)
    return None


async def _build_playlist_response(
    playlist: Playlist,
    user: Optional[User],
    storage: StorageService,
    include: IncludeQuery,
) -> PlaylistResponse:
    """Build a PlaylistResponse with optional nested summaries."""
    owner = None
    owner_id = redact_owner(cast(HasOwnerId, playlist), user)
    if "owner" in include and owner_id is not None and playlist.owner:
        owner = await build_user_summary(playlist.owner)
    tracks = None
    if "tracks" in include:
        playlist_tracks = getattr(playlist, "tracks", None)
        if playlist_tracks is not None:
            track_list: List[TrackSummary] = []
            for pt in sorted(playlist_tracks, key=lambda pt: pt.position):
                summary = await build_track_summary(pt.track, storage)
                if summary is not None:
                    track_list.append(summary)
            tracks = track_list

    return PlaylistResponse(
        id=str(playlist.id),
        name=playlist.name,
        owner_id=owner_id,
        description=playlist.description,
        visibility=playlist.visibility,
        image_url=await _playlist_image_url(playlist, storage),
        cover_url=await _playlist_cover_url(playlist, storage),
        owner=owner,
        tracks=tracks,
    )


@router.get("/", response_model=List[PlaylistResponse])
async def list_playlists(
    response: Response,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """List playlists visible to the requester."""
    total = await music.count_playlists(db, user=user)
    rows = await music.list_playlists(
        db,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        include=set(include.values),
    )
    pagination.set_total(response, total)
    return [await _build_playlist_response(p, user, storage, include) for p in rows]


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
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Get a playlist by ID."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    # ``require_access`` already loads the row and raises 404 when missing.
    assert playlist is not None

    return await _build_playlist_response(playlist, user, storage, include)


@router.patch("/{playlist_id}", response_model=PlaylistResponse)
async def update_playlist(
    playlist_id: str,
    body: PlaylistUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Partially update a playlist."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if body.name is not None:
        playlist.name = body.name
    if body.description is not None:
        playlist.description = body.description
    if body.visibility is not None:
        playlist.visibility = body.visibility.value

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type="playlist",
        target_id=playlist_id,
        details={
            "name": playlist.name,
            "visibility": playlist.visibility,
            "image_file_id": playlist.image_file_id,
            "cover_file_id": playlist.cover_file_id,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_playlist_response(playlist, current_user, storage, include)


@router.post("/{playlist_id}/image", response_model=PlaylistResponse)
async def upload_playlist_image(
    playlist_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Upload a playlist image."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        playlist,
        "image_file_id",
        file,
        current_user,
        owner_id=playlist.owner_id,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type="playlist",
        target_id=playlist_id,
        details={"image_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_playlist_response(playlist, current_user, storage, include)


@router.post("/{playlist_id}/cover", response_model=PlaylistResponse)
async def upload_playlist_cover(
    playlist_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Upload playlist cover art."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        playlist,
        "cover_file_id",
        file,
        current_user,
        owner_id=playlist.owner_id,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type="playlist",
        target_id=playlist_id,
        details={"cover_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_playlist_response(playlist, current_user, storage, include)


@router.delete("/{playlist_id}/image", response_model=PlaylistResponse)
async def delete_playlist_image(
    playlist_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Remove a playlist image."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(playlist, "image_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type="playlist",
        target_id=playlist_id,
        details={"image_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_playlist_response(playlist, current_user, storage, include)


@router.delete("/{playlist_id}/cover", response_model=PlaylistResponse)
async def delete_playlist_cover(
    playlist_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Remove playlist cover art."""
    playlist = await music.get_playlist(db, playlist_id, include=set(include.values))
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(playlist, "cover_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.update",
        target_type="playlist",
        target_id=playlist_id,
        details={"cover_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_playlist_response(playlist, current_user, storage, include)


async def _resolve_track_ids(
    db: AsyncSession,
    user: User,
    body: AddPlaylistTracksRequest,
) -> List[str]:
    """Resolve ``track_ids`` / ``album_id`` / ``artist_id`` into accessible track IDs."""
    resolved: List[str] = []
    resolved_promises = []
    if body.track_ids:
        for track_id in body.track_ids:
            resolved_promises.append(acl.can_access(db, user, "track", track_id))
        access_results = await asyncio.gather(*resolved_promises)
        resolved.extend([track_id for track_id, can_access in zip(body.track_ids, access_results) if can_access])

    if body.album_id:
        resolved.extend(await music.get_track_ids_for_album(db, body.album_id, user=user))
    if body.artist_id:
        resolved.extend(await music.get_track_ids_for_artist(db, body.artist_id, user=user))

    seen: set[str] = set()
    deduped: List[str] = []
    for track_id in resolved:
        if track_id not in seen:
            seen.add(track_id)
            deduped.append(track_id)
    return deduped


async def _track_response(
    storage: StorageService,
    track,
    user: Optional[User],
    include: IncludeQuery,
) -> TrackResponse:
    """Build a TrackResponse with audio URL."""
    return await _build_track_response(track, user, storage, include)


@router.post("/{playlist_id}/tracks", status_code=status.HTTP_201_CREATED)
async def add_tracks_to_playlist(
    playlist_id: str,
    request: Request,
    body: AddPlaylistTracksRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add existing tracks, an album, or an artist to a playlist."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if not body.track_ids and not body.album_id and not body.artist_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one source must be provided",
        )

    track_ids = await _resolve_track_ids(db, current_user, body)
    added_ids = await music.add_playlist_tracks(db, playlist_id, track_ids)

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist_track.add",
        target_type="playlist",
        target_id=playlist_id,
        details={
            "source": body.model_dump(exclude_unset=True),
            "track_ids": added_ids,
            "count": len(added_ids),
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return {"added": len(added_ids), "track_ids": added_ids}


@router.get(
    "/{playlist_id}/tracks",
    response_model=List[TrackResponse],
    dependencies=[Depends(require_access("playlist"))],
)
async def list_playlist_tracks_route(
    response: Response,
    playlist_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"artist", "album", "owner"})),
):
    """List tracks that are members of the playlist."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    total = await music.count_playlist_tracks(db, playlist_id=playlist_id, user=user)
    rows = await music.list_playlist_tracks(
        db,
        playlist_id=playlist_id,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        include=set(include.values),
    )
    pagination.set_total(response, total)
    return [await _track_response(storage, row, user, include) for row in rows]


@router.post("/{playlist_id}/tracks/remove", status_code=status.HTTP_200_OK)
async def remove_tracks_from_playlist(
    playlist_id: str,
    request: Request,
    body: RemovePlaylistTracksRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove existing tracks from a playlist."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if not body.track_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="track_ids must not be empty",
        )

    removed_count, removed_ids = await music.remove_playlist_tracks(db, playlist_id, body.track_ids)

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist_track.remove",
        target_type="playlist",
        target_id=playlist_id,
        details={
            "track_ids": removed_ids,
            "count": removed_count,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return {"removed": removed_count, "track_ids": removed_ids}


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(rate_limit_account)])
async def delete_playlist(
    playlist_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    recursive: bool = Query(False, description="Also delete the playlist's tracks and uploads"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Delete a playlist and, optionally, its tracks."""
    playlist = await music.get_playlist(db, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "playlist", playlist_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    admin_deletion = current_user.is_admin and playlist.owner_id != current_user.id
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="playlist.admin_delete" if admin_deletion else "playlist.delete",
        target_type="playlist",
        target_id=playlist_id,
        details={
            "name": playlist.name,
            "recursive": recursive,
            "owner_id": playlist.owner_id,
        },
        ip_address=client_ip(request),
    )

    try:
        result = await deletion.delete_playlist(
            db,
            storage,
            playlist_id,
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
