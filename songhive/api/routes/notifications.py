"""
Notifications routes: list, seen-state management, deletion, and delivery
preferences.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.notification import NotificationType
from ...models.user import User
from ...services import notifications
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_db

router = APIRouter(prefix="/notifications")


class NotificationResponse(BaseModel):
    """A single notification."""

    id: str
    type: NotificationType
    actor_url: Optional[str] = None
    source_url: Optional[str] = None
    payload: Optional[dict] = None
    seen_at: Optional[str] = None
    created_at: Optional[str] = None


class NotificationIdsRequest(BaseModel):
    """Payload for bulk seen/unseen updates."""

    ids: List[str] = Field(min_length=1, max_length=200)


class NotificationIdsDeleteRequest(BaseModel):
    """Payload for bulk deletion."""

    ids: List[str] = Field(min_length=1, max_length=500)


class UpdatedResponse(BaseModel):
    """Number of rows affected by a bulk update."""

    updated: int


class DeletedResponse(BaseModel):
    """Number of rows removed by a delete operation."""

    deleted: int


class NotificationPreferenceItem(BaseModel):
    """Delivery targets for one notification type."""

    type: NotificationType
    in_app: bool
    email: bool
    email_digest: bool


class NotificationPreferencesResponse(BaseModel):
    """Merged preference view: one item per notification type."""

    preferences: List[NotificationPreferenceItem]


class NotificationPreferencesUpdate(BaseModel):
    """Payload for updating notification preferences."""

    preferences: List[NotificationPreferenceItem] = Field(min_length=1)


class UnreadCountResponse(BaseModel):
    """Number of unseen notifications."""

    count: int


def _parse_types(value: Optional[str]) -> Optional[List[NotificationType]]:
    """Parse the comma-separated ``type`` allowlist; ``None`` when absent."""
    if value is None or not value.strip():
        return None
    requested = {item.strip().lower() for item in value.split(",")}
    return [t for t in NotificationType if t.value in requested]


@router.get("/", response_model=List[NotificationResponse])
async def list_notifications(
    response: Response,
    seen: Optional[bool] = Query(None),
    type: Optional[str] = Query(None, description="Comma-separated notification type allowlist"),
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's notifications, newest first."""
    rows, total = await notifications.list_notifications(
        db,
        current_user.id,
        seen=seen,
        types=_parse_types(type),
        limit=pagination.limit,
        offset=pagination.offset,
    )
    pagination.set_total(response, total)
    return [NotificationResponse(**notifications.notification_to_dict(row)) for row in rows]


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the number of unseen notifications for the current user."""
    return UnreadCountResponse(count=await notifications.get_unread_count(db, current_user.id))


@router.post("/seen", response_model=UpdatedResponse)
async def mark_seen(
    body: NotificationIdsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark the given notifications as seen."""
    updated = await notifications.mark_seen(db, current_user.id, body.ids)
    await db.commit()
    return UpdatedResponse(updated=updated)


@router.post("/unseen", response_model=UpdatedResponse)
async def mark_unseen(
    body: NotificationIdsRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark the given notifications as unseen."""
    updated = await notifications.mark_unseen(db, current_user.id, body.ids)
    await db.commit()
    return UpdatedResponse(updated=updated)


@router.post("/seen-all", response_model=UpdatedResponse)
async def mark_all_seen(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all of the current user's notifications as seen."""
    updated = await notifications.mark_all_seen(db, current_user.id)
    await db.commit()
    return UpdatedResponse(updated=updated)


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete one of the current user's notifications.

    Missing and other users' notifications both return 404 to avoid ID
    enumeration.
    """
    deleted = await notifications.delete_notifications(db, current_user.id, [notification_id])
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/delete", response_model=DeletedResponse)
async def delete_notifications_bulk(
    body: NotificationIdsDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete the given notifications of the current user."""
    deleted = await notifications.delete_notifications(db, current_user.id, body.ids)
    await db.commit()
    return DeletedResponse(deleted=deleted)


@router.post("/clear", response_model=DeletedResponse)
async def clear_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all of the current user's notifications."""
    deleted = await notifications.clear_notifications(db, current_user.id)
    await db.commit()
    return DeletedResponse(deleted=deleted)


@router.get("/preferences", response_model=NotificationPreferencesResponse)
async def get_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the merged per-type delivery preference view."""
    prefs = await notifications.get_preferences(db, current_user.id)
    return NotificationPreferencesResponse(preferences=[NotificationPreferenceItem(**p) for p in prefs])


@router.put("/preferences", response_model=NotificationPreferencesResponse)
async def update_preferences(
    body: NotificationPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upsert per-type delivery preferences and return the merged view."""
    for item in body.preferences:
        await notifications.set_preference(
            db,
            current_user.id,
            item.type,
            in_app=item.in_app,
            email=item.email,
            email_digest=item.email_digest,
        )
    await db.commit()
    prefs = await notifications.get_preferences(db, current_user.id)
    return NotificationPreferencesResponse(preferences=[NotificationPreferenceItem(**p) for p in prefs])
