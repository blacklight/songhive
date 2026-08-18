"""
Admin routes.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.user import User, UserRole
from ...users import invites as invite_service
from ...users import manager as user_manager
from ..deps import get_db, require_admin

router = APIRouter(prefix="/admin")


class AdminUserResponse(BaseModel):
    """Admin-facing user record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str
    is_active: bool
    role: UserRole


@router.get("/users", response_model=List[AdminUserResponse])
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """List all users (admin only)."""
    users = await user_manager.list_users(db, limit=limit, offset=offset)
    return [AdminUserResponse.model_validate(user) for user in users]


@router.post("/users/{user_id}/promote", response_model=AdminUserResponse)
async def promote_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Promote a user to admin."""
    try:
        user = await user_manager.promote_user(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return AdminUserResponse.model_validate(user)


@router.post("/users/{user_id}/demote", response_model=AdminUserResponse)
async def demote_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Demote a user to the user role."""
    try:
        user = await user_manager.demote_user(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return AdminUserResponse.model_validate(user)


@router.post("/users/{user_id}/approve", response_model=AdminUserResponse)
async def approve_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Approve a user by activating their account."""
    try:
        user = await user_manager.approve_user(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return AdminUserResponse.model_validate(user)


@router.post("/users/{user_id}/activate", response_model=AdminUserResponse)
async def activate_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Activate (re-enable) a user account."""
    try:
        user = await user_manager.activate_user(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return AdminUserResponse.model_validate(user)


@router.post("/users/{user_id}/deactivate", response_model=AdminUserResponse)
async def deactivate_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Deactivate a user account."""
    try:
        user = await user_manager.deactivate_user_by_id(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return AdminUserResponse.model_validate(user)


class AdminInviteResponse(BaseModel):
    """Admin-facing invite record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    created_by: str
    max_uses: Optional[int]
    uses: int
    expires_at: Optional[datetime]
    created_at: datetime


class AdminInviteCreateRequest(BaseModel):
    """Request body for creating an invite code."""

    max_uses: Optional[int] = None
    expires_at: Optional[datetime] = None


@router.get("/invites", response_model=List[AdminInviteResponse])
async def list_invites(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """List invite codes (admin only)."""
    total = await invite_service.count_invites(db)
    invites = await invite_service.list_invites(db, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return [AdminInviteResponse.model_validate(invite) for invite in invites]


@router.post(
    "/invites",
    response_model=AdminInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite(
    body: AdminInviteCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Create a new invite code (admin only)."""
    try:
        invite = await invite_service.create_invite(
            db,
            created_by=admin.id,
            max_uses=body.max_uses,
            expires_at=body.expires_at,
        )
    except invite_service.InviteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return AdminInviteResponse.model_validate(invite)


@router.delete("/invites/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invite(
    code: str,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Revoke an invite code (admin only)."""
    deleted = await invite_service.revoke_invite(db, code)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
