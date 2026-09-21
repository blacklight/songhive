"""
Two-factor authentication service.

Supported second factors:

- TOTP one-time passwords (RFC 6238) via ``pyotp``. The shared secret is only
  persisted after the user proves enrollment by entering a current code; the
  pending secret lives in Redis until then. At rest the secret is encrypted
  with the same Fernet helper used for external library configuration.
- WebAuthn/FIDO2 security keys (USB keys, platform authenticators) via
  ``fido2``. Registration and authentication ceremonies are staged in Redis.
- Single-use recovery codes, stored as SHA-256 hashes.

Password-verified logins for users with any factor enabled do not issue
tokens directly: they yield a short-lived ``mfa_token`` (a Redis-held pending
login) that must be exchanged at the ``/auth/2fa/login`` endpoints. API
tokens and OAuth2 grants are unaffected — they never reach this module.
"""

import base64
import hashlib
import io
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlparse

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.two_factor import RecoveryCode, WebAuthnCredential
from ..models.user import User
from ..services.auth import verify_password
from ..services.secrets import decrypt_secret, encrypt_secret
from ..users.tokens import _hash_token

PENDING_LOGIN_TTL = 300
"""Seconds a pending (password-verified) login stays valid for 2FA."""

MAX_PENDING_ATTEMPTS = 5
"""Failed second-factor attempts allowed before a pending login is dropped."""

TOTP_SETUP_TTL = 600
"""Seconds a pending TOTP enrollment secret stays valid."""

WEBAUTHN_STATE_TTL = 600
"""Seconds a staged WebAuthn ceremony state stays valid."""

RECOVERY_CODE_COUNT = 10
"""Number of recovery codes generated per batch."""

_TOTP_SECRET_KEY = "2fa:totp-setup:{}"
_PENDING_LOGIN_KEY = "2fa:pending:{}"
_WEBAUTHN_REG_KEY = "2fa:webauthn-reg:{}"
_WEBAUTHN_AUTH_KEY = "2fa:webauthn-auth:{}"

_LOCALHOST_NAMES = {"localhost", "127.0.0.1", "::1"}


class TwoFactorError(ValueError):
    """Raised when a two-factor operation cannot be fulfilled."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class PendingLogin:
    """State of a password-verified login awaiting its second factor."""

    user_id: str
    attempts: int = 0


def _pending_login_key(token: str) -> str:
    return _PENDING_LOGIN_KEY.format(_hash_token(token))


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


def _import_pyotp():
    """Import pyotp, raising a clear error if the package is missing."""
    try:
        import pyotp

        return pyotp
    except ImportError as exc:
        raise TwoFactorError(
            "The 'pyotp' package is required for TOTP two-factor authentication",
            status_code=500,
        ) from exc


def _decrypt_totp_secret(user: User) -> Optional[str]:
    """Decrypt the user's stored TOTP secret, if any."""
    if not user.totp_secret:
        return None
    try:
        return decrypt_secret(user.totp_secret)
    except Exception:
        return None


def verify_totp(user: User, code: str) -> bool:
    """Verify a TOTP code against the user's enrolled secret."""
    secret = _decrypt_totp_secret(user)
    if not secret:
        return False
    pyotp = _import_pyotp()
    return cast(bool, pyotp.TOTP(secret).verify(code.strip(), valid_window=1))


async def begin_totp_setup(user: User, config: SonghiveConfig, redis: Redis) -> tuple[str, str]:
    """
    Generate a pending TOTP secret for the user and return ``(secret, uri)``.

    The secret is held in Redis until :func:`confirm_totp_setup` verifies a
    code — the shared secret is never persisted before that proof.
    """
    pyotp = _import_pyotp()
    secret = pyotp.random_base32()
    issuer = config.federation.instance_name or "Songhive"
    uri = cast(str, pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer))
    await redis.set(_TOTP_SECRET_KEY.format(user.id), secret, ex=TOTP_SETUP_TTL)
    return secret, uri


def totp_qr_code_data_uri(uri: str) -> str:
    """Render an otpauth:// URI as a PNG QR code ``data:`` URI."""
    try:
        import qrcode
    except ImportError as exc:
        raise TwoFactorError(
            "The 'qrcode' package is required to render TOTP QR codes",
            status_code=500,
        ) from exc

    image = qrcode.make(uri)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def confirm_totp_setup(session: AsyncSession, user: User, code: str, redis: Redis) -> List[str]:
    """
    Confirm a pending TOTP enrollment with a current code.

    On success the encrypted secret is stored on the user and a fresh batch
    of recovery codes is generated and returned (shown once to the caller).
    """
    key = _TOTP_SECRET_KEY.format(user.id)
    secret = await redis.get(key)
    if not secret:
        raise TwoFactorError("No pending TOTP setup; call the setup endpoint first", status_code=400)

    pyotp = _import_pyotp()
    if not pyotp.TOTP(secret).verify(code.strip(), valid_window=1):
        raise TwoFactorError("Invalid verification code", status_code=400)

    user.totp_secret = encrypt_secret(secret)
    await redis.delete(key)
    codes = await regenerate_recovery_codes(session, user)
    await session.flush()
    return codes


async def disable_totp(session: AsyncSession, user: User, password: str) -> None:
    """Disable TOTP for the user after re-verifying their password."""
    if not verify_password(password, user.password_hash):
        raise TwoFactorError("Invalid password", status_code=403)
    user.totp_secret = None
    if not await _has_webauthn_credentials(session, user):
        await _delete_recovery_codes(session, user)
    await session.flush()


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


def _normalize_recovery_code(code: str) -> str:
    """Normalize a recovery code for hashing: lowercase, no separators."""
    return code.strip().lower().replace("-", "").replace(" ", "")


def _hash_recovery_code(code: str) -> str:
    return hashlib.sha256(_normalize_recovery_code(code).encode("utf-8")).hexdigest()


def _generate_recovery_code() -> str:
    """Return a recovery code formatted as ``xxxx-xxxx-xxxx``."""
    raw = secrets.token_hex(6)
    return "-".join(raw[i : i + 4] for i in range(0, 12, 4))


async def regenerate_recovery_codes(session: AsyncSession, user: User) -> List[str]:
    """Replace the user's recovery codes with a fresh batch and return it."""
    await _delete_recovery_codes(session, user)
    codes = [_generate_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    for code in codes:
        session.add(RecoveryCode(user_id=user.id, code_hash=_hash_recovery_code(code)))
    await session.flush()
    return codes


async def _delete_recovery_codes(session: AsyncSession, user: User) -> None:
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))


async def remaining_recovery_codes(session: AsyncSession, user: User) -> int:
    """Return the number of unused recovery codes the user has left."""
    result = await session.execute(
        select(func.count(RecoveryCode.id)).where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.used_at.is_(None),
        )
    )
    return result.scalar() or 0


async def consume_recovery_code(session: AsyncSession, user: User, code: str) -> bool:
    """Consume a single-use recovery code, returning True on success."""
    result = await session.execute(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == _hash_recovery_code(code),
            RecoveryCode.used_at.is_(None),
        )
    )
    recovery_code = result.scalar_one_or_none()
    if recovery_code is None:
        return False
    recovery_code.used_at = datetime.now(timezone.utc)
    await session.flush()
    return True


# ---------------------------------------------------------------------------
# WebAuthn / security keys
# ---------------------------------------------------------------------------


def _import_fido2():
    """Import the fido2 pieces needed by the ceremonies."""
    try:
        from fido2.server import Fido2Server
        from fido2.webauthn import (
            AttestedCredentialData,
            PublicKeyCredentialRpEntity,
            PublicKeyCredentialUserEntity,
        )

        return Fido2Server, AttestedCredentialData, PublicKeyCredentialRpEntity, PublicKeyCredentialUserEntity
    except ImportError as exc:
        raise TwoFactorError(
            "The 'fido2' package is required for security-key two-factor authentication",
            status_code=500,
        ) from exc


def _rp_id(config: SonghiveConfig, request_host: Optional[str] = None) -> str:
    """Resolve the WebAuthn relying-party id for this instance."""
    if config.federation.instance_domain:
        return config.federation.instance_domain
    if request_host:
        return request_host.split(":")[0]
    return "localhost"


def _make_fido2_server(config: SonghiveConfig, rp_id: str):
    """Build a Fido2Server whose origin check accepts this instance's host."""
    Fido2Server, _, PublicKeyCredentialRpEntity, _ = _import_fido2()
    rp = PublicKeyCredentialRpEntity(id=rp_id, name=config.federation.instance_name or "Songhive")

    def _verify_origin(origin: str) -> bool:
        parsed = urlparse(origin)
        host = parsed.hostname or ""
        if host != rp_id:
            return False
        # WebAuthn requires secure contexts; plain HTTP is only tolerable for
        # local development hosts and debug deployments.
        if parsed.scheme == "https":
            return True
        return parsed.scheme == "http" and (host in _LOCALHOST_NAMES or config.server.debug)

    return Fido2Server(rp, verify_origin=_verify_origin)


async def _load_credentials(session: AsyncSession, user: User) -> List[WebAuthnCredential]:
    result = await session.execute(select(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id))
    return list(result.scalars().all())


async def list_webauthn_credentials(session: AsyncSession, user: User) -> List[WebAuthnCredential]:
    """Return the user's registered security keys."""
    return await _load_credentials(session, user)


async def _has_webauthn_credentials(session: AsyncSession, user: User) -> bool:
    result = await session.execute(
        select(func.count(WebAuthnCredential.id)).where(WebAuthnCredential.user_id == user.id)
    )
    return (result.scalar() or 0) > 0


def _credential_blobs(credentials: List[WebAuthnCredential]) -> list:
    """Rebuild fido2 AttestedCredentialData objects from stored blobs."""
    _, AttestedCredentialData, _, _ = _import_fido2()
    return [AttestedCredentialData(bytes(cred.credential_data)) for cred in credentials]


async def webauthn_register_begin(
    session: AsyncSession,
    user: User,
    config: SonghiveConfig,
    redis: Redis,
    request_host: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a WebAuthn registration ceremony and return publicKey options."""
    rp_id = _rp_id(config, request_host)
    server = _make_fido2_server(config, rp_id)
    _, _, _, PublicKeyCredentialUserEntity = _import_fido2()

    existing = await _load_credentials(session, user)
    user_entity = PublicKeyCredentialUserEntity(
        id=user.id.encode("utf-8"),
        name=user.username,
        display_name=user.display_name or user.username,
    )
    options, state = server.register_begin(
        user_entity,
        credentials=_credential_blobs(existing),
    )
    await redis.set(_WEBAUTHN_REG_KEY.format(user.id), json.dumps(state), ex=WEBAUTHN_STATE_TTL)
    return dict(options)


async def webauthn_register_complete(
    session: AsyncSession,
    user: User,
    credential: Dict[str, Any],
    name: Optional[str],
    config: SonghiveConfig,
    redis: Redis,
    request_host: Optional[str] = None,
) -> WebAuthnCredential:
    """Finish a WebAuthn registration ceremony and persist the credential."""
    key = _WEBAUTHN_REG_KEY.format(user.id)
    raw_state = await redis.get(key)
    if not raw_state:
        raise TwoFactorError("No pending security-key registration", status_code=400)

    server = _make_fido2_server(config, _rp_id(config, request_host))
    try:
        auth_data = server.register_complete(json.loads(raw_state), credential)
    except Exception as exc:
        raise TwoFactorError("Security-key registration failed", status_code=400) from exc
    await redis.delete(key)

    cred_data = auth_data.credential_data
    if cred_data is None:
        raise TwoFactorError("Security-key registration did not return a credential", status_code=400)

    from fido2.utils import websafe_encode

    transports = credential.get("response", {}).get("transports")
    record = WebAuthnCredential(
        user_id=user.id,
        credential_id=websafe_encode(cred_data.credential_id),
        credential_data=bytes(cred_data),
        name=(name or "").strip() or None,
        transports=",".join(transports) if isinstance(transports, list) else None,
    )
    session.add(record)
    try:
        await session.flush()
    except Exception as exc:
        raise TwoFactorError("This security key is already registered", status_code=409) from exc
    return record


async def webauthn_auth_begin(
    session: AsyncSession,
    user: User,
    pending_token: str,
    config: SonghiveConfig,
    redis: Redis,
    request_host: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a WebAuthn assertion ceremony for a pending login."""
    credentials = await _load_credentials(session, user)
    if not credentials:
        raise TwoFactorError("No security keys registered", status_code=400)

    server = _make_fido2_server(config, _rp_id(config, request_host))
    options, state = server.authenticate_begin(_credential_blobs(credentials))
    await redis.set(
        _WEBAUTHN_AUTH_KEY.format(_hash_token(pending_token)),
        json.dumps(state),
        ex=PENDING_LOGIN_TTL,
    )
    return dict(options)


async def webauthn_auth_complete(
    session: AsyncSession,
    user: User,
    pending_token: str,
    credential: Dict[str, Any],
    config: SonghiveConfig,
    redis: Redis,
    request_host: Optional[str] = None,
) -> bool:
    """Verify a WebAuthn assertion for a pending login."""
    key = _WEBAUTHN_AUTH_KEY.format(_hash_token(pending_token))
    raw_state = await redis.get(key)
    if not raw_state:
        raise TwoFactorError("No pending security-key challenge", status_code=400)

    credentials = await _load_credentials(session, user)
    server = _make_fido2_server(config, _rp_id(config, request_host))
    try:
        used = server.authenticate_complete(json.loads(raw_state), _credential_blobs(credentials), credential)
    except Exception as exc:
        raise TwoFactorError("Security-key verification failed", status_code=400) from exc
    await redis.delete(key)
    return used is not None


async def delete_webauthn_credential(session: AsyncSession, user: User, credential_id: str) -> bool:
    """Delete one of the user's security keys by its id."""
    credential = await session.scalar(
        select(WebAuthnCredential).where(
            WebAuthnCredential.id == credential_id,
            WebAuthnCredential.user_id == user.id,
        )
    )
    deleted = credential is not None
    if credential is not None:
        await session.delete(credential)
        await session.flush()
    if deleted and not user.totp_secret and not await _has_webauthn_credentials(session, user):
        await _delete_recovery_codes(session, user)
    await session.flush()
    return deleted


# ---------------------------------------------------------------------------
# Pending logins
# ---------------------------------------------------------------------------


async def user_has_2fa(session: AsyncSession, user: User) -> bool:
    """Return True when the user has any second factor enrolled."""
    if user.totp_secret:
        return True
    return await _has_webauthn_credentials(session, user)


async def enabled_methods(session: AsyncSession, user: User) -> List[str]:
    """Return the second-factor methods available for the user's login."""
    methods: List[str] = []
    if user.totp_secret:
        methods.append("totp")
    if await _has_webauthn_credentials(session, user):
        methods.append("webauthn")
    if await remaining_recovery_codes(session, user):
        methods.append("recovery_code")
    return methods


async def create_pending_login(user: User, redis: Redis) -> str:
    """Create a pending-login token for a password-verified user."""
    token = secrets.token_urlsafe(32)
    await redis.set(
        _pending_login_key(token),
        json.dumps({"user_id": user.id, "attempts": 0}),
        ex=PENDING_LOGIN_TTL,
    )
    return token


async def _read_pending_login(token: str, redis: Redis) -> Optional[PendingLogin]:
    raw = await redis.get(_pending_login_key(token))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    user_id = data.get("user_id")
    if not user_id:
        return None
    return PendingLogin(user_id=user_id, attempts=int(data.get("attempts") or 0))


async def get_pending_login(token: str, redis: Redis) -> Optional[PendingLogin]:
    """Return the pending login for ``token`` without consuming it."""
    return await _read_pending_login(token, redis)


async def fail_pending_login(token: str, redis: Redis) -> bool:
    """
    Record a failed second-factor attempt for a pending login.

    Returns False when the attempt budget is exhausted and the pending login
    has been dropped, forcing a fresh password authentication.
    """
    key = _pending_login_key(token)
    pending = await _read_pending_login(token, redis)
    if pending is None:
        return False
    pending.attempts += 1
    if pending.attempts >= MAX_PENDING_ATTEMPTS:
        await redis.delete(key)
        return False
    await redis.set(key, json.dumps({"user_id": pending.user_id, "attempts": pending.attempts}), keepttl=True)
    return True


async def pop_pending_login(token: str, redis: Redis) -> Optional[PendingLogin]:
    """Consume (delete) a pending login token, returning its payload."""
    key = _pending_login_key(token)
    raw = await redis.get(key)
    if raw:
        await redis.delete(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    user_id = data.get("user_id")
    if not user_id:
        return None
    return PendingLogin(user_id=user_id)


async def verify_login_code(session: AsyncSession, user: User, code: str) -> bool:
    """
    Verify a second-factor code for login.

    TOTP is tried first when enrolled; anything that does not look like a
    6-digit OTP is tried as a recovery code.
    """
    if user.totp_secret and verify_totp(user, code):
        return True
    return await consume_recovery_code(session, user, code)


# ---------------------------------------------------------------------------
# Administrative clearing
# ---------------------------------------------------------------------------


async def clear_two_factor(session: AsyncSession, user: User) -> None:
    """Remove every second factor enrolled for the user."""
    user.totp_secret = None
    await session.execute(delete(WebAuthnCredential).where(WebAuthnCredential.user_id == user.id))
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await session.flush()
