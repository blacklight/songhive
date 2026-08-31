"""
Session management routes: list and revoke active refresh-token sessions.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.user import User
from ...services import audit
from ...users.tokens import list_user_sessions, revoke_session
from .._common import client_ip
from ..deps import get_config, get_current_user, get_db, get_redis

router = APIRouter(prefix="/auth/sessions", tags=["Sessions"])


class SessionSummary(BaseModel):
    """Metadata for an active refresh-token session."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "a4b5c6...",
                    "ip_address": "192.0.2.1",
                    "user_agent": "Mozilla/5.0",
                    "created_at": "2026-08-31T12:00:00Z",
                    "expires_at": "2026-09-30T12:00:00Z",
                    "is_current": False,
                }
            ]
        },
    )

    id: str = Field(..., description="SHA-256 hash of the refresh token; used as the session id")
    ip_address: Optional[str] = Field(None, description="IP address from which the session was created")
    user_agent: Optional[str] = Field(None, description="User-Agent header from which the session was created")
    created_at: Optional[datetime] = Field(None, description="Timestamp when the session was created")
    expires_at: Optional[datetime] = Field(None, description="Timestamp when the session expires")
    is_current: bool = Field(False, description="True if this is the caller's current session")


class SessionListResponse(BaseModel):
    """Paginated list of active sessions."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {
                            "id": "a4b5c6...",
                            "ip_address": "192.0.2.1",
                            "user_agent": "Mozilla/5.0",
                            "created_at": "2026-08-31T12:00:00Z",
                            "expires_at": "2026-09-30T12:00:00Z",
                            "is_current": True,
                        }
                    ],
                    "total": 1,
                }
            ]
        },
    )

    items: list[SessionSummary] = Field(..., description="List of active sessions")
    total: int = Field(..., description="Total number of active sessions")


class RevokeSessionResponse(BaseModel):
    """Response returned after revoking a session."""

    success: bool = True


@router.get(
    "",
    response_model=SessionListResponse,
)
async def list_sessions(
    current_session_id: Optional[str] = Query(
        None,
        description="Optional session id of the caller, used to mark the current session.",
    ),
    redis: Redis = Depends(get_redis),
    user: User = Depends(get_current_user),
):
    """List the authenticated user's active refresh-token sessions."""
    sessions = await list_user_sessions(redis, user.id)

    items = []
    for session in sessions:
        items.append(
            SessionSummary(
                id=session.id,
                ip_address=session.ip_address,
                user_agent=session.user_agent,
                created_at=session.created_at,
                expires_at=session.expires_at,
                is_current=session.id == current_session_id,
            )
        )

    return SessionListResponse(items=items, total=len(items))


@router.delete(
    "/{session_id}",
    response_model=RevokeSessionResponse,
)
async def delete_session(
    request: Request,
    session_id: str,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
):
    """Revoke an active refresh-token session belonging to the authenticated user."""
    access_token_ttl = (config.auth.access_token_expiry_minutes or 0) * 60
    ok = await revoke_session(redis, session_id, user.id, access_token_ttl)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    await audit.log_action(
        db,
        actor_id=user.id,
        action="user.session.revoke",
        target_type="user_session",
        target_id=session_id,
        details={},
        ip_address=client_ip(request),
    )

    await db.commit()

    return RevokeSessionResponse()
