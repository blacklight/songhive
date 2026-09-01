"""Admin external library routes."""

import dataclasses
from datetime import datetime, timezone
from typing import Any, List, Optional, cast

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...external.errors import ExternalItemNotFound
from ...external.registry import (
    get_external_adapter,
    is_user_configurable,
    list_external_provider_types,
)
from ...external.sync import _find_or_create_library_track
from ...models import ExternalLibrary, ExternalSyncRun, ExternalTrack
from ...models.user import User
from ...services import audit, deletion
from ...services.secrets import redact_config
from ...services.storage import StorageService
from ...tasks.external_libraries import sync_external_library_task
from .._common import Pagination, client_ip, get_pagination
from ..deps import get_db, get_storage_service, require_admin
from ..middleware.rate_limit import rate_limit_account
from .external_libraries import (
    BulkExternalTrackDeleteRequest,
    ExternalLibraryCreate,
    ExternalLibraryResponse,
    ExternalLibraryUpdate,
    ExternalProviderResponse,
    ExternalSyncRequest,
    ExternalSyncRunResponse,
    ExternalTrackDeleteRequest,
    ExternalTrackResponse,
    _build_external_library_response,
    _build_external_track_response,
    _create_external_library,
    _decrypt_external_config,
    _delete_track_source,
    _item_ref_from_track,
    _load_external_library,
    _load_external_track,
    _mark_track_tombstoned,
    _mutation_to_dict,
    _provider_capabilities_summary,
    _provider_item_exists,
    _redact_audit_details,
    _sanitize_error,
    _validate_and_encrypt_config,
)

admin_router = APIRouter(prefix="/admin/external-libraries")


@admin_router.get(
    "/providers",
    response_model=List[ExternalProviderResponse],
    dependencies=[Depends(rate_limit_account), Depends(require_admin)],
)
async def list_admin_providers(
    admin: User = Depends(require_admin),
):
    """List all registered external-library provider types for admins."""
    providers: List[ExternalProviderResponse] = []
    for provider_type in list_external_provider_types():
        summary = await _provider_capabilities_summary(provider_type)
        providers.append(
            ExternalProviderResponse(
                provider_type=provider_type,
                user_configurable=is_user_configurable(provider_type),
                capabilities_summary=summary,
            )
        )
    return providers


@admin_router.get("/", response_model=List[ExternalLibraryResponse])
async def list_admin_external_libraries(
    response: Response,
    include_user: bool = Query(False),
    admin: User = Depends(require_admin),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List admin-scoped external libraries, optionally including user-scoped rows."""
    from sqlalchemy import or_

    stmt = select(ExternalLibrary).options(selectinload(ExternalLibrary.library))
    total_stmt = select(func.count(ExternalLibrary.id))

    if include_user:
        stmt = stmt.where(or_(ExternalLibrary.scope == "admin", ExternalLibrary.scope == "user"))
        total_stmt = total_stmt.where(or_(ExternalLibrary.scope == "admin", ExternalLibrary.scope == "user"))
    else:
        stmt = stmt.where(ExternalLibrary.scope == "admin")
        total_stmt = total_stmt.where(ExternalLibrary.scope == "admin")

    stmt = stmt.order_by(ExternalLibrary.created_at.desc()).offset(pagination.offset).limit(pagination.limit)
    result = await db.execute(stmt)
    total = (await db.execute(total_stmt)).scalar() or 0
    pagination.set_total(response, total)

    return [await _build_external_library_response(lib, admin, db) for lib in result.scalars().all()]


@admin_router.post(
    "/",
    response_model=ExternalLibraryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def create_admin_external_library(
    body: ExternalLibraryCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new admin-scoped external library."""
    config: SonghiveConfig = request.app.state.config
    allow_include_in_index = config.external_libraries.allow_admin_library_index_inclusion

    external_library = await _create_external_library(
        db,
        request,
        admin,
        body,
        scope="admin",
        allow_include_in_index=allow_include_in_index,
        audit_action="external_library.admin_create",
    )
    return await _build_external_library_response(external_library, admin, db)


@admin_router.get("/{external_library_id}", response_model=ExternalLibraryResponse)
async def get_admin_external_library(
    external_library_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get any external library for moderation."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return await _build_external_library_response(external_library, admin, db)


@admin_router.patch(
    "/{external_library_id}",
    response_model=ExternalLibraryResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def update_admin_external_library(
    external_library_id: str,
    body: ExternalLibraryUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an admin-scoped external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    config: SonghiveConfig = request.app.state.config
    allow_include_in_index = config.external_libraries.allow_admin_library_index_inclusion

    changes: dict = {}
    if body.name is not None:
        external_library.name = body.name
        changes["name"] = body.name
    if body.enabled is not None:
        external_library.enabled = body.enabled
        changes["enabled"] = body.enabled
    if body.sync_enabled is not None:
        external_library.sync_enabled = body.sync_enabled
        changes["sync_enabled"] = body.sync_enabled
    if body.sync_interval_seconds is not None:
        external_library.sync_interval_seconds = body.sync_interval_seconds
        changes["sync_interval_seconds"] = body.sync_interval_seconds

    if body.include_in_library_index is not None:
        if body.include_in_library_index and not allow_include_in_index:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="include_in_library_index is not allowed",
            )
        external_library.include_in_library_index = body.include_in_library_index
        changes["include_in_library_index"] = body.include_in_library_index

    if body.config is not None:
        encrypted, capabilities = await _validate_and_encrypt_config(
            external_library.provider_type,
            body.config,
        )
        external_library.config = cast(Any, encrypted)
        external_library.capabilities = dataclasses.asdict(capabilities)
        changes["config_changed"] = True

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="external_library.admin_update",
        target_type="external_library",
        target_id=external_library_id,
        details=changes,
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(external_library)

    return await _build_external_library_response(external_library, admin, db)


@admin_router.delete(
    "/{external_library_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_admin_external_library(
    external_library_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete any external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="external_library.admin_delete",
        target_type="external_library",
        target_id=external_library_id,
        details={"provider_type": external_library.provider_type, "scope": external_library.scope},
        ip_address=client_ip(request),
    )
    await db.delete(external_library)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.post(
    "/{external_library_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_account)],
)
async def sync_admin_external_library(
    external_library_id: str,
    request: Request,
    body: ExternalSyncRequest = Body(default_factory=ExternalSyncRequest),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a manual sync for any external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    run = ExternalSyncRun(
        external_library_id=external_library_id,
        triggered_by="manual",
        triggered_by_user_id=admin.id,
        status="queued",
    )
    db.add(run)
    await db.flush()

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="external_library.admin_sync",
        target_type="external_library",
        target_id=external_library_id,
        details={"include_tombstones": body.include_tombstones},
        ip_address=client_ip(request),
    )

    try:
        sync_external_library_task.delay(
            external_library_id,
            "manual",
            str(admin.id),
            body.include_tombstones,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not enqueue sync task: {_sanitize_error(exc)}",
        ) from exc

    await db.commit()
    return {"sync_run_id": str(run.id)}


@admin_router.get("/{external_library_id}/sync-runs", response_model=List[ExternalSyncRunResponse])
async def list_admin_external_sync_runs(
    external_library_id: str,
    response: Response,
    admin: User = Depends(require_admin),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List sync runs for any external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    stmt = (
        select(ExternalSyncRun)
        .where(ExternalSyncRun.external_library_id == external_library_id)
        .order_by(ExternalSyncRun.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    total_stmt = select(func.count(ExternalSyncRun.id)).where(
        ExternalSyncRun.external_library_id == external_library_id
    )

    result = await db.execute(stmt)
    total = (await db.execute(total_stmt)).scalar() or 0
    pagination.set_total(response, total)

    return [ExternalSyncRunResponse.model_validate(run) for run in result.scalars().all()]


@admin_router.get("/{external_library_id}/tracks", response_model=List[ExternalTrackResponse])
async def list_admin_external_tracks(
    external_library_id: str,
    response: Response,
    state: Optional[str] = Query(None),
    admin: User = Depends(require_admin),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List external tracks for any external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    valid_states = {"active", "shadowed", "tombstoned", "missing", "error"}
    if state is not None and state not in valid_states:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid state: {state}",
        )

    stmt = select(ExternalTrack).where(ExternalTrack.external_library_id == external_library_id)
    total_stmt = select(func.count(ExternalTrack.id)).where(ExternalTrack.external_library_id == external_library_id)
    if state:
        stmt = stmt.where(ExternalTrack.state == state)
        total_stmt = total_stmt.where(ExternalTrack.state == state)

    stmt = stmt.order_by(ExternalTrack.provider_key).offset(pagination.offset).limit(pagination.limit)
    result = await db.execute(stmt)
    total = (await db.execute(total_stmt)).scalar() or 0
    pagination.set_total(response, total)

    return [_build_external_track_response(track) for track in result.scalars().all()]


@admin_router.post(
    "/{external_library_id}/tracks/{external_track_id}/restore",
    response_model=ExternalTrackResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def restore_admin_external_track(
    external_library_id: str,
    external_track_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Restore a tombstoned external track."""
    external_library, external_track = await _load_external_track(db, external_library_id, external_track_id)

    if external_track.state != "tombstoned":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Track is not tombstoned",
        )

    decrypted = _decrypt_external_config(external_library.config)
    try:
        adapter_cls = get_external_adapter(external_library.provider_type)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown provider") from exc

    adapter = adapter_cls()
    await adapter.validate_config(decrypted)
    item = _item_ref_from_track(external_track)

    if not await _provider_item_exists(adapter, decrypted, item):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider item not found",
        )

    external_track.state = "active"
    external_track.sync_error = None
    external_track.last_seen_at = datetime.now(timezone.utc)

    if external_track.track_id:
        await _find_or_create_library_track(
            db,
            external_library.library_id,
            external_track.track_id,
            admin.id,
        )

    await audit.log_action(
        db,
        actor_id=admin.id,
        action="external_track.admin_restore",
        target_type="external_track",
        target_id=external_track_id,
        details={
            "provider_key": external_track.provider_key,
            "external_library_id": external_library_id,
            "state": "active",
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    return _build_external_track_response(external_track)


@admin_router.delete(
    "/{external_library_id}/tracks/{external_track_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_admin_external_track(
    external_library_id: str,
    external_track_id: str,
    request: Request,
    body: ExternalTrackDeleteRequest = Body(default_factory=ExternalTrackDeleteRequest),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Tombstone or destructively delete an external track as an admin."""
    external_library, external_track = await _load_external_track(db, external_library_id, external_track_id)

    if not body.delete_source:
        await _mark_track_tombstoned(db, external_library, external_track)
        details = _redact_audit_details(
            {"external_library_id": external_library_id, "delete_source": False},
            external_track.provider_key,
        )
        await audit.log_action(
            db,
            actor_id=admin.id,
            action="external_track.admin_tombstone",
            target_type="external_track",
            target_id=external_track_id,
            details=details,
            ip_address=client_ip(request),
        )
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    config: SonghiveConfig = request.app.state.config
    try:
        mutation = await _delete_track_source(db, storage, config, external_library, external_track, body)
    except HTTPException:
        raise
    except deletion.DeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.args[0]) from exc
    except Exception as exc:
        external_track.sync_error = _sanitize_error(exc)
        details = _redact_audit_details(
            {
                "external_library_id": external_library_id,
                "delete_source": True,
                "remove_songhive_track": body.remove_songhive_track,
                "success": False,
                "error": _sanitize_error(exc),
            },
            external_track.provider_key,
        )
        await audit.log_action(
            db,
            actor_id=admin.id,
            action="external_track.admin_delete_source",
            target_type="external_track",
            target_id=external_track_id,
            details=details,
            ip_address=client_ip(request),
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_sanitize_error(exc),
        ) from exc

    success_details = {
        "external_library_id": external_library_id,
        "delete_source": True,
        "remove_songhive_track": body.remove_songhive_track,
        "success": True,
    }
    if mutation is not None:
        success_details["mutation"] = redact_config(_mutation_to_dict(mutation))
    details = _redact_audit_details(success_details, external_track.provider_key)
    await audit.log_action(
        db,
        actor_id=admin.id,
        action="external_track.admin_delete_source",
        target_type="external_track",
        target_id=external_track_id,
        details=details,
        ip_address=client_ip(request),
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.post(
    "/{external_library_id}/tracks/bulk-delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def bulk_delete_admin_external_tracks(
    external_library_id: str,
    request: Request,
    body: BulkExternalTrackDeleteRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Tombstone or destructively delete multiple external tracks."""
    if not body.external_track_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="external_track_ids is required",
        )

    external_library = await _load_external_library(db, external_library_id)
    if external_library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    result = await db.execute(
        select(ExternalTrack).where(
            ExternalTrack.id.in_(body.external_track_ids),
            ExternalTrack.external_library_id == external_library_id,
        )
    )
    tracks = {str(track.id): track for track in result.scalars().all()}
    if len(tracks) != len(body.external_track_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track not found")

    affected_track_ids: List[str] = []
    config: SonghiveConfig = request.app.state.config

    if body.delete_source:
        if not body.confirm or body.confirm != "DELETE":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Confirm must be 'DELETE'",
            )
        if not config.external_libraries.allow_destructive_delete:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Destructive source deletion is disabled",
            )
        capabilities = external_library.capabilities or {}
        if not capabilities.get("delete_source"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Adapter does not support source deletion",
            )

        decrypted = _decrypt_external_config(external_library.config)
        try:
            adapter_cls = get_external_adapter(external_library.provider_type)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown provider") from exc

        adapter = adapter_cls()
        await adapter.validate_config(decrypted)

        for track_id in body.external_track_ids:
            external_track = tracks[track_id]

            if external_track.state != "missing":
                item = _item_ref_from_track(external_track)
                try:
                    await adapter.delete_source(decrypted, item)
                except ExternalItemNotFound:
                    pass
                except Exception as exc:
                    external_track.sync_error = _sanitize_error(exc)
                    await db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"{track_id}: {_sanitize_error(exc)}",
                    ) from exc

            if body.remove_songhive_track and external_track.track_id:
                try:
                    await deletion.delete_track(db, storage, external_track.track_id, delete_audio_file=False)
                except deletion.DeletionError as exc:
                    if exc.status_code != status.HTTP_404_NOT_FOUND:
                        raise HTTPException(status_code=exc.status_code, detail=exc.args[0]) from exc
                await db.delete(external_track)
            else:
                external_track.state = "missing"
                external_track.last_seen_at = datetime.now(timezone.utc)

            affected_track_ids.append(track_id)

        details = redact_config(
            {
                "external_library_id": external_library_id,
                "delete_source": True,
                "remove_songhive_track": body.remove_songhive_track,
                "external_track_ids": affected_track_ids,
                "count": len(affected_track_ids),
            }
        )
        await audit.log_action(
            db,
            actor_id=admin.id,
            action="external_track.bulk_delete_source",
            target_type="external_library",
            target_id=external_library_id,
            details=details,
            ip_address=client_ip(request),
        )
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    for track_id in body.external_track_ids:
        external_track = tracks[track_id]
        await _mark_track_tombstoned(db, external_library, external_track)
        affected_track_ids.append(track_id)

    details = redact_config(
        {
            "external_library_id": external_library_id,
            "delete_source": False,
            "external_track_ids": affected_track_ids,
            "count": len(affected_track_ids),
        }
    )
    await audit.log_action(
        db,
        actor_id=admin.id,
        action="external_track.bulk_tombstone",
        target_type="external_library",
        target_id=external_library_id,
        details=details,
        ip_address=client_ip(request),
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
