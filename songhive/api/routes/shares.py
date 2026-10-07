"""
Share-grant routes: owner/admin CRUD for giving specific users access to items.
"""

import logging
from datetime import datetime
from typing import Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.audit_log import AuditTargetType
from ...models.notification import NotificationType
from ...models.share_grant import ShareGrant
from ...models.user import User
from ...services import acl, audit
from ...services import notifications as notifications_service
from ...services import sharing
from ...services.auth import get_user_by_id, get_user_by_username_or_email
from .._common import Pagination, get_pagination
from ..deps import get_current_user, get_db
from ..middleware.rate_limit import rate_limit_account
from ..responses import UserSummary, build_user_summary
from ._common import load_and_authorize, validate_item_type

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/shares")


class ShareGrantCreate(BaseModel):
    """Payload for creating a share grant."""

    item_type: str
    item_id: str
    user_id: str
    collaborator: bool = False

    @field_validator("item_type")
    @classmethod
    def _check_item_type(cls, value: str) -> str:
        return validate_item_type(value)

    @model_validator(mode="after")
    def _check_collaborator(self) -> "ShareGrantCreate":
        if self.collaborator and self.item_type not in acl.COLLABORATIVE_ITEM_TYPES:
            raise ValueError(f"Item type {self.item_type!r} does not support collaborator grants")
        return self


class ShareGrantResponse(BaseModel):
    """Public share-grant response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    item_type: str
    item_id: str
    user_id: str
    username: Optional[str] = None
    collaborator: bool = False
    created_at: datetime


class ShareGrantUpdate(BaseModel):
    """Payload for updating a share grant's role."""

    collaborator: bool


class CreatedShareResponse(BaseModel):
    """A share grant or share URL token created by the current user."""

    id: str
    kind: Literal["grant", "url"]
    item_type: str
    item_id: str
    item_title: Optional[str] = None
    item_url: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    collaborator: Optional[bool] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ReceivedShareResponse(BaseModel):
    """A share grant the current user has received."""

    id: str
    item_type: str
    item_id: str
    item_title: Optional[str] = None
    item_url: Optional[str] = None
    collaborator: bool = False
    shared_by: Optional[UserSummary] = None
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
    try:
        grant, created = await sharing.create_share_grant(
            db,
            body.item_type,
            body.item_id,
            target_user.id,
            created_by=current_user.id,
            collaborator=body.collaborator,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
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
        item_title = acl.get_item_title(item)
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
                "collaborator": grant.collaborator,
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


def _item_url(
    item_type: str,
    item_id: str,
    titles: Dict[Tuple[str, str], Optional[str]],
) -> Optional[str]:
    """Return the frontend page URL for an item, or None when it is gone."""
    plural = acl.get_item_plural(item_type)
    if plural is None or (item_type, item_id) not in titles:
        return None
    return f"/{plural}/{item_id}"


def _created_share_entry(
    *,
    share_id: str,
    kind: Literal["grant", "url"],
    item_type: str,
    item_id: str,
    created_at: datetime,
    titles: Dict[Tuple[str, str], Optional[str]],
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    collaborator: Optional[bool] = None,
    expires_at: Optional[datetime] = None,
    revoked_at: Optional[datetime] = None,
) -> CreatedShareResponse:
    """Build a ``CreatedShareResponse``, resolving the item title and page URL."""
    item_key = (item_type, item_id)
    return CreatedShareResponse(
        id=share_id,
        kind=kind,
        item_type=item_type,
        item_id=item_id,
        item_title=titles.get(item_key),
        item_url=_item_url(item_type, item_id, titles),
        user_id=user_id,
        username=username,
        collaborator=collaborator,
        created_at=created_at,
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


@router.get("/mine", response_model=List[CreatedShareResponse])
async def list_created_shares(
    response: Response,
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    include_revoked: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """List every share grant and share URL token created by the current user.

    Revoked share tokens are hidden unless ``include_revoked`` is set.
    """
    grants = await sharing.list_share_grants_created_by(db, current_user.id)
    tokens = await sharing.list_share_tokens_created_by(db, current_user.id, include_revoked=include_revoked)

    titles = await acl.resolve_item_titles(
        db,
        [(g.item_type, g.item_id) for g in grants] + [(t.item_type, t.item_id) for t in tokens],
    )

    entries = [
        _created_share_entry(
            share_id=grant.id,
            kind="grant",
            item_type=grant.item_type,
            item_id=grant.item_id,
            created_at=grant.created_at,
            titles=titles,
            user_id=grant.user_id,
            username=grant.user.username if grant.user is not None else None,
            collaborator=grant.collaborator,
        )
        for grant in grants
    ]
    entries += [
        _created_share_entry(
            share_id=token.id,
            kind="url",
            item_type=token.item_type,
            item_id=token.item_id,
            created_at=token.created_at,
            titles=titles,
            expires_at=token.expires_at,
            revoked_at=token.revoked_at,
        )
        for token in tokens
    ]
    entries.sort(key=lambda entry: entry.created_at, reverse=True)

    pagination.set_total(response, len(entries))
    return entries[pagination.offset : pagination.offset + pagination.limit]


@router.get("/received", response_model=List[ReceivedShareResponse])
async def list_received_shares(
    response: Response,
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    item_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List share grants the current user has received, newest first."""
    if item_type is not None:
        try:
            item_type = validate_item_type(item_type)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid item type",
            ) from exc

    total = await sharing.count_share_grants_received(db, current_user.id, item_type=item_type)
    grants = await sharing.list_share_grants_received(
        db,
        current_user.id,
        item_type=item_type,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    pagination.set_total(response, total)

    titles = await acl.resolve_item_titles(db, [(g.item_type, g.item_id) for g in grants])
    return [
        ReceivedShareResponse(
            id=grant.id,
            item_type=grant.item_type,
            item_id=grant.item_id,
            item_title=titles.get((grant.item_type, grant.item_id)),
            item_url=_item_url(grant.item_type, grant.item_id, titles),
            collaborator=grant.collaborator,
            shared_by=(
                await build_user_summary(grant.creator)
                if grant.creator is not None and grant.creator.is_active
                else None
            ),
            created_at=grant.created_at,
        )
        for grant in grants
    ]


def _audit_target_type(item_type: str) -> Optional[AuditTargetType]:
    """Map a shareable item type to an audit target type, when one exists."""
    try:
        return AuditTargetType(item_type)
    except ValueError:
        return None


@router.patch(
    "/{share_id}",
    response_model=ShareGrantResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def update_share_grant(
    share_id: str,
    body: ShareGrantUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a share grant's role (viewer ↔ collaborator).

    Only the item owner or an admin may update a grant — grant creators
    without management rights and grantees cannot.  Missing and unauthorized
    requests both return 404 to avoid ID enumeration.
    """
    grant = await db.get(ShareGrant, share_id)
    if grant is None or not await acl.can_manage(db, current_user, grant.item_type, grant.item_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    try:
        await sharing.set_share_grant_collaborator(db, grant, body.collaborator)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="share_grant.update",
        target_type=_audit_target_type(grant.item_type),
        target_id=grant.item_id,
        details={
            "item_type": grant.item_type,
            "item_id": grant.item_id,
            "user_id": grant.user_id,
            "collaborator": grant.collaborator,
        },
    )
    await db.commit()

    response = ShareGrantResponse.model_validate(grant)
    if grant.user is not None:
        response.username = grant.user.username
    return response


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share_grant(
    share_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke a share grant by id.

    The item owner, an admin, the user who created the grant, or the grantee
    (leaving a share) may revoke it.  Missing and unauthorized requests both
    return 404 to avoid ID enumeration.
    """
    grant = await db.get(ShareGrant, share_id)
    if grant is None or (
        grant.user_id != current_user.id
        and grant.created_by != current_user.id
        and not await acl.can_manage(db, current_user, grant.item_type, grant.item_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )

    is_leave = grant.user_id == current_user.id
    item_type, item_id, grantee_id = grant.item_type, grant.item_id, grant.user_id
    await sharing.revoke_share_grant_by_id(db, share_id)
    plural = acl.get_item_plural(item_type) or item_type
    await notifications_service.retract_notifications(
        db,
        user_id=grantee_id,
        type=NotificationType.SHARE,
        source_url=f"/{plural}/{item_id}",
    )
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="share_grant.leave" if is_leave else "share_grant.revoke",
        target_type=_audit_target_type(item_type),
        target_id=item_id,
        details={
            "item_type": item_type,
            "item_id": item_id,
            "user_id": grantee_id,
        },
    )
    await db.commit()
    return None
