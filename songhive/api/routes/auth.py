"""
Authentication routes: registration, login, token refresh and logout.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...services.auth import (
    get_user_by_id,
    get_user_by_username_or_email,
    verify_password,
)
from ...tasks.email import send_password_reset_email, send_verification_email
from ...users.manager import (
    RegistrationError,
    confirm_password_reset,
    register_user,
    request_password_reset,
    verify_email,
)
from ...users.tokens import (
    TokenPair,
    issue_token_pair,
    revoke_refresh_token,
    rotate_refresh_token,
    validate_refresh_token,
)
from ..deps import get_config, get_db, get_redis
from ..middleware.rate_limit import check_rate_limit, rate_limit

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


class VerifyEmailRequest(BaseModel):
    """Request body for confirming an email verification token."""

    token: str


class VerifyEmailResponse(BaseModel):
    """Response returned after a successful email verification."""

    success: bool = True


class PasswordResetInitRequest(BaseModel):
    """Request body for requesting a password reset."""

    username: str


class PasswordResetInitResponse(BaseModel):
    """Generic response returned after a password reset request."""

    success: bool = True


class PasswordResetConfirmRequest(BaseModel):
    """Request body for confirming a password reset."""

    token: str
    new_password: str = Field(..., min_length=1)


class PasswordResetConfirmResponse(BaseModel):
    """Response returned after a successful password reset."""

    success: bool = True


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
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

    if config.auth.require_email_verification and user.email_verification_token_raw:
        send_verification_email.delay(user.email, user.username, user.email_verification_token_raw)

    return RegisterResponse.model_validate(user)


@router.post("/login", response_model=TokenPairResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Authenticate with a username/email and password."""
    await check_rate_limit(request, config, redis, identifier=body.username)
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


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    dependencies=[Depends(rate_limit)],
)
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
        await revoke_refresh_token(body.refresh_token, redis)
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


@router.post(
    "/logout",
    response_model=LogoutResponse,
    dependencies=[Depends(rate_limit)],
)
async def logout(
    body: LogoutRequest,
    redis: Redis = Depends(get_redis),
):
    """Revoke a refresh token."""
    await revoke_refresh_token(body.refresh_token, redis)
    return LogoutResponse()


@router.post(
    "/verify-email",
    response_model=VerifyEmailResponse,
    dependencies=[Depends(rate_limit)],
    summary="Verify an email address",
    description=(
        "Confirm an email address using a verification token sent to the user's "
        "inbox. The token is single-use and is cleared after a successful "
        "verification."
    ),
)
async def verify_email_endpoint(
    body: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    """Confirm an email address using a verification token sent by email."""
    user = await verify_email(db, body.token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )
    return VerifyEmailResponse()


@router.post(
    "/password-reset/request",
    response_model=PasswordResetInitResponse,
    summary="Request a password reset",
    description=(
        "Start the password reset flow for an account. A reset token is sent to "
        "the user's email address if the account exists. The endpoint always "
        "returns a generic success response to avoid revealing whether an account "
        "exists for a given username or email."
    ),
)
async def password_reset_request(
    body: PasswordResetInitRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Request a password reset for an account.

    Always returns a generic success response to avoid revealing whether an
    account exists for a given username or email.
    """
    await check_rate_limit(request, config, redis, identifier=body.username)
    user, token = await request_password_reset(db, config, body.username)
    if user is not None and token is not None:
        send_password_reset_email.delay(user.email, user.username, token)
    return PasswordResetInitResponse()


@router.post(
    "/password-reset/confirm",
    response_model=PasswordResetConfirmResponse,
    dependencies=[Depends(rate_limit)],
    summary="Confirm a password reset",
    description=(
        "Set a new password using a password-reset token received by email. On "
        "success, all active refresh tokens for the user are revoked."
    ),
)
async def password_reset_confirm(
    body: PasswordResetConfirmRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """Set a new password using a password-reset token."""
    success = await confirm_password_reset(db, redis, body.token, body.new_password)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    return PasswordResetConfirmResponse()


def _token_pair_response(token_pair: TokenPair) -> TokenPairResponse:
    """Convert a service-level token pair into the API response model."""
    return TokenPairResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        token_type=token_pair.token_type,
        expires_in=token_pair.expires_in,
    )
