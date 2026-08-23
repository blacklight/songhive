"""
Admin routes.
"""

from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from kombu.exceptions import OperationalError as KombuOperationalError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models._enums import Visibility
from ...models.user import User, UserRole
from ...services import audit, music
from ...services import settings as settings_service
from ...services import stats as stats_service
from ...services.auth import get_user_by_id
from ...services.federation import unpublish_track_activity
from ...tasks.storage import cleanup_orphaned_files
from ...users import invites as invite_service
from ...users import manager as user_manager
from ...users import oauth as oauth_client_service
from ...users.tokens import revoke_all_user_refresh_tokens
from .._common import Pagination, client_ip, get_pagination
from ..deps import get_config, get_db, get_redis, require_admin
from ..middleware.rate_limit import rate_limit_account

router = APIRouter(prefix="/admin")


class AdminUserResponse(BaseModel):
    """Admin-facing user record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str
    is_active: bool
    role: UserRole


class AuditLogResponse(BaseModel):
    """Admin-facing audit log record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    actor_id: Optional[str]
    target_type: Optional[str]
    target_id: Optional[str]
    details: Optional[dict]
    ip_address: Optional[str]
    created_at: datetime


@router.get(
    "/users",
    response_model=List[AdminUserResponse],
    dependencies=[Depends(require_admin)],
)
async def list_users(
    response: Response,
    q: Optional[str] = Query(None, min_length=1, max_length=128),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List all users (admin only), optionally filtering by username or email."""
    if q:
        users, total = await user_manager.search_users(db, q, limit=pagination.limit, offset=pagination.offset)
    else:
        users = await user_manager.list_users(db, limit=pagination.limit, offset=pagination.offset)
        total = await user_manager.count_users(db)

    pagination.set_total(response, total)
    return [AdminUserResponse.model_validate(user) for user in users]


@router.post(
    "/users/{user_id}/promote",
    response_model=AdminUserResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def promote_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Promote a user to admin."""
    try:
        user = await user_manager._get_user_or_raise(db, user_id)
        old_role = user.role
        user = await user_manager.promote_user(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="user.promote",
        target_type="user",
        target_id=user_id,
        details={"old_role": old_role, "new_role": user.role},
        ip_address=client_ip(request),
    )
    return AdminUserResponse.model_validate(user)


@router.post(
    "/users/{user_id}/demote",
    response_model=AdminUserResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def demote_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Demote a user to the user role."""
    try:
        user = await user_manager._get_user_or_raise(db, user_id)
        old_role = user.role
        user = await user_manager.demote_user(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="user.demote",
        target_type="user",
        target_id=user_id,
        details={"old_role": old_role, "new_role": user.role},
        ip_address=client_ip(request),
    )
    return AdminUserResponse.model_validate(user)


@router.post(
    "/users/{user_id}/approve",
    response_model=AdminUserResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def approve_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Approve a user by activating their account."""
    try:
        user = await user_manager.approve_user(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="user.approve",
        target_type="user",
        target_id=user_id,
        details={"is_active": user.is_active},
        ip_address=client_ip(request),
    )
    return AdminUserResponse.model_validate(user)


@router.post(
    "/users/{user_id}/activate",
    response_model=AdminUserResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def activate_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Activate (re-enable) a user account."""
    try:
        user = await user_manager.activate_user(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="user.activate",
        target_type="user",
        target_id=user_id,
        details={"is_active": user.is_active},
        ip_address=client_ip(request),
    )
    return AdminUserResponse.model_validate(user)


@router.post(
    "/users/{user_id}/deactivate",
    response_model=AdminUserResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def deactivate_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    admin: User = Depends(require_admin),
):
    """Deactivate a user account."""
    try:
        user = await user_manager.deactivate_user_by_id(db, user_id, redis=redis)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="user.deactivate",
        target_type="user",
        target_id=user_id,
        details={"is_active": user.is_active, "tokens_revoked": True},
        ip_address=client_ip(request),
    )
    return AdminUserResponse.model_validate(user)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    admin: User = Depends(require_admin),
):
    """Delete a user account and all dependent data (admin only)."""
    await revoke_all_user_refresh_tokens(redis, user_id)

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="user.delete",
        target_type="user",
        target_id=user_id,
        details={},
        ip_address=client_ip(request),
    )

    try:
        await user_manager.delete_user(db, user_id)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/tracks/{track_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_track(
    track_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Delete any track as an admin."""
    track = await music.get_track(db, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track not found")

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="track.admin_delete",
        target_type="track",
        target_id=track_id,
        details={"title": track.title, "owner_id": track.owner_id},
        ip_address=client_ip(request),
    )

    artist = track.artist
    owner_id = track.owner_id
    was_public = track.visibility == Visibility.PUBLIC.value
    object_id = track.federation_object_id

    await db.delete(track)
    await db.commit()

    if was_public and owner_id and artist is not None:
        owner = await get_user_by_id(db, owner_id)
        if owner is not None:
            background_tasks.add_task(
                unpublish_track_activity,
                track,
                artist,
                owner,
                request.app.state.config,
                object_id,
            )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/audit",
    response_model=List[AuditLogResponse],
    dependencies=[Depends(require_admin)],
)
async def list_audit_logs(
    response: Response,
    action: Optional[str] = Query(None),
    actor_id: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List audit log entries (admin only)."""
    logs, total = await audit.list_audit_logs(
        db,
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        limit=limit,
        offset=offset,
    )
    Pagination(limit=limit, offset=offset).set_total(response, total)
    return [AuditLogResponse.model_validate(log) for log in logs]


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


@router.get(
    "/invites",
    response_model=List[AdminInviteResponse],
    dependencies=[Depends(require_admin)],
)
async def list_invites(
    response: Response,
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List invite codes (admin only)."""
    total = await invite_service.count_invites(db)
    invites = await invite_service.list_invites(db, limit=pagination.limit, offset=pagination.offset)
    pagination.set_total(response, total)
    return [AdminInviteResponse.model_validate(invite) for invite in invites]


@router.post(
    "/invites",
    response_model=AdminInviteResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def create_invite(
    body: AdminInviteCreateRequest,
    request: Request,
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

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="invite.create",
        target_type="invite",
        target_id=invite.id,
        details={
            "code": invite.code,
            "max_uses": body.max_uses,
            "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        },
        ip_address=client_ip(request),
    )
    return AdminInviteResponse.model_validate(invite)


@router.delete(
    "/invites/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_invite(
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Revoke an invite code (admin only)."""
    invite = await invite_service.get_invite(db, code)
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    await invite_service.revoke_invite(db, code)

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="invite.delete",
        target_type="invite",
        target_id=invite.id,
        details={"code": invite.code},
        ip_address=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class AdminOAuthClientResponse(BaseModel):
    """Admin-facing OAuth2 client record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    name: str
    redirect_uris: List[str]
    grant_types: List[str]
    owner_id: Optional[str]
    is_confidential: bool
    created_at: datetime
    updated_at: datetime


class AdminOAuthClientCreateResponse(AdminOAuthClientResponse):
    """OAuth2 client record including the plaintext secret returned once."""

    client_secret: Optional[str] = None


class AdminOAuthClientCreateRequest(BaseModel):
    """Request body for creating an OAuth2 client."""

    name: str = Field(..., min_length=1, max_length=128)
    redirect_uris: List[str] = Field(..., min_length=1)
    grant_types: Optional[List[str]] = None
    is_confidential: bool = True
    owner_id: Optional[str] = None

    @field_validator("redirect_uris", mode="before")
    @classmethod
    def _ensure_list(cls, value):
        """Allow a single redirect URI to be supplied as a string."""
        if isinstance(value, str):
            return [value]
        return value


@router.get(
    "/oauth/clients",
    response_model=List[AdminOAuthClientResponse],
    dependencies=[Depends(require_admin)],
)
async def list_oauth_clients(
    response: Response,
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List OAuth2 clients (admin only)."""
    total = await oauth_client_service.count_oauth_clients(db)
    clients = await oauth_client_service.list_oauth_clients(db, limit=pagination.limit, offset=pagination.offset)
    pagination.set_total(response, total)
    return [AdminOAuthClientResponse.model_validate(client) for client in clients]


@router.get(
    "/oauth/clients/{client_id}",
    response_model=AdminOAuthClientResponse,
    dependencies=[Depends(require_admin)],
)
async def get_oauth_client(
    client_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single OAuth2 client (admin only)."""
    client = await oauth_client_service.get_oauth_client_by_client_id(db, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OAuth client not found")
    return AdminOAuthClientResponse.model_validate(client)


@router.post(
    "/oauth/clients",
    response_model=AdminOAuthClientCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def create_oauth_client(
    body: AdminOAuthClientCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Create a new OAuth2 client (admin only)."""
    try:
        client, client_secret = await oauth_client_service.create_oauth_client(
            db,
            created_by=admin.id,
            name=body.name,
            redirect_uris=body.redirect_uris,
            grant_types=body.grant_types,
            is_confidential=body.is_confidential,
            owner_id=body.owner_id,
        )
    except oauth_client_service.OAuthClientError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="oauth_client.create",
        target_type="oauth_client",
        target_id=client.id,
        details={"client_id": client.client_id, "name": client.name},
        ip_address=client_ip(request),
    )

    response = AdminOAuthClientCreateResponse.model_validate(client)
    response.client_secret = client_secret
    return response


@router.delete(
    "/oauth/clients/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_oauth_client(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Delete an OAuth2 client (admin only)."""
    client = await oauth_client_service.get_oauth_client_by_client_id(db, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OAuth client not found")

    await oauth_client_service.delete_oauth_client(db, client_id)

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="oauth_client.delete",
        target_type="oauth_client",
        target_id=client.id,
        details={"client_id": client.client_id, "name": client.name},
        ip_address=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SettingResponse(BaseModel):
    """Admin-facing setting record."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    value: Any
    type: str
    updated_at: Optional[datetime]


class SettingUpdateRequest(BaseModel):
    """Request body for updating a setting."""

    value: Any


@router.get(
    "/settings",
    response_model=List[SettingResponse],
    dependencies=[Depends(require_admin)],
)
async def list_settings(
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """List all runtime settings (admin only)."""
    settings = await settings_service.list_settings(db, redis)
    response.headers["X-Total-Count"] = str(len(settings))
    return settings


@router.put(
    "/settings/{key}",
    response_model=SettingResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def update_setting(
    key: str,
    body: SettingUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    admin: User = Depends(require_admin),
):
    """Update a runtime setting (admin only)."""
    if key not in settings_service.ALLOWED_SETTINGS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown setting key")

    old_value = await settings_service.get_setting(db, redis, key)
    try:
        row = await settings_service.set_setting(db, redis, key, body.value, updated_by=admin.id)
    except settings_service.SettingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="settings.update",
        target_type="setting",
        target_id=key,
        details={"old_value": old_value, "new_value": body.value},
        ip_address=client_ip(request),
    )

    # Refresh app state from the DB; the setting was just written.
    request.app.state.config = await settings_service.apply_settings_overrides(db, request.app.state.config)

    return SettingResponse(
        key=row.key,
        value=body.value,
        type=settings_service.ALLOWED_SETTINGS[key]["type"],
        updated_at=row.updated_at,
    )


@router.get(
    "/stats",
    dependencies=[Depends(require_admin)],
)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    config: SonghiveConfig = Depends(get_config),
):
    """Return system health and content statistics (admin only)."""
    return await stats_service.get_all_stats(db, config, redis)


class BulkUserActionRequest(BaseModel):
    """Request body for bulk user actions."""

    action: str
    user_ids: list[str] = Field(..., min_length=1, max_length=100)

    @field_validator("action")
    @classmethod
    def _validate_action(cls, value: str) -> str:
        if value not in {"deactivate", "activate", "delete"}:
            raise ValueError("action must be one of: deactivate, activate, delete")
        return value


class BulkUserActionResponse(BaseModel):
    """Response returned after a bulk user action."""

    action: str
    processed: int
    failed: list[dict]


@router.post(
    "/users/bulk",
    response_model=BulkUserActionResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def bulk_user_action(
    body: BulkUserActionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    admin: User = Depends(require_admin),
):
    """Apply a bulk action to a list of users (admin only)."""
    failed: list[dict] = []

    for user_id in body.user_ids:
        try:
            async with db.begin_nested():
                if body.action == "deactivate":
                    await user_manager.deactivate_user_by_id(db, user_id, redis=redis)
                elif body.action == "activate":
                    await user_manager.activate_user(db, user_id)
                elif body.action == "delete":
                    await revoke_all_user_refresh_tokens(redis, user_id)
                    await user_manager.delete_user(db, user_id)
        except (user_manager.UserManagementError, SQLAlchemyError, RedisConnectionError) as exc:
            failed.append({"user_id": user_id, "error": str(exc)})
            continue

        await audit.log_action(
            db,
            actor_id=admin.id,
            action=f"user.bulk_{body.action}",
            target_type="user",
            target_id=user_id,
            details={},
            ip_address=client_ip(request),
        )

    return BulkUserActionResponse(
        action=body.action,
        processed=len(body.user_ids) - len(failed),
        failed=failed,
    )


@router.post(
    "/storage/cleanup",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_account)],
)
async def storage_cleanup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Trigger the orphaned-files cleanup Celery task (admin only)."""
    try:
        result = cleanup_orphaned_files.delay()  # type: ignore
    except (KombuOperationalError, RedisConnectionError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Celery broker unavailable",
        ) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="storage.cleanup_trigger",
        target_type="storage",
        target_id=None,
        details={},
        ip_address=client_ip(request),
    )
    await db.commit()

    return {"task_id": result.id, "status": "queued"}
