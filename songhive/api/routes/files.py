"""
File storage routes: upload, metadata, and download.
"""

import logging
from pathlib import Path
from typing import List, Literal, Optional, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models._enums import Visibility
from ...models.library import Library
from ...models.stored_file import StoredFile
from ...models.user import User
from ...services import acl, audit, deletion, music
from ...services.federation import unpublish_track_activity
from ...services.import_ import DuplicateTrackError, import_audio_file
from ...services.storage import StorageService, count_files, list_files
from ...storage import FileSizeLimitExceededError
from .._common import Pagination, client_ip, get_pagination
from ..deps import get_current_user, get_current_user_optional, get_db, get_storage_service, require_access
from ..middleware.rate_limit import rate_limit, rate_limit_account
from ._common import HasOwnerId, redact_owner

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


@router.post(
    "/upload",
    response_model=StoredFileResponse,
    dependencies=[Depends(rate_limit)],
)
async def upload_file(
    response: Response,
    file: UploadFile,
    visibility: Visibility = Query(Visibility.PRIVATE),
    library_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file and store it in the configured backend.

    Content-addressable deduplication returns the canonical ``StoredFile`` when
    the same bytes have already been uploaded. In that case the response
    includes an ``X-Duplicate: true`` header and the caller's ``owner_id`` and
    ``visibility`` are ignored; they only apply to newly created rows.

    Audio files are additionally imported into the selected library (or the
    caller's default ``Uploads`` library) so they become tracks and are not
    garbage-collected as orphans.
    """
    content_type = file.content_type or "application/octet-stream"
    try:
        stored_file, is_duplicate = await storage.store_file(
            db,
            file.file,
            content_type=content_type,
            original_filename=file.filename,
            owner_id=current_user.id,
            visibility=visibility.value,
            return_duplicate=True,
        )
    except FileSizeLimitExceededError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large") from exc

    if is_duplicate:
        response.headers["X-Duplicate"] = "true"

    track_id: Optional[str] = None
    if content_type.startswith("audio/"):
        storage._rewind(file.file)
        library = await _get_target_library(db, current_user, library_id)
        try:
            result = await import_audio_file(
                db,
                storage_service=storage,
                file=file.file,
                filename=file.filename or "audio",
                library_id=str(library.id),
                owner_id=str(current_user.id),
                visibility=visibility.value,
                source="upload",
                content_type=content_type,
            )
            track_id = str(result.track.id)
        except DuplicateTrackError as exc:
            track_id = exc.existing_track_id
        except Exception as exc:
            logger.warning("Could not import audio file %s as track: %s", stored_file.id, exc)

    if track_id is not None:
        response.headers["X-Track-Id"] = track_id

    db.add(stored_file)
    await db.commit()

    logger.info("Uploaded file %s (%s bytes)", stored_file.id, stored_file.size)
    url = await storage.get_url(stored_file)
    return StoredFileResponse(
        id=stored_file.id,
        content_type=stored_file.content_type,
        size=stored_file.size,
        sha256=stored_file.sha256,
        owner_id=redact_owner(cast(HasOwnerId, stored_file), current_user),
        visibility=stored_file.visibility,
        original_filename=stored_file.original_filename,
        url=url,
    )


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
    """Get metadata for a stored file."""
    stored_file = await db.get(StoredFile, file_id)
    # ``require_access`` already loads the row and raises 404 when missing.
    assert stored_file is not None

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
    )


_DOWNLOAD_FALLBACK_FILENAME = "file"

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
    """
    if not filename:
        return _DOWNLOAD_FALLBACK_FILENAME
    filename = filename.replace("\r", "").replace("\n", "").replace("\\", "/")
    filename = Path(filename).name
    filename = filename.strip()
    return filename or _DOWNLOAD_FALLBACK_FILENAME


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

    try:
        path = await storage.backend.retrieve(stored_file.storage_path)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found") from e
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if disposition == "inline" and not _safe_inline_types(stored_file.content_type):
        disposition = "attachment"

    filename = _sanitize_filename(stored_file.original_filename)
    headers = {"X-Content-Type-Options": "nosniff"}

    return FileResponse(
        path,
        media_type=stored_file.content_type,
        filename=filename,
        content_disposition_type=disposition,
        headers=headers,
    )


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
