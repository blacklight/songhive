"""
API token routes: create, list, and revoke long-lived user API tokens.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from redis.asyncio import Redis
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
from ..deps import get_config, get_current_user, get_db, get_redis
from ..middleware.rate_limit import rate_limit

router = APIRouter(prefix="/auth/api-tokens", tags=["API Tokens"])


class ApiTokenCreateRequest(BaseModel):
    """Request body for creating a new API token."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "CI/CD Pipeline",
                    "expires_at": "2027-12-31T23:59:59Z",
                },
                {
                    "name": "Mobile App",
                    "expires_at": None,
                },
            ]
        }
    )

    name: str = Field(..., min_length=1, max_length=128, description="A descriptive name for the token")
    expires_at: Optional[datetime] = Field(
        None,
        description="Optional expiration timestamp (ISO 8601 format with timezone). If null, the token never expires.",
    )

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

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "name": "CI/CD Pipeline",
                    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.example",
                    "expires_at": "2027-12-31T23:59:59Z",
                    "created_at": "2026-08-23T12:00:00Z",
                }
            ]
        },
    )

    id: str = Field(..., description="Unique identifier for the token")
    name: str = Field(..., description="The descriptive name of the token")
    token: str = Field(..., description="The raw JWT. Store this securely; it cannot be retrieved again.")
    expires_at: Optional[datetime] = Field(None, description="Expiration timestamp, or null if the token never expires")
    created_at: datetime = Field(..., description="Timestamp when the token was created")


class ApiTokenSummary(BaseModel):
    """Metadata for an API token; never includes the raw JWT."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "name": "CI/CD Pipeline",
                    "expires_at": "2027-12-31T23:59:59Z",
                    "last_used_at": "2026-08-23T14:30:00Z",
                    "created_at": "2026-08-23T12:00:00Z",
                    "is_active": True,
                }
            ]
        },
    )

    id: str = Field(..., description="Unique identifier for the token")
    name: str = Field(..., description="The descriptive name of the token")
    expires_at: Optional[datetime] = Field(None, description="Expiration timestamp, or null if the token never expires")
    last_used_at: Optional[datetime] = Field(
        None, description="Timestamp when the token was last used for authentication"
    )
    created_at: datetime = Field(..., description="Timestamp when the token was created")
    is_active: bool = Field(..., description="True if the token is not revoked and has not expired")


class ApiTokenListResponse(BaseModel):
    """Paginated list of API tokens."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "id": "550e8400-e29b-41d4-a716-446655440000",
                            "name": "CI/CD Pipeline",
                            "expires_at": "2027-12-31T23:59:59Z",
                            "last_used_at": "2026-08-23T14:30:00Z",
                            "created_at": "2026-08-23T12:00:00Z",
                            "is_active": True,
                        },
                        {
                            "id": "660e8400-e29b-41d4-a716-446655440001",
                            "name": "Mobile App",
                            "expires_at": None,
                            "last_used_at": "2026-08-22T10:15:00Z",
                            "created_at": "2026-08-20T08:00:00Z",
                            "is_active": True,
                        },
                    ],
                    "total": 2,
                }
            ]
        }
    )

    items: list[ApiTokenSummary] = Field(..., description="List of API token metadata")
    total: int = Field(..., description="Total number of tokens for the user")


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
    redis: Redis = Depends(get_redis),
    user: User = Depends(get_current_user),
):
    """Revoke an API token belonging to the authenticated user."""
    ok = await revoke_api_token(db, token_id, user.id, redis=redis)
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
