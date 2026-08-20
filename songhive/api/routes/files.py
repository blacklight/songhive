"""
File storage routes: upload, metadata, and download.
"""

import logging
from pathlib import Path
from typing import Literal, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models._enums import Visibility
from ...models.stored_file import StoredFile
from ...models.user import User
from ...services.storage import StorageService
from ...storage import FileSizeLimitExceededError
from ..deps import get_current_user, get_current_user_optional, get_db, get_storage_service, require_access
from ..middleware.rate_limit import rate_limit
from ._common import _HasOwnerId, redact_owner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files")


class StoredFileResponse(BaseModel):
    """Public metadata for a stored file."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    storage_path: str
    storage_backend: str
    content_type: str
    size: int
    sha256: str
    owner_id: Optional[str] = None
    visibility: str = Visibility.PRIVATE.value
    original_filename: Optional[str] = None
    url: str


@router.post("/upload", response_model=StoredFileResponse)
async def upload_file(
    response: Response,
    file: UploadFile,
    visibility: Visibility = Query(Visibility.PRIVATE),
    current_user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file and store it in the configured backend.

    Content-addressable deduplication returns the canonical ``StoredFile`` when
    the same bytes have already been uploaded. In that case the response
    includes an ``X-Duplicate: true`` header and the caller's ``owner_id`` and
    ``visibility`` are ignored; they only apply to newly created rows.
    """
    try:
        stored_file, is_duplicate = await storage.store_file(
            db,
            file.file,
            content_type=file.content_type or "application/octet-stream",
            original_filename=file.filename,
            owner_id=current_user.id,
            visibility=visibility.value,
            return_duplicate=True,
        )
    except FileSizeLimitExceededError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large") from exc

    if is_duplicate:
        response.headers["X-Duplicate"] = "true"

    db.add(stored_file)
    await db.commit()

    logger.info("Uploaded file %s (%s bytes)", stored_file.id, stored_file.size)
    url = await storage.get_url(stored_file)
    return StoredFileResponse(
        id=stored_file.id,
        storage_path=stored_file.storage_path,
        storage_backend=stored_file.storage_backend,
        content_type=stored_file.content_type,
        size=stored_file.size,
        sha256=stored_file.sha256,
        owner_id=redact_owner(cast(_HasOwnerId, stored_file), current_user),
        visibility=stored_file.visibility,
        original_filename=stored_file.original_filename,
        url=url,
    )


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
        storage_path=stored_file.storage_path,
        storage_backend=stored_file.storage_backend,
        content_type=stored_file.content_type,
        size=stored_file.size,
        sha256=stored_file.sha256,
        owner_id=redact_owner(cast(_HasOwnerId, stored_file), user),
        visibility=stored_file.visibility,
        original_filename=stored_file.original_filename,
        url=url,
    )


_DOWNLOAD_FALLBACK_FILENAME = "file"


def _safe_inline_types(content_type: str) -> bool:
    """Return True if the content type can be safely served inline."""
    if not content_type:
        return False
    if content_type.split(";")[0].strip() in ("image/svg+xml", "image/svg"):
        return False
    return content_type.startswith(("audio/", "image/", "video/"))


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
