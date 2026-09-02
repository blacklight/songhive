"""User-facing external library routes."""

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional, cast

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...config.schema import SonghiveConfig
from ...external.base import ExternalLibraryAdapter
from ...external.errors import (
    ExternalItemNotFound,
    UnsupportedExternalOperation,
)
from ...external.registry import (
    get_external_adapter,
    is_user_configurable,
    list_external_provider_types,
)
from ...external.sync import (
    _find_or_create_library_track,
    _remove_library_track,
)
from ...external.types import ExternalItemRef, ExternalMutationResult
from ...models import ExternalLibrary, ExternalSyncRun, ExternalTrack, Library
from ...models._enums import Visibility
from ...models.user import User
from ...services import acl, audit, deletion
from ...services.federation import unpublish_track_activity
from ...services.secrets import decrypt_json, encrypt_json, redact_config
from ...services.storage import StorageService
from ...tasks.external_libraries import sync_external_library_task
from .._common import Pagination, client_ip, get_pagination
from ..deps import (
    get_current_user,
    get_db,
    get_storage_service,
)
from ..middleware.rate_limit import rate_limit_account

router = APIRouter(prefix="/external-libraries")
logger = logging.getLogger(__name__)


class ExternalLibraryCreate(BaseModel):
    """Request body for creating an external library."""

    provider_type: str
    name: Optional[str] = None
    config: dict
    library_id: Optional[str] = None
    library_name: Optional[str] = None
    visibility: Optional[Visibility] = Visibility.PRIVATE
    enabled: bool = True
    sync_enabled: bool = True
    sync_interval_seconds: Optional[int] = None
    include_in_library_index: bool = False

    @model_validator(mode="after")
    def _check_library_source(self):
        """Ensure the caller does not attach and create a library at once."""
        if self.library_id and self.library_name:
            raise ValueError("Provide either library_id or library_name, not both")
        return self


class ExternalLibraryUpdate(BaseModel):
    """Partial update payload for an external library."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None
    sync_enabled: Optional[bool] = None
    sync_interval_seconds: Optional[int] = None
    include_in_library_index: Optional[bool] = None


class ExternalLibraryCapabilitiesResponse(BaseModel):
    """Capability summary for an external library."""

    model_config = ConfigDict(from_attributes=True)

    list_items: bool = False
    read_bytes: bool = False
    stream_url: bool = False
    range_read: bool = False
    download: bool = False
    compute_hash: bool = False
    read_tags: bool = False
    write_tags: bool = False
    delete_source: bool = False
    detect_changes: bool = False
    validate_config: bool = False
    limits: Optional[dict] = None


class ExternalLibraryResponse(BaseModel):
    """External library response with redacted config."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    library_id: str
    provider_type: str
    scope: str
    name: Optional[str] = None
    config: dict
    enabled: bool
    include_in_library_index: bool
    sync_enabled: bool
    sync_interval_seconds: Optional[int] = None
    last_sync_started_at: Optional[datetime] = None
    last_sync_completed_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_sync_error: Optional[str] = None
    capabilities: Optional[ExternalLibraryCapabilitiesResponse] = None
    created_by_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    can_manage: bool = False
    can_sync: bool = False


class ExternalSyncRunResponse(BaseModel):
    """External sync run response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    external_library_id: str
    triggered_by: str
    triggered_by_user_id: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    items_seen: int = 0
    tracks_created: int = 0
    tracks_updated: int = 0
    tracks_shadowed: int = 0
    tracks_tombstoned: int = 0
    tracks_missing: int = 0
    tracks_failed: int = 0
    error: Optional[str] = None
    details: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class ExternalSyncRequest(BaseModel):
    """Request body for a manual sync."""

    include_tombstones: bool = False


class ExternalTrackResponse(BaseModel):
    """External track response."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    external_library_id: str
    track_id: Optional[str] = None
    provider_key: str
    state: str
    sha256: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
    write_back_pending: bool = False
    write_back_error: Optional[str] = None
    sync_error: Optional[str] = None
    display_path: Optional[str] = None


class ExternalTrackDeleteRequest(BaseModel):
    """Request body for tombstoning or destructively deleting an external track."""

    delete_source: bool = False
    confirm: Optional[str] = None
    remove_songhive_track: bool = False


class BulkExternalTrackDeleteRequest(BaseModel):
    """Request body for bulk external-track deletion."""

    external_track_ids: List[str]
    delete_source: bool = False
    confirm: Optional[str] = None
    remove_songhive_track: bool = False


class ExternalDuplicateWarning(BaseModel):
    """Warning when an uploaded file collides with an external track."""

    token: str
    sha256: str
    provider_type: str
    display_info: List[dict] = []


class ExternalDuplicateResolutionRequest(BaseModel):
    """Resolution choice for an external duplicate warning."""

    token: str
    action: Literal["keep_local", "discard_upload"]


class ExternalProviderResponse(BaseModel):
    """Provider type available to the requester."""

    provider_type: str
    user_configurable: bool
    capabilities_summary: dict


def _utcnow() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _sanitize_error(exc: Any) -> str:
    """Return a short, config-free string for an exception or message."""
    if isinstance(exc, str):
        return exc[:512]
    return str(exc)[:512]


def _decrypt_external_config(raw: Any) -> dict:
    """Decrypt an external-library config when it is stored as a Fernet token."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return decrypt_json(raw)
        except Exception:
            logger.warning("Failed to decrypt external library config")
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _redacted_config(raw: Any) -> dict:
    """Return the redacted, decrypted form of an external-library config."""
    return redact_config(_decrypt_external_config(raw))


def _mutation_to_dict(mutation: ExternalMutationResult) -> dict:
    """Return a JSON-safe, redactable dict for an ``ExternalMutationResult``."""
    return {
        "provider_key": mutation.provider_key,
        "etag": mutation.etag,
        "mtime": mutation.mtime.isoformat() if mutation.mtime else None,
        "checksum": mutation.checksum,
        "sha256": mutation.sha256,
    }


def _redact_audit_details(details: dict, provider_key: str) -> dict:
    """Redact a details dict and preserve the non-secret provider key."""
    redacted = redact_config(details)
    redacted["provider_key"] = provider_key
    return redacted


def _to_capabilities_response(caps: Optional[dict]) -> Optional[ExternalLibraryCapabilitiesResponse]:
    """Build a capabilities response from a stored capabilities dict."""
    if not caps:
        return None
    try:
        return ExternalLibraryCapabilitiesResponse(**caps)
    except Exception:
        logger.warning("Failed to parse external library capabilities: %s", caps)
        return None


def _provider_allowed_for_create(
    provider_type: str,
    user: User,
    config: SonghiveConfig,
) -> bool:
    """Return whether the user may create an external library of this type."""
    if not is_user_configurable(provider_type):
        return False
    if user.is_admin:
        return True
    if not config.external_libraries.allow_user_created_libraries:
        return False
    allowed = config.external_libraries.allowed_user_providers
    if allowed and provider_type not in allowed:
        return False
    if provider_type in config.external_libraries.denied_user_providers:
        return False
    return True


async def _build_external_library_response(
    external_library: ExternalLibrary,
    user: Optional[User],
    db: AsyncSession,
) -> ExternalLibraryResponse:
    """Build an ``ExternalLibraryResponse`` with redacted config and permissions."""
    can_manage = False
    if user is not None and external_library.library_id is not None:
        can_manage = await acl.can_manage(db, user, "library", external_library.library_id)

    return ExternalLibraryResponse(
        id=str(external_library.id),
        library_id=str(external_library.library_id),
        provider_type=external_library.provider_type,
        scope=external_library.scope,
        name=external_library.name,
        config=_redacted_config(external_library.config),
        enabled=external_library.enabled,
        include_in_library_index=external_library.include_in_library_index,
        sync_enabled=external_library.sync_enabled,
        sync_interval_seconds=external_library.sync_interval_seconds,
        last_sync_started_at=external_library.last_sync_started_at,
        last_sync_completed_at=external_library.last_sync_completed_at,
        last_sync_status=external_library.last_sync_status,
        last_sync_error=external_library.last_sync_error,
        capabilities=_to_capabilities_response(external_library.capabilities),
        created_by_id=str(external_library.created_by_id) if external_library.created_by_id else None,
        created_at=external_library.created_at,
        updated_at=external_library.updated_at,
        can_manage=can_manage,
        can_sync=can_manage,
    )


def _build_external_track_response(external_track: ExternalTrack) -> ExternalTrackResponse:
    """Build an ``ExternalTrackResponse`` with a derived display path."""
    raw = external_track.raw_metadata or {}
    if isinstance(raw, dict):
        display_path = raw.get("display_path", external_track.provider_key)
    else:
        display_path = external_track.provider_key
    return ExternalTrackResponse(
        id=str(external_track.id),
        external_library_id=str(external_track.external_library_id),
        track_id=str(external_track.track_id) if external_track.track_id else None,
        provider_key=external_track.provider_key,
        state=external_track.state,
        sha256=external_track.sha256,
        last_seen_at=external_track.last_seen_at,
        last_synced_at=external_track.last_synced_at,
        write_back_pending=external_track.write_back_pending,
        write_back_error=external_track.write_back_error,
        sync_error=external_track.sync_error,
        display_path=display_path,
    )


def _item_ref_from_track(external_track: ExternalTrack) -> ExternalItemRef:
    """Build an ``ExternalItemRef`` from an ``ExternalTrack`` row."""
    raw = external_track.raw_metadata or {}
    display_path = (
        raw.get("display_path", external_track.provider_key) if isinstance(raw, dict) else external_track.provider_key
    )
    return ExternalItemRef(
        provider_key=external_track.provider_key,
        display_path=display_path,
        etag=external_track.provider_etag,
        mtime=external_track.provider_mtime,
        size=external_track.provider_size,
        mime_type=external_track.provider_mime_type,
        checksum=external_track.provider_checksum,
        sha256=external_track.sha256,
    )


async def _load_external_library(
    db: AsyncSession,
    external_library_id: str,
) -> Optional[ExternalLibrary]:
    """Load an external library with its underlying library relationship."""
    result = await db.execute(
        select(ExternalLibrary)
        .where(ExternalLibrary.id == external_library_id)
        .options(selectinload(ExternalLibrary.library))
    )
    return result.scalar_one_or_none()


async def _resolve_library_for_create(
    db: AsyncSession,
    user: User,
    body: ExternalLibraryCreate,
) -> Library:
    """Attach to an existing library or create a new one for the external library."""
    if body.library_id:
        library = await db.get(Library, body.library_id)
        if library is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Library not found",
            )
        if not await acl.can_manage(db, user, "library", body.library_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied",
            )
        return library

    name = body.library_name or body.name or f"External {body.provider_type}"
    visibility = (body.visibility or Visibility.PRIVATE).value
    library = Library(
        name=name,
        owner_id=user.id,
        visibility=visibility,
    )
    db.add(library)
    await db.flush()
    return library


async def _validate_and_encrypt_config(
    provider_type: str,
    config: dict,
) -> tuple[str, Any]:
    """Validate config with the adapter and return the encrypted token and capabilities."""
    try:
        adapter_cls = get_external_adapter(provider_type)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown provider type: {provider_type}",
        ) from exc

    adapter: ExternalLibraryAdapter = adapter_cls()
    try:
        capabilities = await adapter.validate_config(config)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_sanitize_error(exc),
        ) from exc

    encrypted = encrypt_json(config)
    return encrypted, capabilities


async def _create_external_library(
    db: AsyncSession,
    request: Request,
    user: User,
    body: ExternalLibraryCreate,
    scope: str,
    allow_include_in_index: bool,
    audit_action: str,
) -> ExternalLibrary:
    """Persist a new external library after validation and encryption."""
    if body.include_in_library_index and not allow_include_in_index:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="include_in_library_index is not allowed",
        )

    library = await _resolve_library_for_create(db, user, body)
    encrypted, capabilities = await _validate_and_encrypt_config(body.provider_type, body.config)

    include_in_index = (
        body.include_in_library_index if allow_include_in_index and body.include_in_library_index is not None else False
    )
    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type=body.provider_type,
        scope=scope,
        name=body.name,
        config=cast(Any, encrypted),
        enabled=body.enabled,
        include_in_library_index=include_in_index,
        sync_enabled=body.sync_enabled,
        sync_interval_seconds=body.sync_interval_seconds,
        created_by_id=user.id,
        capabilities=dataclasses.asdict(capabilities),
    )
    db.add(external_library)
    await db.flush()

    details = {
        "provider_type": body.provider_type,
        "library_id": str(library.id),
        "name": body.name,
        "enabled": body.enabled,
        "sync_enabled": body.sync_enabled,
        "sync_interval_seconds": body.sync_interval_seconds,
        "include_in_library_index": include_in_index,
    }
    await audit.log_action(
        db,
        actor_id=user.id,
        action=audit_action,
        target_type="external_library",
        target_id=str(external_library.id),
        details=details,
        ip_address=client_ip(request),
    )
    await db.commit()
    return external_library


async def _provider_capabilities_summary(
    provider_type: str,
) -> dict:
    """Try to obtain a capability summary for a provider without user secrets."""
    try:
        adapter_cls = get_external_adapter(provider_type)
        adapter = adapter_cls()
        capabilities = await adapter.validate_config({"items": {}})
        return dataclasses.asdict(capabilities)
    except Exception:
        return {}


@router.get(
    "/providers",
    response_model=List[ExternalProviderResponse],
    dependencies=[Depends(rate_limit_account)],
)
async def list_providers(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """List external-library provider types available to the requester."""
    config: SonghiveConfig = request.app.state.config
    allowed = config.external_libraries.allowed_user_providers
    denied = config.external_libraries.denied_user_providers

    providers: List[ExternalProviderResponse] = []
    for provider_type in list_external_provider_types():
        if not is_user_configurable(provider_type):
            continue
        if not current_user.is_admin:
            if allowed and provider_type not in allowed:
                continue
            if provider_type in denied:
                continue

        summary = await _provider_capabilities_summary(provider_type)
        providers.append(
            ExternalProviderResponse(
                provider_type=provider_type,
                user_configurable=True,
                capabilities_summary=summary,
            )
        )

    return providers


@router.get("/", response_model=List[ExternalLibraryResponse])
async def list_external_libraries(
    response: Response,
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List user-scoped external libraries visible to the requester."""
    stmt = (
        select(ExternalLibrary)
        .where(ExternalLibrary.scope == "user")
        .options(selectinload(ExternalLibrary.library))
        .order_by(ExternalLibrary.created_at.desc())
    )
    if not current_user.is_admin:
        stmt = stmt.where(ExternalLibrary.created_by_id == current_user.id)

    total_stmt = select(func.count(ExternalLibrary.id)).where(ExternalLibrary.scope == "user")
    if not current_user.is_admin:
        total_stmt = total_stmt.where(ExternalLibrary.created_by_id == current_user.id)

    result = await db.execute(stmt.offset(pagination.offset).limit(pagination.limit))
    total = (await db.execute(total_stmt)).scalar() or 0
    pagination.set_total(response, total)

    return [await _build_external_library_response(lib, current_user, db) for lib in result.scalars().all()]


@router.post(
    "/",
    response_model=ExternalLibraryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def create_external_library(
    body: ExternalLibraryCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user-scoped external library."""
    config: SonghiveConfig = request.app.state.config

    if not current_user.is_admin and not config.external_libraries.allow_user_created_libraries:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User-created external libraries are disabled",
        )

    if not _provider_allowed_for_create(body.provider_type, current_user, config):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provider type not allowed",
        )

    external_library = await _create_external_library(
        db,
        request,
        current_user,
        body,
        scope="user",
        allow_include_in_index=False,
        audit_action="external_library.create",
    )
    return await _build_external_library_response(external_library, current_user, db)


@router.get("/{external_library_id}", response_model=ExternalLibraryResponse)
async def get_external_library(
    external_library_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single user-scoped external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None or external_library.scope != "user":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if external_library.created_by_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    return await _build_external_library_response(external_library, current_user, db)


@router.patch(
    "/{external_library_id}",
    response_model=ExternalLibraryResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def update_external_library(
    external_library_id: str,
    body: ExternalLibraryUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a user-scoped external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None or external_library.scope != "user":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if external_library.created_by_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if "include_in_library_index" in body.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="include_in_library_index is not allowed",
        )

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
    if "sync_interval_seconds" in body.model_fields_set:
        external_library.sync_interval_seconds = body.sync_interval_seconds
        changes["sync_interval_seconds"] = body.sync_interval_seconds

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
        actor_id=current_user.id,
        action="external_library.update",
        target_type="external_library",
        target_id=external_library_id,
        details=changes,
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(external_library)

    return await _build_external_library_response(external_library, current_user, db)


@router.delete(
    "/{external_library_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_external_library(
    external_library_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Delete a user-scoped external library and its linked tracks, albums, and artists."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None or external_library.scope != "user":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if external_library.created_by_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="external_library.delete",
        target_type="external_library",
        target_id=external_library_id,
        details={"provider_type": external_library.provider_type},
        ip_address=client_ip(request),
    )

    try:
        result = await deletion.delete_external_library(
            db,
            storage,
            external_library,
            user=current_user,
            is_admin=current_user.is_admin,
        )
    except deletion.DeletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.args[0]) from exc

    await db.commit()

    for info in result.unpublish:
        if info.owner is not None and info.artist is not None:
            background_tasks.add_task(
                unpublish_track_activity,
                info.track,
                info.artist,
                info.owner,
                request.app.state.config,
                info.federation_object_id,
            )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{external_library_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_account)],
)
async def sync_external_library_route(
    external_library_id: str,
    request: Request,
    body: ExternalSyncRequest = Body(default_factory=ExternalSyncRequest),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue a manual sync for an external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None or external_library.scope != "user":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if external_library.created_by_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    run = ExternalSyncRun(
        external_library_id=external_library_id,
        triggered_by="manual",
        triggered_by_user_id=current_user.id,
        status="queued",
    )
    db.add(run)
    await db.flush()
    run_id = str(run.id)

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="external_library.sync",
        target_type="external_library",
        target_id=external_library_id,
        details={"include_tombstones": body.include_tombstones},
        ip_address=client_ip(request),
    )

    await db.commit()

    try:
        sync_external_library_task.delay(
            external_library_id,
            "manual",
            str(current_user.id),
            body.include_tombstones,
            run_id,
        )
    except Exception as exc:
        logger.exception("Failed to enqueue external-library sync: %s", external_library_id)
        existing_run = await db.get(ExternalSyncRun, run_id)
        if existing_run is not None:
            existing_run.status = "failed"
            existing_run.completed_at = _utcnow()
            existing_run.error = _sanitize_error(exc)
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not enqueue sync task: {_sanitize_error(exc)}",
        ) from exc

    return {"sync_run_id": run_id}


@router.get("/{external_library_id}/sync-runs", response_model=List[ExternalSyncRunResponse])
async def list_external_sync_runs(
    external_library_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List sync runs for an external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None or external_library.scope != "user":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if external_library.created_by_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

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


@router.get("/{external_library_id}/tracks", response_model=List[ExternalTrackResponse])
async def list_external_tracks(
    external_library_id: str,
    response: Response,
    state: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    pagination: Pagination = Depends(get_pagination),
    db: AsyncSession = Depends(get_db),
):
    """List external tracks for an external library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None or external_library.scope != "user":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if external_library.created_by_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

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


async def _load_external_track(
    db: AsyncSession,
    external_library_id: str,
    external_track_id: str,
) -> tuple[ExternalLibrary, ExternalTrack]:
    """Load and verify an external track belongs to the given library."""
    external_library = await _load_external_library(db, external_library_id)
    if external_library is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    external_track = await db.get(ExternalTrack, external_track_id)
    if external_track is None or str(external_track.external_library_id) != external_library_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Track not found")

    return external_library, external_track


async def _check_track_manage_permission(
    db: AsyncSession,
    user: User,
    external_library: ExternalLibrary,
) -> None:
    """Require manage permission on the underlying library."""
    if not await acl.can_manage(db, user, "library", external_library.library_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


async def _provider_item_exists(
    adapter: ExternalLibraryAdapter,
    decrypted_config: dict,
    item: ExternalItemRef,
) -> bool:
    """Confirm a provider item still exists."""
    try:
        await adapter.read_metadata(decrypted_config, item)
        return True
    except ExternalItemNotFound:
        return False
    except UnsupportedExternalOperation:
        async for ref in adapter.iter_items(decrypted_config):
            if ref.provider_key == item.provider_key:
                return True
        return False
    except Exception:
        return False


async def _mark_track_tombstoned(
    db: AsyncSession,
    external_library: ExternalLibrary,
    external_track: ExternalTrack,
) -> None:
    """Tombstone an external track and remove its LibraryTrack association."""
    external_track.state = "tombstoned"
    external_track.last_seen_at = _utcnow()
    if external_track.track_id:
        await _remove_library_track(
            db,
            external_library.library_id,
            external_track.track_id,
        )


async def _ensure_can_delete_source(
    external_library: ExternalLibrary,
    config: SonghiveConfig,
) -> tuple[ExternalLibraryAdapter, dict]:
    """Validate that an external library may delete provider-side source files.

    Returns the initialized adapter and decrypted config.  Raises
    ``HTTPException`` for any precondition failure.
    """
    if not config.external_libraries.allow_destructive_delete:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Destructive source deletion is disabled",
        )

    capabilities = external_library.capabilities or {}
    if not capabilities.get("write_tags"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="External library is not writeable",
        )
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
    return adapter, decrypted


async def _delete_source_item(
    adapter: ExternalLibraryAdapter,
    decrypted_config: dict,
    external_track: ExternalTrack,
) -> Optional[ExternalMutationResult]:
    """Delete the provider-side source for a single external track.

    Returns the adapter mutation result, or ``None`` when the provider item is
    already gone.  Lets adapter exceptions propagate.
    """
    if external_track.state == "missing":
        return None

    item = _item_ref_from_track(external_track)
    try:
        return await adapter.delete_source(decrypted_config, item)
    except ExternalItemNotFound:
        return None


async def _remove_or_tombstone_external_track(
    db: AsyncSession,
    storage: StorageService,
    external_track: ExternalTrack,
    remove_songhive_track: bool,
) -> None:
    """Remove the Songhive track and external track, or mark it missing."""
    if remove_songhive_track and external_track.track_id:
        try:
            await deletion.delete_track(db, storage, external_track.track_id, delete_audio_file=False)
        except deletion.DeletionError as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
        await db.delete(external_track)
    else:
        external_track.state = "missing"
        external_track.last_seen_at = _utcnow()


async def _delete_track_source(
    db: AsyncSession,
    storage: StorageService,
    config: SonghiveConfig,
    external_library: ExternalLibrary,
    external_track: ExternalTrack,
    body: ExternalTrackDeleteRequest,
) -> Optional[ExternalMutationResult]:
    """Delete the provider-side source for an external track.

    Returns the adapter mutation result, or ``None`` when the provider item is
    already gone.  Raises ``HTTPException`` for precondition failures and lets
    adapter or deletion errors propagate so the caller can audit them.
    """
    if not body.confirm or body.confirm != "DELETE":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Confirm must be 'DELETE'",
        )

    adapter, decrypted = await _ensure_can_delete_source(external_library, config)
    mutation = await _delete_source_item(adapter, decrypted, external_track)
    await _remove_or_tombstone_external_track(db, storage, external_track, body.remove_songhive_track)
    return mutation


@router.post(
    "/{external_library_id}/tracks/{external_track_id}/restore",
    response_model=ExternalTrackResponse,
    dependencies=[Depends(rate_limit_account)],
)
async def restore_external_track(
    external_library_id: str,
    external_track_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore a tombstoned external track if the provider item still exists."""
    external_library, external_track = await _load_external_track(db, external_library_id, external_track_id)
    if external_library.scope != "user":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await _check_track_manage_permission(db, current_user, external_library)

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
    external_track.last_seen_at = _utcnow()

    if external_track.track_id:
        await _find_or_create_library_track(
            db,
            external_library.library_id,
            external_track.track_id,
            current_user.id,
        )

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="external_track.restore",
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


@router.delete(
    "/{external_library_id}/tracks/{external_track_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_account)],
)
async def delete_external_track(
    external_library_id: str,
    external_track_id: str,
    request: Request,
    body: ExternalTrackDeleteRequest = Body(default_factory=ExternalTrackDeleteRequest),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Tombstone or destructively delete an external track."""
    external_library, external_track = await _load_external_track(db, external_library_id, external_track_id)
    if external_library.scope != "user":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await _check_track_manage_permission(db, current_user, external_library)

    if not body.delete_source:
        await _mark_track_tombstoned(db, external_library, external_track)
        details = _redact_audit_details(
            {"external_library_id": external_library_id, "delete_source": False},
            external_track.provider_key,
        )
        await audit.log_action(
            db,
            actor_id=current_user.id,
            action="external_track.tombstone",
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
            actor_id=current_user.id,
            action="external_track.delete_source",
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
        actor_id=current_user.id,
        action="external_track.delete_source",
        target_type="external_track",
        target_id=external_track_id,
        details=details,
        ip_address=client_ip(request),
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
