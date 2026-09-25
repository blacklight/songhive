"""
Bulk download archive routes.

``POST /downloads`` resolves a selection (explicit track/remote-object/
episode ids, or an album/artist/playlist/library container) into an archive
request row and enqueues asynchronous ZIP generation. Archives are
per-user: list/detail/download/delete only ever touch the requester's own
rows. Request throttling uses the shared per-account sliding window plus a
cap on concurrently active archives per user; a Redis-backed semaphore caps
archive builds instance-wide (``config.downloads.max_concurrent_archives``).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...models.download import DownloadArchive
from ...models.user import User
from ...services import downloads as downloads_service
from ...services.storage import StorageService
from ..deps import get_config, get_current_user, get_db, get_storage_service
from ..middleware.rate_limit import rate_limit_account
from .files import _download_stored_file_response

router = APIRouter(prefix="/downloads")
logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = ("pending", "processing")


class ArchiveCreateRequest(BaseModel):
    """Bulk archive request: pick items directly and/or name a container."""

    track_ids: List[str] = Field(default_factory=list, max_length=500)
    remote_object_ids: List[str] = Field(default_factory=list, max_length=500)
    episode_ids: List[str] = Field(default_factory=list, max_length=500)
    album_id: Optional[str] = None
    artist_id: Optional[str] = None
    playlist_id: Optional[str] = None
    library_id: Optional[str] = None
    label: Optional[str] = Field(default=None, max_length=256)

    def has_source(self) -> bool:
        return bool(
            self.track_ids
            or self.remote_object_ids
            or self.episode_ids
            or self.album_id
            or self.artist_id
            or self.playlist_id
            or self.library_id
        )


class ArchiveItemResponse(BaseModel):
    kind: str
    title: str
    artist: str = ""


class DownloadArchiveResponse(BaseModel):
    id: str
    status: str
    label: str
    item_count: int
    items: List[ArchiveItemResponse]
    item_errors: Optional[list] = None
    error: Optional[str] = None
    size: Optional[int] = None
    download_url: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


def _archive_response(archive: DownloadArchive) -> DownloadArchiveResponse:
    stored = archive.archive_file
    ready = archive.status == "ready" and stored is not None
    return DownloadArchiveResponse(
        id=archive.id,
        status=archive.status,
        label=archive.label,
        item_count=len(archive.items or []),
        items=[
            ArchiveItemResponse(
                kind=str(item.get("kind") or ""),
                title=str(item.get("title") or ""),
                artist=str(item.get("artist") or ""),
            )
            for item in (archive.items or [])
        ],
        item_errors=archive.item_errors,
        error=archive.error,
        size=stored.size if ready else None,
        download_url=f"/api/v1/downloads/{archive.id}/file" if ready else None,
        created_at=archive.created_at.isoformat(),
        completed_at=archive.completed_at.isoformat() if archive.completed_at else None,
    )


def _check_enabled(config: SonghiveConfig) -> None:
    if not config.downloads.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


async def _get_own_archive(
    db: AsyncSession,
    user: User,
    archive_id: str,
) -> DownloadArchive:
    result = await db.execute(
        select(DownloadArchive)
        .where(DownloadArchive.id == archive_id, DownloadArchive.user_id == user.id)
        .options(selectinload(DownloadArchive.archive_file))
    )
    archive = result.scalar_one_or_none()
    if archive is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return archive


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=DownloadArchiveResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def create_download_archive(
    body: ArchiveCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """Queue a ZIP archive for the requested items/containers."""
    _check_enabled(config)
    if not body.has_source():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No download source given")

    active = await db.scalar(
        select(func.count())
        .select_from(DownloadArchive)
        .where(DownloadArchive.user_id == user.id, DownloadArchive.status.in_(_ACTIVE_STATUSES))
    )
    if (active or 0) >= config.downloads.max_active_per_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many downloads in progress; wait for one to finish",
        )

    try:
        items, _skipped = await downloads_service.resolve_archive_items(
            db,
            user,
            config,
            track_ids=body.track_ids,
            remote_object_ids=body.remote_object_ids,
            episode_ids=body.episode_ids,
            album_id=body.album_id,
            artist_id=body.artist_id,
            playlist_id=body.playlist_id,
            library_id=body.library_id,
        )
    except downloads_service.ArchiveRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    if not items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No accessible items to archive",
        )

    label = body.label or await downloads_service.derive_label(
        db,
        item_count=len(items),
        album_id=body.album_id,
        artist_id=body.artist_id,
        playlist_id=body.playlist_id,
        library_id=body.library_id,
    )

    archive = DownloadArchive(
        user_id=user.id,
        status="pending",
        label=label,
        items=[item.as_dict() for item in items],
    )
    db.add(archive)
    await db.flush()

    try:
        from ...tasks.downloads import build_download_archive

        build_download_archive.delay(archive.id)
    except Exception as exc:  # broker errors must not fail the request
        logger.warning("Could not enqueue download archive %s: %s", archive.id, exc)

    return _archive_response(archive)


@router.get("/", response_model=List[DownloadArchiveResponse])
async def list_download_archives(
    response: Response,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """List the current user's download archives, newest first."""
    _check_enabled(config)
    limit = min(max(1, limit), 200)
    stmt = (
        select(DownloadArchive)
        .where(DownloadArchive.user_id == user.id)
        .options(selectinload(DownloadArchive.archive_file))
        .order_by(DownloadArchive.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    archives = list(result.scalars().all())
    response.headers["X-Total-Count"] = str(len(archives))
    return [_archive_response(archive) for archive in archives]


@router.get("/{archive_id}", response_model=DownloadArchiveResponse)
async def get_download_archive(
    archive_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """Return one of the current user's archives."""
    _check_enabled(config)
    archive = await _get_own_archive(db, user, archive_id)
    return _archive_response(archive)


@router.get("/{archive_id}/file", dependencies=[Depends(rate_limit_account)])
async def download_archive_file(
    archive_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage_service),
    config: SonghiveConfig = Depends(get_config),
):
    """Download the generated ZIP once the archive is ready."""
    _check_enabled(config)
    archive = await _get_own_archive(db, user, archive_id)
    if archive.status != "ready" or archive.archive_file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return await _download_stored_file_response(archive.archive_file, "attachment", storage)


@router.delete(
    "/{archive_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_download_archive(
    archive_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage_service),
    config: SonghiveConfig = Depends(get_config),
):
    """Delete one of the current user's archives and its backing ZIP."""
    _check_enabled(config)
    archive = await _get_own_archive(db, user, archive_id)
    await downloads_service.delete_archive_payload(db, storage, archive)
    await db.delete(archive)


@router.post("/clear", dependencies=[Depends(rate_limit_account)])
async def clear_completed_downloads(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: StorageService = Depends(get_storage_service),
    config: SonghiveConfig = Depends(get_config),
):
    """Delete all of the current user's ready/failed archives."""
    _check_enabled(config)
    result = await db.execute(
        select(DownloadArchive)
        .where(DownloadArchive.user_id == user.id, DownloadArchive.status.in_(("ready", "failed")))
        .options(selectinload(DownloadArchive.archive_file))
    )
    archives = list(result.scalars().all())
    for archive in archives:
        await downloads_service.delete_archive_payload(db, storage, archive)
        await db.delete(archive)
    return {"cleared": len(archives)}
