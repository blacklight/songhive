"""Shared helpers for entity image/cover upload endpoints."""

import io
from typing import Any, Optional

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.stored_file import StoredFile
from ...models.user import User
from ...services.storage import StorageService

IMAGE_SIZE_LIMIT = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


async def upload_entity_image(
    db: AsyncSession,
    storage: StorageService,
    entity: Any,
    field_name: str,
    file: UploadFile,
    user: User,
    *,
    owner_id: Optional[str] = None,
) -> StoredFile:
    """
    Store an image and attach it to ``entity`` at ``field_name``.

    Validates the content type against a small image allowlist and enforces
    the instance image size limit. The file is stored with the entity's
    visibility and attached by setting the entity's foreign key.
    """
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported image type",
        )

    data = await file.read()
    if len(data) > IMAGE_SIZE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image too large",
        )

    buffer = io.BytesIO(data)
    stored = await storage.store_file(
        db,
        buffer,
        content_type=content_type,
        original_filename=file.filename,
        owner_id=owner_id or user.id,
        visibility=getattr(entity, "visibility", "private"),
    )

    setattr(entity, field_name, stored.id)
    relationship = field_name.replace("_file_id", "_file")
    if hasattr(entity, relationship):
        setattr(entity, relationship, stored)
    return stored


async def remove_entity_image(entity: Any, field_name: str) -> Optional[str]:
    """Detach an image from ``entity`` and return the previous file id."""
    previous = getattr(entity, field_name)
    setattr(entity, field_name, None)
    relationship = field_name.replace("_file_id", "_file")
    if hasattr(entity, relationship):
        setattr(entity, relationship, None)
    return previous
