"""
Library routes.
"""

import uuid
from pathlib import Path
from typing import List, Optional, cast

from fastapi import (
    APIRouter,
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

from ...models._enums import Visibility
from ...models.library import Library
from ...models.user import User
from ...services import acl, music
from ...services.import_ import DuplicateTrackError, ImportResult, import_audio_file
from ...services.storage import StorageService
from ...tasks.import_ import process_upload, scan_directory
from .._common import Pagination, get_pagination
from ..deps import (
    get_current_user,
    get_current_user_optional,
    get_db,
    get_storage_service,
    require_access,
)
from ._common import HasOwnerId, redact_owner
from .tracks import TrackResponse

router = APIRouter(prefix="/libraries")


class LibraryResponse(BaseModel):
    """Public library response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    owner_id: Optional[str] = None
    description: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value


class LibraryCreate(BaseModel):
    """Library creation payload."""

    name: str
    description: Optional[str] = None


class LibraryUpdate(BaseModel):
    """Library partial update."""

    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[Visibility] = None


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


@router.get("/", response_model=List[LibraryResponse])
async def list_libraries(
    response: Response,
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List libraries visible to the requester."""
    total = await music.count_libraries(db, user=user)
    rows = await music.list_libraries(db, user=user, limit=pagination.limit, offset=pagination.offset)
    pagination.set_total(response, total)
    return [
        LibraryResponse(
            id=str(lib.id),
            name=lib.name,
            owner_id=redact_owner(cast(HasOwnerId, lib), user),
            description=lib.description,
            visibility=lib.visibility,
        )
        for lib in rows
    ]


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
):
    """Get a library by ID."""
    library = await music.get_library(db, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return LibraryResponse(
        id=str(library.id),
        name=library.name,
        owner_id=redact_owner(cast(HasOwnerId, library), user),
        description=library.description,
        visibility=library.visibility,
    )


async def _track_response(
    storage: StorageService,
    track,
    user: Optional[User],
) -> TrackResponse:
    """Build a TrackResponse with audio URL."""
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
        owner_id=redact_owner(track, user),
        visibility=track.visibility,
    )


@router.post("/{library_id}/tracks", status_code=201)
async def upload_track(
    library_id: str,
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
        )
        await db.commit()
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

    track_response = await _track_response(storage, result.track, user)
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
        )
        await db.commit()
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
        )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"job_id": job_id, "enqueued": _enqueued + 1},
    )


@router.post("/{library_id}/tracks/bulk")
async def bulk_upload_tracks(
    library_id: str,
    request: Request,
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
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
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
        db, library_id=library_id, user=user, limit=pagination.limit, offset=pagination.offset
    )
    pagination.set_total(response, total)
    return [await _track_response(storage, row, user) for row in rows]


@router.patch("/{library_id}", response_model=LibraryResponse)
async def update_library(
    library_id: str,
    body: LibraryUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Partially update a library."""
    library = await music.get_library(db, library_id)
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

    await db.commit()

    return LibraryResponse(
        id=str(library.id),
        name=library.name,
        owner_id=library.owner_id,
        description=library.description,
        visibility=library.visibility,
    )


@router.delete("/{library_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_library(
    library_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a library and its track memberships."""
    library = await music.get_library(db, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "library", library_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    await db.delete(library)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
