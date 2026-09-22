"""Server-side audio output routes."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.output_stream import OutputStream
from ...models.user import User
from ...services.outputs import (
    _provider_allowed_for_create,
    create_output,
    delete_output,
    get_output_stream,
    get_provider_fields,
    list_outputs,
    redacted_config,
    update_output,
    validate_output,
)
from ..deps import get_config, get_current_user, get_db
from ..middleware.rate_limit import rate_limit_account

router = APIRouter(prefix="/outputs")


class OutputCreate(BaseModel):
    """Request body for creating an output."""

    provider_type: str
    name: str
    config: dict
    enabled: bool = True


class OutputUpdate(BaseModel):
    """Partial update body for an output."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None


class OutputCapabilitiesResponse(BaseModel):
    """Capability summary for an output."""

    model_config = ConfigDict(from_attributes=True)

    metadata_updates: bool = False
    pause_supported: bool = False
    seek_supported: bool = False
    multi_listener: bool = False
    user_configurable: bool = False


class OutputResponse(BaseModel):
    """Output with redacted config."""

    id: str
    user_id: str
    provider_type: str
    name: str
    config: dict
    capabilities: Optional[OutputCapabilitiesResponse] = None
    enabled: bool
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ProviderResponse(BaseModel):
    """Available output provider."""

    provider_type: str
    user_configurable: bool
    can_create: bool
    fields: list[dict]


class ValidationResponse(BaseModel):
    """Result of an output validation test."""

    ok: bool
    capabilities: Optional[OutputCapabilitiesResponse] = None
    error: Optional[str] = None


def _to_capabilities_response(caps: Optional[dict]) -> Optional[OutputCapabilitiesResponse]:
    if not caps:
        return None
    try:
        return OutputCapabilitiesResponse(**caps)
    except Exception:
        return None


def _to_response(output: OutputStream) -> OutputResponse:
    return OutputResponse(
        id=str(output.id),
        user_id=output.user_id,
        provider_type=output.provider_type,
        name=output.name,
        config=redacted_config(output.config, output.provider_type),
        capabilities=_to_capabilities_response(output.capabilities),
        enabled=output.enabled,
        last_error=output.last_error,
        created_at=output.created_at,
        updated_at=output.updated_at,
    )


@router.get(
    "/providers",
    response_model=list[ProviderResponse],
    dependencies=[Depends(rate_limit_account)],
)
async def list_providers(
    current_user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """List output provider types available to the requester."""
    from ...streams.registry import list_output_types

    providers = []
    for provider_type in list_output_types():
        if not _provider_allowed_for_create(provider_type, current_user, config):
            # Still show non-user-configurable providers to admin? Skip.
            continue
        provider_cls = get_provider(provider_type)
        providers.append(
            ProviderResponse(
                provider_type=provider_type,
                user_configurable=provider_cls.user_configurable,
                can_create=_provider_allowed_for_create(provider_type, current_user, config),
                fields=get_provider_fields(provider_type),
            )
        )
    return providers


def get_provider(provider_type: str):
    """Return the registered provider class."""
    from ...streams.registry import get_output

    return get_output(provider_type)


@router.get("/", response_model=list[OutputResponse])
async def list_user_outputs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's outputs."""
    outputs = await list_outputs(db, current_user)
    return [_to_response(out) for out in outputs]


@router.post(
    "/",
    response_model=OutputResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user_output(
    body: OutputCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Create a new server-side output."""
    output = await create_output(
        db,
        current_user,
        config,
        provider_type=body.provider_type,
        name=body.name,
        cfg=body.config,
        enabled=body.enabled,
    )
    await db.commit()
    return _to_response(output)


@router.get("/{output_id}", response_model=OutputResponse)
async def get_user_output(
    output_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single output."""
    output = await get_output_stream(db, output_id, current_user)
    return _to_response(output)


@router.patch("/{output_id}", response_model=OutputResponse)
async def patch_user_output(
    output_id: str,
    body: OutputUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Update an output, preserving redacted sentinels."""
    output = await get_output_stream(db, output_id, current_user)
    output = await update_output(
        db,
        output,
        current_user,
        config,
        name=body.name,
        cfg=body.config,
        enabled=body.enabled,
    )
    await db.commit()
    return _to_response(output)


@router.delete("/{output_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_output(
    output_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an output."""
    output = await get_output_stream(db, output_id, current_user)
    await delete_output(db, output, current_user)
    await db.commit()
    return None


@router.post("/{output_id}/validate", response_model=ValidationResponse)
async def validate_user_output(
    output_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Validate an output and refresh stored capabilities."""
    output = await get_output_stream(db, output_id, current_user)
    try:
        output = await validate_output(db, output, current_user)
        await db.commit()
    except HTTPException as exc:
        await db.rollback()
        return ValidationResponse(ok=False, error=exc.detail)
    return ValidationResponse(
        ok=True,
        capabilities=_to_capabilities_response(output.capabilities),
        error=output.last_error,
    )
