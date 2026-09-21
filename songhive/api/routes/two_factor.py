"""
Two-factor authentication routes: TOTP enrollment, WebAuthn security keys,
recovery codes, and the second-factor leg of the login flow.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.user import User
from ...services.auth import get_user_by_id, verify_password
from ...users import two_factor
from ...users.tokens import issue_token_pair
from .._common import client_ip
from ..cookies import set_auth_cookies
from ..deps import get_config, get_current_user, get_db, get_redis
from ..middleware.rate_limit import check_rate_limit, rate_limit
from .auth import TokenPairResponse, _token_pair_response

router = APIRouter(prefix="/auth/2fa")


class WebAuthnCredentialSummary(BaseModel):
    """Public view of a registered security key."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    credential_id: str
    name: Optional[str]
    transports: Optional[str]
    created_at: datetime


class TwoFactorStatusResponse(BaseModel):
    """Overview of the current user's second-factor enrollment."""

    enabled: bool
    totp_enabled: bool
    webauthn_credentials: List[WebAuthnCredentialSummary]
    recovery_codes_remaining: int


class TotpSetupResponse(BaseModel):
    """Pending TOTP enrollment details (secret is not yet active)."""

    secret: str
    otpauth_url: str
    qr_code: str


class TotpConfirmRequest(BaseModel):
    """Request body for confirming a pending TOTP enrollment."""

    code: str = Field(..., min_length=1)


class RecoveryCodesResponse(BaseModel):
    """A freshly generated batch of recovery codes (shown once)."""

    recovery_codes: List[str]


class PasswordConfirmRequest(BaseModel):
    """Request body for sensitive actions that re-verify the password."""

    password: str = Field(..., min_length=1)


class SuccessResponse(BaseModel):
    """Generic success response."""

    success: bool = True


class WebAuthnRegisterCompleteRequest(BaseModel):
    """Browser-produced attestation plus an optional display name."""

    name: Optional[str] = Field(None, max_length=128)
    credential: Dict[str, Any]


class WebAuthnRegisterCompleteResponse(BaseModel):
    """The stored credential and, for first-time enrollment, recovery codes."""

    credential: WebAuthnCredentialSummary
    recovery_codes: Optional[List[str]] = None


class MfaLoginRequest(BaseModel):
    """Request body for completing a password-verified login with a code."""

    mfa_token: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)


class WebAuthnLoginBeginRequest(BaseModel):
    """Request body for starting a security-key assertion at login."""

    mfa_token: str = Field(..., min_length=1)


class WebAuthnLoginCompleteRequest(BaseModel):
    """Request body for finishing a security-key assertion at login."""

    mfa_token: str = Field(..., min_length=1)
    credential: Dict[str, Any]


def _request_host(request: Request) -> Optional[str]:
    """Return the request's host for WebAuthn relying-party resolution."""
    return request.base_url.hostname


async def _complete_login(
    request: Request,
    response: Response,
    user: User,
    config: SonghiveConfig,
    redis: Redis,
) -> TokenPairResponse:
    """Issue the token pair for a fully authenticated user."""
    user.last_login = datetime.now(timezone.utc)
    token_pair = await issue_token_pair(
        user,
        config,
        redis,
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    set_auth_cookies(response, config, token_pair)
    return _token_pair_response(token_pair)


async def _resolve_pending_login(
    request: Request,
    mfa_token: str,
    db: AsyncSession,
    config: SonghiveConfig,
    redis: Redis,
) -> User:
    """Load the pending login and its user, or raise a 401."""
    await check_rate_limit(request, config, redis, identifier=mfa_token)
    pending = await two_factor.get_pending_login(mfa_token, redis)
    if pending is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired two-factor token",
        )
    user = await get_user_by_id(db, pending.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired two-factor token",
        )
    return user


async def _drop_failed_pending(mfa_token: str, redis: Redis) -> None:
    """Register a failed attempt and raise when the token died with it."""
    if not await two_factor.fail_pending_login(mfa_token, redis):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Too many failed attempts; please log in again",
        )


@router.get("", response_model=TwoFactorStatusResponse)
async def two_factor_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's two-factor enrollment status."""
    credentials = await two_factor.list_webauthn_credentials(db, current_user)
    return TwoFactorStatusResponse(
        enabled=current_user.totp_secret is not None or bool(credentials),
        totp_enabled=current_user.totp_secret is not None,
        webauthn_credentials=[WebAuthnCredentialSummary.model_validate(cred) for cred in credentials],
        recovery_codes_remaining=await two_factor.remaining_recovery_codes(db, current_user),
    )


@router.post(
    "/totp/setup",
    response_model=TotpSetupResponse,
    dependencies=[Depends(rate_limit)],
)
async def totp_setup(
    request: Request,
    current_user: User = Depends(get_current_user),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Start TOTP enrollment: returns the secret and a QR code for it.

    The secret is not persisted yet — :func:`totp_confirm` must verify a
    current code before it becomes active.
    """
    if current_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="TOTP is already enabled; disable it first",
        )
    await check_rate_limit(request, config, redis, identifier=current_user.id)
    secret, uri = await two_factor.begin_totp_setup(current_user, config, redis)
    return TotpSetupResponse(
        secret=secret,
        otpauth_url=uri,
        qr_code=two_factor.totp_qr_code_data_uri(uri),
    )


@router.post(
    "/totp/confirm",
    response_model=RecoveryCodesResponse,
    dependencies=[Depends(rate_limit)],
)
async def totp_confirm(
    body: TotpConfirmRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Confirm a pending TOTP enrollment with a current code.

    On success the secret is saved and a batch of recovery codes is returned
    exactly once.
    """
    await check_rate_limit(request, config, redis, identifier=current_user.id)
    try:
        codes = await two_factor.confirm_totp_setup(db, current_user, body.code, redis)
    except two_factor.TwoFactorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post(
    "/totp/disable",
    response_model=SuccessResponse,
    dependencies=[Depends(rate_limit)],
)
async def totp_disable(
    body: PasswordConfirmRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Disable TOTP after re-verifying the account password."""
    await check_rate_limit(request, config, redis, identifier=current_user.id)
    try:
        await two_factor.disable_totp(db, current_user, body.password)
    except two_factor.TwoFactorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return SuccessResponse()


@router.post(
    "/webauthn/register/begin",
    dependencies=[Depends(rate_limit)],
)
async def webauthn_register_begin(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Start a security-key registration ceremony (WebAuthn create options)."""
    await check_rate_limit(request, config, redis, identifier=current_user.id)
    try:
        return await two_factor.webauthn_register_begin(
            db, current_user, config, redis, request_host=_request_host(request)
        )
    except two_factor.TwoFactorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post(
    "/webauthn/register/complete",
    response_model=WebAuthnRegisterCompleteResponse,
    dependencies=[Depends(rate_limit)],
)
async def webauthn_register_complete(
    body: WebAuthnRegisterCompleteRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Finish a security-key registration with the browser's attestation."""
    await check_rate_limit(request, config, redis, identifier=current_user.id)
    try:
        credential = await two_factor.webauthn_register_complete(
            db,
            current_user,
            body.credential,
            body.name,
            config,
            redis,
            request_host=_request_host(request),
        )
    except two_factor.TwoFactorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    # First-time enrollment also issues the initial batch of recovery codes.
    recovery_codes = None
    if not current_user.totp_secret and await two_factor.remaining_recovery_codes(db, current_user) == 0:
        recovery_codes = await two_factor.regenerate_recovery_codes(db, current_user)

    return WebAuthnRegisterCompleteResponse(
        credential=WebAuthnCredentialSummary.model_validate(credential),
        recovery_codes=recovery_codes,
    )


@router.delete(
    "/webauthn/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit)],
)
async def webauthn_delete(
    credential_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Remove a registered security key."""
    await check_rate_limit(request, config, redis, identifier=current_user.id)
    if not await two_factor.delete_webauthn_credential(db, current_user, credential_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Credential not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/recovery-codes",
    response_model=RecoveryCodesResponse,
    dependencies=[Depends(rate_limit)],
)
async def recovery_codes_regenerate(
    body: PasswordConfirmRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Regenerate the user's recovery codes after re-verifying the password."""
    await check_rate_limit(request, config, redis, identifier=current_user.id)
    if not await two_factor.user_has_2fa(db, current_user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Two-factor authentication is not enabled",
        )
    if not verify_password(body.password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid password")
    codes = await two_factor.regenerate_recovery_codes(db, current_user)
    return RecoveryCodesResponse(recovery_codes=codes)


@router.post("/login", response_model=TokenPairResponse)
async def mfa_login(
    body: MfaLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Complete a password-verified login with a TOTP or recovery code."""
    user = await _resolve_pending_login(request, body.mfa_token, db, config, redis)
    if not await two_factor.verify_login_code(db, user, body.code):
        await _drop_failed_pending(body.mfa_token, redis)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid two-factor code",
        )

    await two_factor.pop_pending_login(body.mfa_token, redis)
    return await _complete_login(request, response, user, config, redis)


@router.post("/login/webauthn/begin")
async def mfa_login_webauthn_begin(
    body: WebAuthnLoginBeginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Start a security-key assertion for a pending two-factor login."""
    user = await _resolve_pending_login(request, body.mfa_token, db, config, redis)
    try:
        return await two_factor.webauthn_auth_begin(
            db, user, body.mfa_token, config, redis, request_host=_request_host(request)
        )
    except two_factor.TwoFactorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/login/webauthn/complete", response_model=TokenPairResponse)
async def mfa_login_webauthn_complete(
    body: WebAuthnLoginCompleteRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """Finish a security-key assertion for a pending two-factor login."""
    user = await _resolve_pending_login(request, body.mfa_token, db, config, redis)
    try:
        ok = await two_factor.webauthn_auth_complete(
            db,
            user,
            body.mfa_token,
            body.credential,
            config,
            redis,
            request_host=_request_host(request),
        )
    except two_factor.TwoFactorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    if not ok:
        await _drop_failed_pending(body.mfa_token, redis)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Security-key verification failed",
        )

    await two_factor.pop_pending_login(body.mfa_token, redis)
    return await _complete_login(request, response, user, config, redis)
