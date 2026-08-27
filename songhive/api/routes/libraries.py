"""
Library routes.
"""

import asyncio
import uuid
from pathlib import Path
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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models._enums import Visibility
from ...models.artist import Artist
from ...models.library import Library
from ...models.user import User
from ...services import acl, audit, deletion, music
from ...services.federation import publish_track_activity, unpublish_track_activity
from ...services.import_ import DuplicateTrackError, ImportResult, import_audio_file
from ...services.storage import StorageService
from ...tasks.import_ import process_upload, scan_directory
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
from ..responses import TrackSummary, UserSummary, build_track_summary, build_user_summary
from ._common import HasOwnerId, redact_owner
from ._images import remove_entity_image, upload_entity_image
from .tracks import TrackResponse, _build_track_response

router = APIRouter(prefix="/libraries")


class LibraryResponse(BaseModel):
    """Public library response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_id: Optional[str] = None
    description: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value
    image_url: Optional[str] = None
    cover_url: Optional[str] = None
    can_write: bool = False
    owner: Optional[UserSummary] = None
    tracks: Optional[List[TrackSummary]] = None


def _can_write_library(user: Optional[User], library: Library) -> bool:
    """Return whether the requester may add tracks to this library."""
    if user is None:
        return False
    return user.is_admin or library.owner_id == user.id


class LibraryCreate(BaseModel):
    """Library creation payload."""

    name: str
    description: Optional[str] = None


class LibraryUpdate(BaseModel):
    """Library partial update."""

    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[Visibility] = None


class AddLibraryTracksRequest(BaseModel):
    """Request body for adding tracks, albums, or artists to a library."""

    track_ids: Optional[List[str]] = None
    album_id: Optional[str] = None
    artist_id: Optional[str] = None


class RemoveLibraryTracksRequest(BaseModel):
    """Request body for removing tracks from a library."""

    track_ids: List[str]


class ScanRequest(BaseModel):
    """Directory scan request."""

    path: str


class BulkUploadResult(BaseModel):
    """Per-file bulk upload result."""

    filename: str
    status: str
    track_id: Optional[str] = None
    upload_id: Optional[str] = None
    existing_track_id: Optional[str] = None
    error: Optional[str] = None


def _library_track_sort_key(track):
    """Return a sort key for a library's tracks."""
    return track.created_at


async def _library_image_url(library: Library, storage: StorageService) -> Optional[str]:
    """Resolve a library's image URL from its stored file."""
    if library.image_file_id and library.image_file:
        return await storage.get_url(library.image_file)
    return None


async def _library_cover_url(library: Library, storage: StorageService) -> Optional[str]:
    """Resolve a library's cover art URL from its stored file."""
    if library.cover_file_id and library.cover_file:
        return await storage.get_url(library.cover_file)
    return None


async def _build_library_response(
    library: Library,
    user: Optional[User],
    storage: StorageService,
    include: IncludeQuery,
) -> LibraryResponse:
    """Build a LibraryResponse with optional nested summaries."""
    owner = None
    owner_id = redact_owner(cast(HasOwnerId, library), user)
    if "owner" in include and owner_id is not None and library.owner:
        owner = await build_user_summary(library.owner)
    tracks = None
    if "tracks" in include and library.tracks is not None:
        track_list: List[TrackSummary] = []
        for t in sorted(library.tracks, key=_library_track_sort_key):
            summary = await build_track_summary(t, storage)
            if summary is not None:
                track_list.append(summary)
        tracks = track_list

    return LibraryResponse(
        id=str(library.id),
        name=library.name,
        owner_id=owner_id,
        description=library.description,
        visibility=library.visibility,
        image_url=await _library_image_url(library, storage),
        cover_url=await _library_cover_url(library, storage),
        can_write=_can_write_library(user, library),
        owner=owner,
        tracks=tracks,
    )


@router.get("/", response_model=List[LibraryResponse])
async def list_libraries(
    response: Response,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    sort: SortParams = Depends(get_sort({"name", "created_at", "updated_at"}, "name")),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """List libraries visible to the requester."""
    total = await music.count_libraries(db, user=user)
    rows = await music.list_libraries(
        db,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        include=set(include.values),
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    return [await _build_library_response(lib, user, storage, include) for lib in rows]


@router.post("/", response_model=LibraryResponse, status_code=201)
async def create_library(
    body: LibraryCreate,
    visibility: Visibility = Query(Visibility.PRIVATE),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new library owned by the current user."""
    library = Library(
        name=body.name,
        owner_id=current_user.id,
        description=body.description,
        visibility=visibility.value,
    )
    db.add(library)
    await db.commit()

    return LibraryResponse(
        id=str(library.id),
        name=library.name,
        owner_id=library.owner_id,
        description=library.description,
        visibility=library.visibility,
        can_write=True,
    )


@router.get(
    "/{library_id}",
    response_model=LibraryResponse,
    dependencies=[Depends(require_access("library"))],
)
async def get_library(
    library_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Get a library by ID."""
    library = await music.get_library(db, library_id, include=set(include.values))
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return await _build_library_response(library, user, storage, include)


async def _track_response(
    storage: StorageService,
    track,
    user: Optional[User],
    include: Optional[IncludeQuery] = None,
) -> TrackResponse:
    """Build a TrackResponse with audio URL."""
    if include is None:
        include = IncludeQuery(values=set())
    return await _build_track_response(track, user, storage, include)


@router.post("/{library_id}/tracks", status_code=201)
async def upload_track(
    library_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    force: bool = Query(False),
    visibility: Visibility = Query(Visibility.PRIVATE),
    enrich: bool = Query(True),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Upload a single audio file into a library."""
    library = await music.get_library(db, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if user is None or not await acl.can_manage(db, user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    try:
        result: ImportResult = await import_audio_file(
            db,
            storage_service=storage,
            file=file.file,
            filename=file.filename or "audio.mp3",
            library_id=library_id,
            owner_id=str(user.id) if user else None,
            visibility=visibility.value,
            force=force,
            enrich=enrich,
            content_type=file.content_type,
        )
        await db.commit()
        if result.track.visibility == Visibility.PUBLIC.value and user is not None:
            result.track.federation_object_id = str(uuid.uuid4())
            await db.commit()
            artist = await db.get(Artist, result.track.artist_id)
            if artist is not None:
                background_tasks.add_task(
                    publish_track_activity,
                    result.track,
                    artist,
                    user,
                    request.app.state.config,
                    result.track.federation_object_id,
                )
    except DuplicateTrackError as exc:
        existing = await music.get_track(db, exc.existing_track_id)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": "duplicate",
                "existing_track_id": exc.existing_track_id,
                "track": ((await _track_response(storage, existing, user)).model_dump() if existing else None),
            },
        )

    track = await music.get_track(db, result.track.id)
    track_response = await _track_response(storage, track, user)
    return {
        "track": track_response,
        "upload_id": str(result.upload.id),
    }


async def _import_single_sync_file(
    db: AsyncSession,
    storage: StorageService,
    upload_file: UploadFile,
    library_id: str,
    user: Optional[User],
    visibility: Visibility,
    force: bool,
    enrich: bool,
    background_tasks: Optional[BackgroundTasks],
    config: SonghiveConfig,
) -> BulkUploadResult:
    """Import a single uploaded audio file and return a per-file result."""
    filename = upload_file.filename or "audio.mp3"
    try:
        result: ImportResult = await import_audio_file(
            db,
            storage_service=storage,
            file=upload_file.file,
            filename=filename,
            library_id=library_id,
            owner_id=str(user.id) if user else None,
            visibility=visibility.value,
            force=force,
            enrich=enrich,
            content_type=upload_file.content_type,
        )
        await db.commit()
        if background_tasks is not None and result.track.visibility == Visibility.PUBLIC.value and user is not None:
            result.track.federation_object_id = str(uuid.uuid4())
            await db.commit()
            artist = await db.get(Artist, result.track.artist_id)
            if artist is not None:
                background_tasks.add_task(
                    publish_track_activity,
                    result.track,
                    artist,
                    user,
                    config,
                    result.track.federation_object_id,
                )
        return BulkUploadResult(
            filename=filename,
            status="created",
            track_id=str(result.track.id),
            upload_id=str(result.upload.id),
        )
    except DuplicateTrackError as exc:
        return BulkUploadResult(
            filename=filename,
            status="duplicate",
            existing_track_id=exc.existing_track_id,
        )
    except Exception as exc:
        return BulkUploadResult(
            filename=filename,
            status="error",
            error=str(exc),
        )


async def _upload_sync_batch(
    db: AsyncSession,
    storage: StorageService,
    files: List[UploadFile],
    library_id: str,
    user: Optional[User],
    visibility: Visibility,
    force: bool,
    enrich: bool,
    background_tasks: BackgroundTasks,
    config: SonghiveConfig,
) -> List[BulkUploadResult]:
    """Import a small batch of audio files synchronously."""
    results: List[BulkUploadResult] = []
    for upload_file in files:
        results.append(
            await _import_single_sync_file(
                db,
                storage,
                upload_file,
                library_id,
                user,
                visibility,
                force,
                enrich,
                background_tasks,
                config,
            )
        )
    return results


async def _upload_async_batch(
    db: AsyncSession,
    storage: StorageService,
    files: List[UploadFile],
    library_id: str,
    user: Optional[User],
    visibility: Visibility,
    force: bool,
    enrich: bool,
) -> JSONResponse:
    """Store a large batch of files and enqueue background processing."""
    stored_files = []
    for upload_file in files:
        content_type = upload_file.content_type or "application/octet-stream"
        stored, _ = await storage.store_file(
            db,
            upload_file.file,
            content_type,
            original_filename=upload_file.filename or "audio.mp3",
            owner_id=str(user.id) if user else None,
            visibility=visibility.value,
            return_duplicate=True,
        )
        stored_files.append((stored, upload_file.filename))
    await db.commit()

    job_id = str(uuid.uuid4())
    _enqueued = -1
    for _enqueued, (stored, upload_filename) in enumerate(stored_files):
        process_upload.delay(  # type: ignore
            library_id,
            str(user.id) if user else None,
            stored_file_id=str(stored.id),
            filename=upload_filename,
            visibility=visibility.value,
            force=force,
            enrich=enrich,
            content_type=stored.content_type,
            source="upload",
        )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"job_id": job_id, "enqueued": _enqueued + 1},
    )


@router.post("/{library_id}/tracks/bulk")
async def bulk_upload_tracks(
    library_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    force: bool = Query(False),
    visibility: Visibility = Query(Visibility.PRIVATE),
    enrich: bool = Query(True),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Upload many audio files into a library."""
    library = await music.get_library(db, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if user is None or not await acl.can_manage(db, user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    threshold = request.app.state.config.imports.bulk_import_sync_threshold
    config = request.app.state.config
    if len(files) <= threshold:
        return await _upload_sync_batch(
            db,
            storage,
            files,
            library_id,
            user,
            visibility,
            force,
            enrich,
            background_tasks,
            config,
        )

    return await _upload_async_batch(
        db,
        storage,
        files,
        library_id,
        user,
        visibility,
        force,
        enrich,
    )


async def _resolve_track_ids(
    db: AsyncSession,
    user: User,
    body: AddLibraryTracksRequest,
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


@router.post("/{library_id}/tracks/add", status_code=status.HTTP_201_CREATED)
async def add_tracks_to_library(
    library_id: str,
    request: Request,
    body: AddLibraryTracksRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add existing tracks, an album, or an artist to a library."""
    library = await music.get_library(db, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_manage(db, current_user, "library", library_id):
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
    added_ids = await music.add_library_tracks(db, library_id, track_ids, added_by_id=current_user.id)

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="library_track.add",
        target_type="library",
        target_id=library_id,
        details={
            "source": body.model_dump(exclude_unset=True),
            "track_ids": added_ids,
            "count": len(added_ids),
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return {"added": len(added_ids), "track_ids": added_ids}


@router.post("/{library_id}/tracks/remove", status_code=status.HTTP_200_OK)
async def remove_tracks_from_library(
    library_id: str,
    request: Request,
    body: RemoveLibraryTracksRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove existing tracks from a library."""
    library = await music.get_library(db, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_manage(db, current_user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if not body.track_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="track_ids must not be empty",
        )

    removed_ids = await music.remove_library_tracks(db, library_id, body.track_ids)

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="library_track.remove",
        target_type="library",
        target_id=library_id,
        details={
            "track_ids": removed_ids,
            "count": len(removed_ids),
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return {"removed": len(removed_ids), "track_ids": removed_ids}


@router.post("/{library_id}/scan")
async def scan_library(
    library_id: str,
    body: ScanRequest,
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a directory scan for audio files."""
    library = await music.get_library(db, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if user is None or not await acl.can_manage(db, user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    config = request.app.state.config
    if not config.imports.scan_roots:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="directory scanning is not configured",
        )

    resolved = Path(body.path).expanduser().resolve()
    allowed = [Path(r).expanduser().resolve() for r in config.imports.scan_roots]
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path outside configured scan roots",
        )

    result = scan_directory.delay(  # type: ignore
        body.path,
        library_id,
        str(user.id) if user else None,
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"task_id": result.id},
    )


@router.get("/{library_id}/tracks", response_model=List[TrackResponse])
async def list_library_tracks_route(
    response: Response,
    library_id: str,
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
    """List tracks that are members of the library."""
    library = await music.get_library(db, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_access(db, user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    total = await music.count_library_tracks(db, library_id=library_id, user=user)
    rows = await music.list_library_tracks(
        db,
        library_id=library_id,
        user=user,
        limit=pagination.limit,
        offset=pagination.offset,
        include=set(include.values),
        sort_by=sort.field,
        sort_dir=sort.direction,
    )
    pagination.set_total(response, total)
    return [await _track_response(storage, row, user, include) for row in rows]


@router.patch("/{library_id}", response_model=LibraryResponse)
async def update_library(
    library_id: str,
    body: LibraryUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Partially update a library."""
    library = await music.get_library(db, library_id, include=set(include.values))
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if body.name is not None:
        library.name = body.name
    if body.description is not None:
        library.description = body.description
    if body.visibility is not None:
        library.visibility = body.visibility.value

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="library.update",
        target_type="library",
        target_id=library_id,
        details={
            "name": library.name,
            "visibility": library.visibility,
            "image_file_id": library.image_file_id,
            "cover_file_id": library.cover_file_id,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_library_response(library, current_user, storage, include)


@router.post("/{library_id}/image", response_model=LibraryResponse)
async def upload_library_image(
    library_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Upload a library image."""
    library = await music.get_library(db, library_id, include=set(include.values))
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        library,
        "image_file_id",
        file,
        current_user,
        owner_id=library.owner_id,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="library.update",
        target_type="library",
        target_id=library_id,
        details={"image_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_library_response(library, current_user, storage, include)


@router.post("/{library_id}/cover", response_model=LibraryResponse)
async def upload_library_cover(
    library_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Upload library cover art."""
    library = await music.get_library(db, library_id, include=set(include.values))
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    stored = await upload_entity_image(
        db,
        storage,
        library,
        "cover_file_id",
        file,
        current_user,
        owner_id=library.owner_id,
    )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="library.update",
        target_type="library",
        target_id=library_id,
        details={"cover_file_id": stored.id},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_library_response(library, current_user, storage, include)


@router.delete("/{library_id}/image", response_model=LibraryResponse)
async def delete_library_image(
    library_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Remove a library image."""
    library = await music.get_library(db, library_id, include=set(include.values))
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(library, "image_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="library.update",
        target_type="library",
        target_id=library_id,
        details={"image_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_library_response(library, current_user, storage, include)


@router.delete("/{library_id}/cover", response_model=LibraryResponse)
async def delete_library_cover(
    library_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    include: IncludeQuery = Depends(get_include({"owner", "tracks"})),
):
    """Remove library cover art."""
    library = await music.get_library(db, library_id, include=set(include.values))
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await remove_entity_image(library, "cover_file_id")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="library.update",
        target_type="library",
        target_id=library_id,
        details={"cover_file_id": None},
        ip_address=client_ip(request),
    )
    await db.commit()

    return await _build_library_response(library, current_user, storage, include)


@router.delete("/{library_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(rate_limit_account)])
async def delete_library(
    library_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    recursive: bool = Query(False, description="Also delete the library's tracks and uploads"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Delete a library and, optionally, its tracks and uploads."""
    library = await music.get_library(db, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    admin_deletion = current_user.is_admin and library.owner_id != current_user.id
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="library.admin_delete" if admin_deletion else "library.delete",
        target_type="library",
        target_id=library_id,
        details={
            "name": library.name,
            "recursive": recursive,
            "owner_id": library.owner_id,
        },
        ip_address=client_ip(request),
    )

    try:
        result = await deletion.delete_library(
            db,
            storage,
            library_id,
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
