"""
Authentication routes: registration, login, token refresh, logout, and OAuth2.
"""

import base64
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.user import User
from ...services.auth import (
    get_user_by_id,
    get_user_by_username_or_email,
    verify_password,
)
from ...tasks.email import send_password_reset_email, send_verification_email
from ...users.manager import (
    RegistrationError,
    confirm_password_reset,
    generate_verification_email_token,
    register_user,
    request_password_reset,
    verify_email,
)
from ...users.oauth import (
    OAuth2ProviderError,
    create_authorization_code,
    create_token,
    introspect_token,
    revoke_token,
)
from ...users.tokens import (
    TokenPair,
    issue_token_pair,
    revoke_refresh_token,
    rotate_refresh_token,
    validate_refresh_token,
)
from .._common import client_ip
from ..deps import get_config, get_current_user, get_db, get_redis
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


class ResendVerificationRequest(BaseModel):
    """Request body for resending a verification email."""

    username_or_email: str = Field(..., min_length=1)


class ResendVerificationResponse(BaseModel):
    """Generic response returned after a verification resend request."""

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


class OAuth2TokenResponse(BaseModel):
    """OAuth2 token endpoint response."""

    access_token: str
    token_type: str
    expires_in: int
    refresh_token: Optional[str] = None
    scope: Optional[str] = None


class OAuth2IntrospectionResponse(BaseModel):
    """OAuth2 token introspection response."""

    active: bool
    client_id: Optional[str] = None
    username: Optional[str] = None
    token_type: Optional[str] = None
    scope: Optional[str] = None
    exp: Optional[int] = None
    sub: Optional[str] = None


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit)],
)
async def register(
    body: RegisterRequest,
    request: Request,
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
        verification_url = (
            f"{str(request.base_url).rstrip('/')}/verify-email?"
            f"{urlencode({'token': user.email_verification_token_raw})}"
        )
        send_verification_email.delay(  # type: ignore
            user.email,
            user.username,
            verification_url=verification_url,
        )

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
    token_pair = await issue_token_pair(
        user,
        config,
        redis,
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return _token_pair_response(token_pair)


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    dependencies=[Depends(rate_limit)],
)
async def refresh(
    body: RefreshRequest,
    request: Request,
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

    token_pair = await rotate_refresh_token(
        body.refresh_token,
        config,
        redis,
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
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
    "/verify-email/resend",
    response_model=ResendVerificationResponse,
    summary="Resend verification email",
    description=(
        "Request a new verification email for an account. A fresh token is sent "
        "to the user's email address if the account exists, is active, and has "
        "not yet been verified. The endpoint always returns a generic success "
        "response to avoid revealing whether an account exists or is verified."
    ),
)
async def resend_verification_email_endpoint(
    body: ResendVerificationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Resend a verification email for an unverified, active account."""
    await check_rate_limit(request, config, redis, identifier=body.username_or_email)
    user, token = await generate_verification_email_token(db, body.username_or_email)
    if user is not None and token is not None:
        verification_url = f"{str(request.base_url).rstrip('/')}/verify-email?{urlencode({'token': token})}"
        send_verification_email.delay(  # type: ignore
            user.email,
            user.username,
            verification_url=verification_url,
        )
    return ResendVerificationResponse()


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
        send_password_reset_email.delay(user.email, user.username, token)  # type: ignore
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


def _extract_client_credentials(
    request: Request,
    client_id: Optional[str],
    client_secret: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Extract client credentials from HTTP Basic auth or form fields."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:].encode()).decode()
            basic_client_id, _, basic_client_secret = decoded.partition(":")
            client_id = basic_client_id or client_id
            client_secret = basic_client_secret or client_secret
        except ValueError:
            pass
    return client_id, client_secret


def _build_redirect_url(redirect_uri: str, params: Dict[str, Optional[str]]) -> str:
    """Append query parameters to a redirect URI, preserving existing query."""
    parsed = urlparse(redirect_uri)
    query = dict(parse_qsl(parsed.query))
    for key, value in params.items():
        if value is not None:
            query[key] = value
    return urlunparse(parsed._replace(query=urlencode(query)))


def _handle_oauth_error(exc: OAuth2ProviderError) -> HTTPException:
    """Convert an OAuth2 provider error into an HTTPException."""
    headers = None
    if exc.error == "invalid_client":
        headers = {"WWW-Authenticate": "Basic"}
    return HTTPException(status_code=exc.status_code, detail=exc.error, headers=headers)


async def _oauth_authorize(
    db: AsyncSession,
    redis: Redis,
    current_user: User,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    *,
    code_challenge: Optional[str],
    code_challenge_method: str,
    scope: Optional[str],
    state: Optional[str],
) -> RedirectResponse:
    """Validate and create an OAuth2 authorization code for the current user."""
    try:
        code, echo_state = await create_authorization_code(
            db,
            redis,
            current_user,
            response_type,
            client_id,
            redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
            state=state,
        )
    except OAuth2ProviderError as exc:
        raise _handle_oauth_error(exc)

    location = _build_redirect_url(redirect_uri, {"code": code, "state": echo_state})
    return RedirectResponse(location, status_code=status.HTTP_302_FOUND)


@router.get("/oauth/authorize")
async def oauth_authorize_get(
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: Optional[str] = Query(None),
    code_challenge_method: str = Query("S256"),
    scope: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    """Issue an OAuth2 authorization code for an authenticated resource owner."""
    return await _oauth_authorize(
        db,
        redis,
        current_user,
        response_type,
        client_id,
        redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        state=state,
    )


@router.post("/oauth/authorize")
async def oauth_authorize_post(
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: Optional[str] = Query(None),
    code_challenge_method: str = Query("S256"),
    scope: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    """Issue an OAuth2 authorization code for an authenticated resource owner."""
    return await _oauth_authorize(
        db,
        redis,
        current_user,
        response_type,
        client_id,
        redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        state=state,
    )


@router.post("/oauth/token", response_model=OAuth2TokenResponse)
async def oauth_token(
    request: Request,
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
    scope: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
) -> OAuth2TokenResponse:
    """Exchange an authorization code or refresh token for an access token."""
    client_id, client_secret = _extract_client_credentials(request, client_id, client_secret)
    try:
        token_result = await create_token(
            db,
            redis,
            config,
            grant_type,
            client_id,
            client_secret,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            refresh_token=refresh_token,
            scope=scope,
        )
        return OAuth2TokenResponse.model_validate(token_result)
    except OAuth2ProviderError as exc:
        raise _handle_oauth_error(exc)


@router.post("/oauth/revoke")
async def oauth_revoke(
    request: Request,
    token: str = Form(...),
    token_type_hint: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Response:
    """Revoke an OAuth2 access or refresh token."""
    client_id, client_secret = _extract_client_credentials(request, client_id, client_secret)
    try:
        await revoke_token(
            db,
            redis,
            token,
            token_type_hint,
            client_id=client_id,
            client_secret=client_secret,
        )
    except OAuth2ProviderError as exc:
        raise _handle_oauth_error(exc)
    return Response(status_code=status.HTTP_200_OK)


@router.post("/oauth/introspect", response_model=OAuth2IntrospectionResponse)
async def oauth_introspect(
    request: Request,
    token: str = Form(...),
    token_type_hint: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> OAuth2IntrospectionResponse:
    """Return the active status and metadata for an OAuth2 token."""
    client_id, client_secret = _extract_client_credentials(request, client_id, client_secret)
    try:
        introspect_result = await introspect_token(
            db,
            redis,
            token,
            token_type_hint,
            client_id=client_id,
            client_secret=client_secret,
        )
        return OAuth2IntrospectionResponse.model_validate(introspect_result)
    except OAuth2ProviderError as exc:
        raise _handle_oauth_error(exc)


def _token_pair_response(token_pair: TokenPair) -> TokenPairResponse:
    """Convert a service-level token pair into the API response model."""
    return TokenPairResponse(
        access_token=token_pair.access_token,
        refresh_token=token_pair.refresh_token,
        token_type=token_pair.token_type,
        expires_in=token_pair.expires_in,
    )
