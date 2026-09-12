"""
Share-grant routes: owner/admin CRUD for giving specific users access to items.
"""

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.notification import NotificationType
from ...models.share_grant import ShareGrant
from ...models.user import User
from ...services import acl
from ...services import notifications as notifications_service
from ...services import sharing
from ...services.auth import get_user_by_id, get_user_by_username_or_email
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_db
from ..middleware.rate_limit import rate_limit_account
from ._common import load_and_authorize, validate_item_type

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/shares")


class ShareGrantCreate(BaseModel):
    """Payload for creating a share grant."""

    item_type: str
    item_id: str
    user_id: str

    @field_validator("item_type")
    @classmethod
    def _check_item_type(cls, value: str) -> str:
        return validate_item_type(value)


class ShareGrantResponse(BaseModel):
    """Public share-grant response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    item_type: str
    item_id: str
    user_id: str
    username: Optional[str] = None
    created_at: datetime


async def _resolve_share_grant_user(session: AsyncSession, value: str) -> User:
    """Resolve a target user identifier to an active User.

    The value may be a user id (UUID) or a username/email address.
    """
    user = await get_user_by_id(session, value)
    if user is None:
        user = await get_user_by_username_or_email(session, value)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="User not found",
        )
    return user


@router.post(
    "/",
    response_model=ShareGrantResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def create_share_grant(
    body: ShareGrantCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Grant a specific user access to an item."""
    await load_and_authorize(db, current_user, body.item_type, body.item_id)

    target_user = await _resolve_share_grant_user(db, body.user_id)
    grant, created = await sharing.create_share_grant(
        db,
        body.item_type,
        body.item_id,
        target_user.id,
        created_by=current_user.id,
    )
    await db.commit()

    if created:
        await _notify_share_grant(db, grant, current_user)

    response = ShareGrantResponse.model_validate(grant)
    response.username = target_user.username
    return response


async def _notify_share_grant(
    db: AsyncSession,
    grant: ShareGrant,
    current_user: User,
) -> None:
    """Create a share notification for the grantee, never failing the route."""
    try:
        if grant.user_id == current_user.id:
            return
        item = await acl.get_item(db, grant.item_type, grant.item_id)
        item_title = getattr(item, "title", None) or getattr(item, "name", None)
        plural = acl.get_item_plural(grant.item_type) or grant.item_type
        await notifications_service.create_notification(
            db,
            user_id=grant.user_id,
            type=NotificationType.SHARE,
            actor_url=current_user.actor_url or f"/users/{current_user.username}",
            source_url=f"/{plural}/{grant.item_id}",
            payload={
                "item_type": grant.item_type,
                "item_id": grant.item_id,
                "item_title": item_title,
                "actor_name": current_user.display_name or current_user.username,
                "actor_avatar_url": current_user.avatar_url,
            },
        )
        await db.commit()
    except Exception as exc:
        logger.warning(
            "Failed to create share notification for %s %s: %s",
            grant.item_type,
            grant.item_id,
            exc,
        )


@router.get("/", response_model=List[ShareGrantResponse])
async def list_share_grants(
    response: Response,
    item_type: str = Query(...),
    item_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List all share grants for an item."""
    await load_and_authorize(db, current_user, item_type, item_id)

    total = await sharing.count_share_grants(db, item_type, item_id)
    grants = await sharing.list_share_grants(db, item_type, item_id)
    pagination.set_total(response, total)
    responses = []
    for g in grants:
        resp = ShareGrantResponse.model_validate(g)
        if g.user is not None:
            resp.username = g.user.username
        responses.append(resp)
    return responses


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share_grant(
    share_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke a share grant by id.

    Missing and unauthorized requests both return 404 to avoid ID enumeration.
    """
    grant = await db.get(ShareGrant, share_id)
    if grant is None or not await acl.can_manage(db, current_user, grant.item_type, grant.item_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    item_type, item_id, grantee_id = grant.item_type, grant.item_id, grant.user_id
    await sharing.revoke_share_grant_by_id(db, share_id)
    plural = acl.get_item_plural(item_type) or item_type
    await notifications_service.retract_notifications(
        db,
        user_id=grantee_id,
        type=NotificationType.SHARE,
        source_url=f"/{plural}/{item_id}",
    )
    await db.commit()
    return None
