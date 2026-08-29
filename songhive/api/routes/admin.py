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
from ...models.user import User, UserRole
from ...services import audit, auth, deletion, music
from ...services import settings as settings_service
from ...services import stats as stats_service
from ...services.celery_admin import CeleryAdminError, list_active_celery_tasks, terminate_celery_tasks
from ...services.federation import unpublish_track_activity
from ...services.storage import StorageService
from ...tasks.federation import provision_federation_keys
from ...tasks.storage import cleanup_orphaned_files, rehash_audio_files
from ...tasks.tags import sync_track_tags
from ...users import invites as invite_service
from ...users import manager as user_manager
from ...users import oauth as oauth_client_service
from ...users.tokens import revoke_all_user_refresh_tokens
from .._common import Pagination, client_ip, get_pagination
from ..deps import get_config, get_db, get_redis, get_storage_service, require_admin
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
    actor_name: Optional[str] = None
    actor_username: Optional[str] = None
    target_type: Optional[str]
    target_id: Optional[str]
    target_name: Optional[str] = None
    target_username: Optional[str] = None
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
    q: Optional[str] = Query(None, max_length=128),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List all users (admin only), optionally filtering by username or email."""
    q = q.strip() if q else None
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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    admin: User = Depends(require_admin),
    storage: StorageService = Depends(get_storage_service),
    recursive: bool = Query(False, description="Also delete all content created by the user"),
):
    """Delete a user account and all dependent data (admin only)."""
    target_user = await auth.get_user_by_id(db, user_id)
    await revoke_all_user_refresh_tokens(redis, user_id)

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="user.delete",
        target_type="user",
        target_id=user_id,
        details={
            "recursive": recursive,
            "username": target_user.username if target_user else None,
            "display_name": target_user.display_name if target_user else None,
        },
        ip_address=client_ip(request),
    )

    try:
        unpublish = await user_manager.delete_user(db, user_id, recursive=recursive, storage=storage)
    except user_manager.UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    if unpublish:
        config: SonghiveConfig = request.app.state.config
        for info in unpublish:
            if info.artist is not None and info.owner is not None:
                background_tasks.add_task(
                    unpublish_track_activity,
                    info.track,
                    info.artist,
                    info.owner,
                    config,
                    info.federation_object_id,
                )

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
    storage: StorageService = Depends(get_storage_service),
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

    try:
        unpublish = await deletion.delete_track(db, storage, track_id)
    except deletion.DeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.args[0]) from exc

    await db.commit()

    if unpublish is not None and unpublish.owner is not None and unpublish.artist is not None:
        background_tasks.add_task(
            unpublish_track_activity,
            unpublish.track,
            unpublish.artist,
            unpublish.owner,
            request.app.state.config,
            unpublish.federation_object_id,
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
    enriched = await audit.enrich_audit_logs(db, logs)
    Pagination(limit=limit, offset=offset).set_total(response, total)
    return [AuditLogResponse.model_validate(data) for data in enriched]


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
    recursive: bool = Field(False, description="Also delete all content created by the user (delete only)")

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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    admin: User = Depends(require_admin),
    storage: StorageService = Depends(get_storage_service),
):
    """Apply a bulk action to a list of users (admin only)."""
    failed: list[dict] = []
    unpublish: list[deletion.UnpublishInfo] = []
    config: SonghiveConfig = request.app.state.config

    for user_id in body.user_ids:
        try:
            async with db.begin_nested():
                if body.action == "deactivate":
                    await user_manager.deactivate_user_by_id(db, user_id, redis=redis)
                elif body.action == "activate":
                    await user_manager.activate_user(db, user_id)
                elif body.action == "delete":
                    await revoke_all_user_refresh_tokens(redis, user_id)
                    user_unpublish = await user_manager.delete_user(
                        db, user_id, recursive=body.recursive, storage=storage
                    )
                    unpublish.extend(user_unpublish)
        except (user_manager.UserManagementError, SQLAlchemyError, RedisConnectionError) as exc:
            failed.append({"user_id": user_id, "error": str(exc)})
            continue

        await audit.log_action(
            db,
            actor_id=admin.id,
            action=f"user.bulk_{body.action}",
            target_type="user",
            target_id=user_id,
            details={"recursive": body.recursive} if body.action == "delete" else {},
            ip_address=client_ip(request),
        )

    for info in unpublish:
        if info.artist is not None and info.owner is not None:
            background_tasks.add_task(
                unpublish_track_activity,
                info.track,
                info.artist,
                info.owner,
                config,
                info.federation_object_id,
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


class SyncTagsRequest(BaseModel):
    """Request body for bulk tag sync triggers."""

    track_id: Optional[str] = None
    album_id: Optional[str] = None
    artist_id: Optional[str] = None
    library_id: Optional[str] = None
    all: bool = False
    dry_run: bool = False


class SyncTagsResponse(BaseModel):
    """Response returned after a bulk tag sync trigger."""

    enqueued: int
    status: str


@router.post(
    "/sync-tags",
    response_model=SyncTagsResponse,
    dependencies=[Depends(rate_limit_account), Depends(require_admin)],
)
async def sync_tags(
    body: SyncTagsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Enqueue tag sync for one or more tracks (admin only)."""
    if not any((body.track_id, body.album_id, body.artist_id, body.library_id, body.all)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one scope is required")

    track_ids = await music.resolve_track_ids_for_sync(
        db,
        track_id=body.track_id,
        album_id=body.album_id,
        artist_id=body.artist_id,
        library_id=body.library_id,
        all_=body.all,
        user=admin,
    )

    enqueued = 0
    broker_error: Optional[Exception] = None
    if not body.dry_run:
        try:
            for track_id in track_ids:
                try:
                    sync_track_tags.delay(track_id)  # type: ignore
                    enqueued += 1
                except (KombuOperationalError, RedisConnectionError, OSError) as exc:
                    broker_error = exc
                    break
        except (KombuOperationalError, RedisConnectionError, OSError) as exc:
            broker_error = exc

    scope = {
        "track_id": body.track_id,
        "album_id": body.album_id,
        "artist_id": body.artist_id,
        "library_id": body.library_id,
        "all": body.all,
    }
    await audit.log_action(
        db,
        actor_id=admin.id,
        action="tags.sync",
        target_type="storage",
        target_id=None,
        details={"scope": scope, "enqueued": enqueued, "dry_run": body.dry_run},
        ip_address=client_ip(request),
    )
    await db.commit()

    if broker_error is not None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Celery broker unavailable",
        ) from broker_error

    run_status = "dry_run" if body.dry_run else "queued"
    return SyncTagsResponse(enqueued=enqueued, status=run_status)


class RehashAudioRequest(BaseModel):
    """Request body for triggering an audio rehash task."""

    dry_run: bool = False


class AdminTaskQueuedResponse(BaseModel):
    """Response returned after queueing an admin background task."""

    task_id: Optional[str] = None
    status: str


@router.post(
    "/rehash-audio",
    response_model=AdminTaskQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_account)],
)
async def rehash_audio(
    body: RehashAudioRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Trigger the audio-only SHA-256 rehash Celery task (admin only)."""
    try:
        result = rehash_audio_files.delay(dry_run=body.dry_run)  # type: ignore
    except (KombuOperationalError, RedisConnectionError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Celery broker unavailable",
        ) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="storage.rehash_audio",
        target_type="storage",
        target_id=None,
        details={"dry_run": body.dry_run},
        ip_address=client_ip(request),
    )
    await db.commit()

    return {"task_id": result.id, "status": "queued"}


class ProvisionFederationKeysRequest(BaseModel):
    """Request body for triggering federation key provisioning."""

    dry_run: bool = False


@router.post(
    "/provision-federation-keys",
    response_model=AdminTaskQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_account)],
)
async def provision_federation_keys_endpoint(
    body: ProvisionFederationKeysRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Trigger the federation key provisioning Celery task (admin only)."""
    try:
        result = provision_federation_keys.delay(dry_run=body.dry_run)  # type: ignore
    except (KombuOperationalError, RedisConnectionError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Celery broker unavailable",
        ) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="federation.provision_keys",
        target_type="federation",
        target_id=None,
        details={"dry_run": body.dry_run},
        ip_address=client_ip(request),
    )
    await db.commit()

    return {"task_id": result.id, "status": "queued"}


class CeleryTaskInfo(BaseModel):
    """A currently running Celery task reported by the worker inspect API."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    name: str
    worker: str
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    runtime: Optional[float] = None
    hostname: Optional[str] = None
    acknowledged: Optional[bool] = None
    delivery_info: Optional[dict[str, Any]] = None
    time_start: Optional[float] = None


class CeleryTerminateRequest(BaseModel):
    """Request body for terminating running Celery tasks."""

    task_ids: list[str] = Field(..., min_length=1, max_length=100)


class CeleryTerminateResponse(BaseModel):
    """Response returned after a bulk Celery terminate request."""

    terminated: int


@router.get(
    "/celery/tasks",
    response_model=list[CeleryTaskInfo],
    dependencies=[Depends(require_admin)],
)
async def list_celery_tasks():
    """List all Celery tasks currently running on workers (admin only)."""
    try:
        return await list_active_celery_tasks()
    except CeleryAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.post(
    "/celery/terminate",
    response_model=CeleryTerminateResponse,
    dependencies=[Depends(rate_limit_account), Depends(require_admin)],
)
async def terminate_celery_tasks_endpoint(
    body: CeleryTerminateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Terminate one or more running Celery tasks by id (admin only)."""
    try:
        terminated = await terminate_celery_tasks(body.task_ids)
    except CeleryAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="celery.terminate",
        target_type="celery",
        target_id=None,
        details={"task_ids": body.task_ids, "count": terminated},
        ip_address=client_ip(request),
    )
    await db.commit()

    return CeleryTerminateResponse(terminated=terminated)
