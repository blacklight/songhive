"""
Authentication routes: registration, login, token refresh and logout.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...services.auth import get_user_by_id, get_user_by_username_or_email, verify_password
from ...users.manager import RegistrationError, register_user
from ...users.tokens import (
    TokenPair,
    issue_token_pair,
    revoke_refresh_token,
    rotate_refresh_token,
    validate_refresh_token,
)
from ..deps import get_config, get_db, get_redis

router = APIRouter(prefix="/auth")


class RegisterRequest(BaseModel):
    """Request body for user registration."""

    username: str = Field(..., min_length=1, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=1)
    display_name: Optional[str] = Field(None, max_length=128)
    invite_code: Optional[str] = None


class RegisterResponse(BaseModel):
    """Response returned after a successful registration."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str
    display_name: Optional[str] = None
    is_active: bool
    email_verified: bool
    role: str


class LoginRequest(BaseModel):
    """Request body for username/email and password login."""

    username: str
    password: str


class TokenPairResponse(BaseModel):
    """An access and refresh token pair returned on login or refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    """Request body for refreshing an access token."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Request body for revoking a refresh token."""

    refresh_token: str


class LogoutResponse(BaseModel):
    """Response returned after a logout request."""

    success: bool = True


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Register a new user account."""
    try:
        user = await register_user(
            db,
            config,
            username=body.username,
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            invite_code=body.invite_code,
        )
    except RegistrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return RegisterResponse.model_validate(user)


@router.post("/login", response_model=TokenPairResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Authenticate with a username/email and password."""
    user = await get_user_by_username_or_email(db, body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if config.auth.require_email_verification and not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified",
        )

    user.last_login = datetime.now(timezone.utc)
    token_pair = await issue_token_pair(user, config, redis)
    return _token_pair_response(token_pair)


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Refresh an access token using a valid refresh token."""
    payload = await validate_refresh_token(body.refresh_token, redis)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_user_by_id(db, payload.user_id)
    if user is None or not user.is_active:
        await revoke_refresh_token(body.refresh_token, config, redis)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive or deleted",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_pair = await rotate_refresh_token(body.refresh_token, config, redis)
    if token_pair is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token_pair_response(token_pair)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    body: LogoutRequest,
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Revoke a refresh token."""
    await revoke_refresh_token(body.refresh_token, config, redis)
    return LogoutResponse()


def _token_pair_response(token_pair: TokenPair) -> TokenPairResponse:
    """Convert a service-level token pair into the API response model."""
    return TokenPairResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        token_type=token_pair.token_type,
        expires_in=token_pair.expires_in,
    )
