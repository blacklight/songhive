"""
File storage routes: upload, metadata, and download.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, List, Literal, Optional, Union, cast

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
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models._enums import Visibility
from ...models.library import Library
from ...models.stored_file import StoredFile
from ...models.user import User
from ...services import acl, audit, deletion, music
from ...services.federation import unpublish_track_activity
from ...services.import_ import (
    DuplicateTrackError,
    ExternalDuplicateError,
    ExternalDuplicatePermissionError,
    ExternalDuplicateTokenError,
    import_audio_file,
    resolve_external_duplicate,
)
from ...services.storage import StorageService, count_files, list_files
from ...storage import FileSizeLimitExceededError
from .._common import Pagination, client_ip, get_pagination
from .._include import IncludeQuery
from ..deps import (
    get_config,
    get_current_user,
    get_current_user_optional,
    get_db,
    get_redis,
    get_storage_service,
    require_access,
)
from ..middleware.rate_limit import rate_limit, rate_limit_account
from ..responses import TrackResponse, TrackSummary, build_track_summary
from ._common import HasOwnerId, redact_owner
from .external_libraries import (
    ExternalDuplicateResolutionRequest,
    ExternalDuplicateWarning,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files")

_DEFAULT_UPLOADS_LIBRARY_NAME = "Uploads"


async def _get_or_create_uploads_library(session: AsyncSession, user: User) -> Library:
    """Return a user's default uploads library, creating it if necessary."""
    result = await session.execute(
        select(Library).where(Library.owner_id == user.id, Library.name == _DEFAULT_UPLOADS_LIBRARY_NAME).limit(1)
    )
    library = cast(Optional[Library], result.scalar_one_or_none())
    if library is not None:
        return library

    library = Library(
        name=_DEFAULT_UPLOADS_LIBRARY_NAME,
        owner_id=user.id,
        visibility=Visibility.PRIVATE.value,
    )
    session.add(library)
    await session.flush()
    return library


async def _get_target_library(
    session: AsyncSession,
    user: User,
    library_id: Optional[str],
) -> Library:
    """Resolve the library to import audio into, or fall back to Uploads."""
    if library_id is None:
        return await _get_or_create_uploads_library(session, user)

    library = await music.get_library(session, library_id)
    if library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Library not found")
    if not await acl.can_manage(session, user, "library", library_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return library


class StoredFileResponse(BaseModel):
    """Public metadata for a stored file."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    content_type: str
    size: int
    sha256: str
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value
    original_filename: Optional[str] = None
    url: str
    tracks: Optional[List[TrackSummary]] = None


class BulkFileUploadResult(BaseModel):
    """Per-file result for a bulk file upload."""

    model_config = ConfigDict(from_attributes=True)

    filename: Optional[str] = None
    stored_file: Optional[StoredFileResponse] = None
    track_id: Optional[str] = None
    duplicate: bool = False
    error: Optional[str] = None
    status: Optional[str] = None
    external_duplicate: Optional[ExternalDuplicateWarning] = None


@dataclass
class _UploadOutcome:
    """Internal result of uploading a single file."""

    stored_file: Optional[StoredFile] = None
    track_id: Optional[str] = None
    is_duplicate: bool = False
    error: Optional[str] = None
    external_duplicate: Optional[ExternalDuplicateWarning] = None


def _discover_upload_size(file: BinaryIO) -> Optional[int]:
    """Best-effort discovery of the size of an uploaded file stream."""
    try:
        current = file.tell()
        file.seek(0, 2)
        size = file.tell()
        file.seek(current)
        return size
    except (OSError, AttributeError):
        return None


async def _build_stored_file_response(
    stored_file: StoredFile,
    current_user: User,
    storage: StorageService,
) -> StoredFileResponse:
    """Build a ``StoredFileResponse`` for a stored file."""
    return StoredFileResponse(
        id=stored_file.id,
        content_type=stored_file.content_type,
        size=stored_file.size,
        sha256=stored_file.sha256,
        owner_id=redact_owner(cast(HasOwnerId, stored_file), current_user),
        visibility=stored_file.visibility,
        original_filename=stored_file.original_filename,
        url=await storage.get_url(stored_file),
    )


async def _process_single_upload(
    db: AsyncSession,
    storage: StorageService,
    current_user: User,
    library: Optional[Library],
    file: UploadFile,
    visibility: str,
    external_duplicate_action: Optional[Literal["keep_local", "discard_upload"]] = None,
    redis: Optional[Redis] = None,
) -> _UploadOutcome:
    """Store or import a single uploaded file and return the outcome."""
    content_type = file.content_type or "application/octet-stream"
    stored_file: Optional[StoredFile] = None
    is_duplicate = False
    track_id: Optional[str] = None

    if content_type.startswith("audio/"):
        if library is None:
            library = await _get_or_create_uploads_library(db, current_user)
        try:
            result = await import_audio_file(
                db,
                storage_service=storage,
                file=file.file,
                filename=file.filename or "audio",
                library_id=str(library.id),
                owner_id=str(current_user.id),
                visibility=visibility,
                source="upload",
                content_type=content_type,
                external_duplicate_action=external_duplicate_action,
                redis=redis,
            )
            stored_file = result.stored_file
            is_duplicate = result.was_duplicate
            track_id = str(result.track.id) if result.track else None
        except ExternalDuplicateError as exc:
            provider_type = ""
            if exc.display_infos:
                provider_type = exc.display_infos[0].get("provider_type", "")
            warning = ExternalDuplicateWarning(
                token=exc.token or "",
                sha256=exc.sha256,
                provider_type=provider_type,
                display_info=exc.display_infos,
            )
            return _UploadOutcome(external_duplicate=warning)
        except DuplicateTrackError as exc:
            track_id = exc.existing_track_id
            if exc.stored_file_id is not None:
                stored_file = await db.get(StoredFile, exc.stored_file_id)
            is_duplicate = exc.was_duplicate
        except FileSizeLimitExceededError:
            return _UploadOutcome(error="File too large")
        except Exception as exc:
            logger.warning("Could not import audio file as track: %s", exc)

    if stored_file is None and track_id is None:
        storage._rewind(file.file)
        try:
            stored_file, is_duplicate = await storage.store_file(
                db,
                file.file,
                content_type=content_type,
                original_filename=file.filename,
                owner_id=current_user.id,
                visibility=visibility,
                return_duplicate=True,
            )
        except FileSizeLimitExceededError:
            return _UploadOutcome(error="File too large")

    return _UploadOutcome(
        stored_file=stored_file,
        track_id=track_id,
        is_duplicate=is_duplicate,
    )


@router.post(
    "/upload",
    response_model=Union[StoredFileResponse, TrackResponse],
    dependencies=[Depends(rate_limit)],
)
async def upload_file(
    response: Response,
    file: UploadFile,
    visibility: Visibility = Query(Visibility.PRIVATE),
    library_id: Optional[str] = Query(None),
    external_duplicate_action: Optional[Literal["keep_local", "discard_upload"]] = Query(None),
    current_user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """Upload a file and store it in the configured backend.

    Content-addressable deduplication returns the canonical ``StoredFile`` when
    the same bytes have already been uploaded. In that case the response
    includes an ``X-Duplicate: true`` header and the caller's ``owner_id`` and
    ``visibility`` are ignored; they only apply to newly created rows.

    Audio files are imported directly through ``import_audio_file`` so the
    audio-only content hash is used for both the stored file and the track.
    This avoids creating a second full-file ``StoredFile`` row for the same
    audio upload.
    """
    library: Optional[Library] = None
    if library_id is not None:
        library = await _get_target_library(db, current_user, library_id)

    outcome = await _process_single_upload(
        db,
        storage,
        current_user,
        library,
        file,
        visibility.value,
        external_duplicate_action=external_duplicate_action,
        redis=redis,
    )
    if outcome.error == "File too large":
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")

    if outcome.external_duplicate is not None:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=outcome.external_duplicate.model_dump(),
        )

    if outcome.stored_file is None and outcome.track_id is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not store file")

    if outcome.stored_file is None and outcome.track_id is not None:
        from ..routes.tracks import _build_track_response

        track = await music.get_track(db, outcome.track_id, include={"artist", "album"})
        if track is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track not found")
        await db.commit()
        return await _build_track_response(
            track,
            current_user,
            storage,
            IncludeQuery({"artist", "album"}),
        )

    if outcome.is_duplicate:
        response.headers["X-Duplicate"] = "true"

    if outcome.track_id is not None:
        response.headers["X-Track-Id"] = outcome.track_id

    assert outcome.stored_file is not None
    db.add(outcome.stored_file)
    await db.commit()

    logger.info("Uploaded file %s (%s bytes)", outcome.stored_file.id, outcome.stored_file.size)
    return await _build_stored_file_response(outcome.stored_file, current_user, storage)


@router.post(
    "/upload/bulk",
    response_model=List[BulkFileUploadResult],
    dependencies=[Depends(rate_limit)],
)
async def bulk_upload_files(
    files: List[UploadFile] = File(...),
    visibility: Visibility = Query(Visibility.PRIVATE),
    library_id: Optional[str] = Query(None),
    external_duplicate_action: Optional[Literal["keep_local", "discard_upload"]] = Query(None),
    current_user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """Upload multiple files in a single request.

    Audio files are imported as tracks; other files are stored as-is. The whole
    request is subject to the same per-IP rate limit as ``/files/upload``, and to
    per-request limits on the number of files and total request size.
    """
    config = storage.config

    if len(files) > config.max_bulk_upload_files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Too many files: maximum is {config.max_bulk_upload_files}",
        )

    if config.max_bulk_upload_total_size is not None:
        total_size = 0
        for upload_file in files:
            size = _discover_upload_size(upload_file.file)
            if size is not None:
                total_size += size
        if total_size > config.max_bulk_upload_total_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Total request size exceeds the maximum of {config.max_bulk_upload_total_size} bytes",
            )

    library: Optional[Library] = None
    if library_id is not None:
        library = await _get_target_library(db, current_user, library_id)
    elif any((f.content_type or "").startswith("audio/") for f in files):
        library = await _get_or_create_uploads_library(db, current_user)

    results: List[BulkFileUploadResult] = []
    for upload_file in files:
        try:
            outcome = await _process_single_upload(
                db,
                storage,
                current_user,
                library,
                upload_file,
                visibility.value,
                external_duplicate_action=external_duplicate_action,
                redis=redis,
            )
            if outcome.external_duplicate is not None:
                result = BulkFileUploadResult(
                    filename=upload_file.filename,
                    status="external_duplicate",
                    external_duplicate=outcome.external_duplicate,
                )
            elif outcome.error == "File too large":
                result = BulkFileUploadResult(
                    filename=upload_file.filename,
                    error="File too large",
                )
            elif outcome.stored_file is None and outcome.track_id is not None:
                result = BulkFileUploadResult(
                    filename=upload_file.filename,
                    track_id=outcome.track_id,
                    status="discard_upload",
                )
            elif outcome.stored_file is None:
                result = BulkFileUploadResult(
                    filename=upload_file.filename,
                    error="Could not store file",
                )
            else:
                db.add(outcome.stored_file)
                result = BulkFileUploadResult(
                    filename=upload_file.filename,
                    stored_file=await _build_stored_file_response(
                        outcome.stored_file,
                        current_user,
                        storage,
                    ),
                    track_id=outcome.track_id,
                    duplicate=outcome.is_duplicate,
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Bulk upload failed for %s: %s", upload_file.filename, exc)
            result = BulkFileUploadResult(
                filename=upload_file.filename,
                error="Could not process file",
            )
        finally:
            await upload_file.close()

        results.append(result)

    await db.commit()
    return results


@router.post(
    "/upload/resolve-duplicate",
    response_model=Union[StoredFileResponse, TrackResponse],
    dependencies=[Depends(rate_limit)],
)
async def resolve_upload_duplicate(
    response: Response,
    body: ExternalDuplicateResolutionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    redis: Redis = Depends(get_redis),
    config: SonghiveConfig = Depends(get_config),
):
    """Resolve a pending external-duplicate warning by token."""
    try:
        result = await resolve_external_duplicate(
            db,
            body.token,
            body.action,
            str(current_user.id),
            config,
            storage,
            redis=redis,
        )
    except ExternalDuplicateTokenError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found or expired",
        ) from None
    except ExternalDuplicatePermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        ) from None

    await db.commit()

    if result.track is not None:
        response.headers["X-Track-Id"] = str(result.track.id)

    if result.stored_file is None:
        from ..routes.tracks import _build_track_response

        track = await music.get_track(db, result.track.id, include={"artist", "album"})
        if track is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track not found")
        return await _build_track_response(
            track,
            current_user,
            storage,
            IncludeQuery({"artist", "album"}),
        )

    db.add(result.stored_file)
    await db.commit()
    return await _build_stored_file_response(result.stored_file, current_user, storage)


@router.get(
    "/",
    response_model=List[StoredFileResponse],
    operation_id="list_files_api_v1_files__get",
)
async def list_uploaded_files(
    response: Response,
    q: Optional[str] = Query(None, description="Search by original filename"),
    user: Optional[User] = Depends(get_current_user_optional),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """List stored files visible to the requester."""
    total = await count_files(db, q=q, user=user)
    rows = await list_files(db, q=q, user=user, limit=pagination.limit, offset=pagination.offset)
    pagination.set_total(response, total)
    return [
        StoredFileResponse(
            id=f.id,
            content_type=f.content_type,
            size=f.size,
            sha256=f.sha256,
            owner_id=redact_owner(cast(HasOwnerId, f), user),
            visibility=f.visibility,
            original_filename=f.original_filename,
            url=await storage.get_url(f),
        )
        for f in rows
    ]


@router.get(
    "/{file_id}",
    response_model=StoredFileResponse,
    dependencies=[Depends(require_access("file"))],
)
async def get_file_metadata(
    file_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    """Get metadata for a stored file, including associated tracks."""
    stored_file = await db.get(StoredFile, file_id)
    # ``require_access`` already loads the row and raises 404 when missing.
    assert stored_file is not None

    track_rows, _ = await music.list_tracks(
        db,
        file_id=file_id,
        user=user,
        limit=1000,
        include={"artist", "album"},
    )
    track_summaries = [await build_track_summary(t, storage) for t in track_rows]
    tracks = [t for t in track_summaries if t is not None]

    url = await storage.get_url(stored_file)
    return StoredFileResponse(
        id=stored_file.id,
        content_type=stored_file.content_type,
        size=stored_file.size,
        sha256=stored_file.sha256,
        owner_id=redact_owner(cast(HasOwnerId, stored_file), user),
        visibility=stored_file.visibility,
        original_filename=stored_file.original_filename,
        url=url,
        tracks=tracks,
    )


_DOWNLOAD_FALLBACK_FILENAME = "file"

# ASCII control characters (0x00-0x1f, 0x7f) including NUL; these are
# disallowed in HTTP headers and can break filesystem operations.
_FILENAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Explicit allowlist of MIME types that can be served inline. SVG and broad
# audio/image/video wildcards are excluded to prevent content-sniffing attacks.
_SAFE_INLINE_TYPES = {
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/flac",
    "audio/webm",
    "audio/aac",
    "audio/mp4",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "video/mp4",
    "video/webm",
    "video/ogg",
    "video/avi",
}


def _safe_inline_types(content_type: str) -> bool:
    """Return True if the content type is in the explicit inline allowlist."""
    if not content_type:
        return False
    return content_type.split(";")[0].strip() in _SAFE_INLINE_TYPES


def _sanitize_filename(filename: Optional[str]) -> str:
    """
    Strip control characters, path segments, and surrounding whitespace from a
    filename.

    All ASCII control characters (0x00-0x1f, 0x7f) are removed, backslashes are
    normalised to forward slashes, and the basename is taken using a POSIX path
    parser so the behaviour is the same on every platform.
    """
    if not filename:
        return _DOWNLOAD_FALLBACK_FILENAME
    # Remove ASCII control chars (including NUL) before any path parsing.
    filename = _FILENAME_CONTROL_RE.sub("", filename)
    # Normalise Windows-style separators to POSIX-style before taking basename.
    filename = filename.replace("\\", "/")
    filename = PurePosixPath(filename).name
    if filename in (".", ".."):
        return _DOWNLOAD_FALLBACK_FILENAME
    filename = filename.strip()
    return filename or _DOWNLOAD_FALLBACK_FILENAME


def _sanitize_content_disposition(
    disposition: Literal["inline", "attachment"],
    content_type: Optional[str],
) -> Literal["inline", "attachment"]:
    """Force attachment for content types that are not safe to serve inline."""
    if disposition == "inline" and not _safe_inline_types(content_type or ""):
        return "attachment"
    return disposition


async def _download_stored_file_response(
    stored_file: StoredFile,
    disposition: Literal["inline", "attachment"],
    storage: StorageService,
) -> FileResponse:
    """Resolve a StoredFile and return a FileResponse for its bytes."""
    try:
        path = await storage.backend.retrieve(stored_file.storage_path)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found") from e
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    disposition = _sanitize_content_disposition(disposition, stored_file.content_type)
    filename = _sanitize_filename(stored_file.original_filename)
    headers = {"X-Content-Type-Options": "nosniff"}

    return FileResponse(
        path,
        media_type=stored_file.content_type,
        filename=filename,
        content_disposition_type=disposition,
        headers=headers,
    )


@router.get(
    "/{file_id}/download",
    dependencies=[Depends(require_access("file")), Depends(rate_limit)],
)
async def download_file(
    file_id: str,
    disposition: Literal["inline", "attachment"] = Query("inline"),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    """Download the bytes for a stored file, with Range request support."""
    stored_file = await db.get(StoredFile, file_id)
    # ``require_access`` already loads the row and raises 404 when missing.
    assert stored_file is not None
    return await _download_stored_file_response(stored_file, disposition, storage)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(rate_limit_account)])
async def delete_file(
    file_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Delete a file upload and any tracks/playlists/libraries that reference it."""
    stored_file = await db.get(StoredFile, file_id)
    if stored_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if not await acl.can_manage(db, current_user, "file", file_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    admin_deletion = current_user.is_admin and stored_file.owner_id != current_user.id
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="file.admin_delete" if admin_deletion else "file.delete",
        target_type="file",
        target_id=file_id,
        details={
            "sha256": stored_file.sha256,
            "original_filename": stored_file.original_filename,
            "owner_id": stored_file.owner_id,
        },
        ip_address=client_ip(request),
    )

    try:
        unpublish_list = await deletion.delete_stored_file(db, storage, file_id)
    except deletion.DeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.args[0]) from exc

    await db.commit()

    for info in unpublish_list:
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
