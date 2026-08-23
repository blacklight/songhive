"""
API token routes: create, list, and revoke long-lived user API tokens.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.user import User
from ...services import audit
from ...users.api_tokens import (
    ApiTokenError,
    count_user_api_tokens,
    issue_api_token,
    list_user_api_tokens,
    revoke_api_token,
)
from .._common import client_ip
from ..deps import get_config, get_current_user, get_db
from ..middleware.rate_limit import rate_limit

router = APIRouter(prefix="/auth/api-tokens", tags=["API Tokens"])


class ApiTokenCreateRequest(BaseModel):
    """Request body for creating a new API token."""

    name: str = Field(..., min_length=1, max_length=128)
    expires_at: Optional[datetime] = None

    @field_validator("expires_at")
    @classmethod
    def _reject_past_expires_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return value
        if value.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if value <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return value


class ApiTokenCreateResponse(BaseModel):
    """Response returned after a successful API token creation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    token: str
    expires_at: Optional[datetime]
    created_at: datetime


class ApiTokenSummary(BaseModel):
    """Metadata for an API token; never includes the raw JWT."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    expires_at: Optional[datetime]
    last_used_at: Optional[datetime]
    created_at: datetime
    is_active: bool


class ApiTokenListResponse(BaseModel):
    """Paginated list of API tokens."""

    items: list[ApiTokenSummary]
    total: int


class RevokeApiTokenResponse(BaseModel):
    """Response returned after revoking an API token."""

    success: bool = True


@router.post(
    "",
    response_model=ApiTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
)
async def create_api_token(
    request: Request,
    body: ApiTokenCreateRequest,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    user: User = Depends(get_current_user),
):
    """Create a new named API token for the authenticated user."""
    try:
        api_token, raw_jwt = await issue_api_token(
            db,
            user,
            config,
            body.name,
            body.expires_at,
        )
    except ApiTokenError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit.log_action(
        db,
        actor_id=user.id,
        action="api_token.create",
        target_type="api_token",
        target_id=api_token.id,
        details={
            "name": api_token.name,
            "expires_at": api_token.expires_at.isoformat() if api_token.expires_at else None,
        },
        ip_address=client_ip(request),
    )

    return ApiTokenCreateResponse(
        id=api_token.id,
        name=api_token.name,
        token=raw_jwt,
        expires_at=api_token.expires_at,
        created_at=api_token.created_at,
    )


@router.get(
    "",
    response_model=ApiTokenListResponse,
)
async def list_api_tokens(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List the authenticated user's API token metadata."""
    tokens = await list_user_api_tokens(db, user.id, limit=limit, offset=offset)
    total = await count_user_api_tokens(db, user.id)
    return ApiTokenListResponse(
        items=[ApiTokenSummary.model_validate(t) for t in tokens],
        total=total,
    )


@router.delete(
    "/{token_id}",
    response_model=RevokeApiTokenResponse,
)
async def delete_api_token(
    request: Request,
    token_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Revoke an API token belonging to the authenticated user."""
    ok = await revoke_api_token(db, token_id, user.id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")

    await audit.log_action(
        db,
        actor_id=user.id,
        action="api_token.revoke",
        target_type="api_token",
        target_id=token_id,
        details={},
        ip_address=client_ip(request),
    )

    return RevokeApiTokenResponse()
