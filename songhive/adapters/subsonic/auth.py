"""
Subsonic authentication.

Clients authenticate every request with query (or form) parameters:

* ``u`` + ``p`` — username plus password, either plain or hex-encoded via
  the ``enc:`` prefix. Songhive API tokens are also accepted here, which
  lets users keep their real password out of third-party apps.
* ``u`` + ``apiKey`` — OpenSubsonic API-key authentication; the key is a
  Songhive API token (``/api/v1/api-tokens``).
* ``u`` + ``t`` + ``s`` — salted-token auth, ``token = md5(password + salt)``.
  Verifying it requires the plaintext password, which is incompatible with
  bcrypt-only storage — so it works when the client password is a Songhive
  API token instead: HS256 JWTs are deterministic given their claims, and
  every claim is recoverable from the ``api_tokens`` row (``user_id``,
  ``jti``, ``expires_at`` and ``created_at``, which ``issue_api_token``
  pins to the ``iat`` claim). Each candidate JWT is rebuilt byte-for-byte
  and hashed, so no usable credential is stored for this to work.

The helpers here are shared by the FastAPI routes and the Tornado
streaming handlers.
"""

import binascii
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from redis.asyncio import Redis
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.middleware.auth import create_api_token_jwt, decode_token_payload
from ...models.api_token import ApiToken
from ...models.user import User
from ...services.auth import get_user_by_username, verify_password
from ...users.api_tokens import validate_api_token
from .errors import MISSING_PARAMETER, WRONG_CREDENTIALS, SubsonicError, missing_parameter

_MISSING_CREDENTIALS = "No authentication credentials provided; pass p or apiKey"


def _decode_password(raw: str) -> str:
    """Decode a ``p`` parameter, unwrapping the ``enc:`` hex encoding."""
    if raw.startswith("enc:"):
        try:
            return binascii.unhexlify(raw[4:]).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise SubsonicError(WRONG_CREDENTIALS, "Wrong username or password") from exc
    return raw


async def _user_for_api_token(
    db: AsyncSession,
    token: str,
    secret_key: str,
    redis: Optional[Redis],
) -> Optional[User]:
    """Resolve a Songhive API token (JWT) to its active user, or ``None``."""
    payload = decode_token_payload(token, secret_key)
    if payload is None or payload.get("token_type") != "api_token":
        return None
    jti = payload.get("jti")
    if not jti:
        return None
    api_token = await validate_api_token(db, jti, redis=redis)
    if api_token is None:
        return None
    user = await db.get(User, api_token.user_id)
    return user if user is not None and user.is_active else None


async def _verify_salted_token(
    db: AsyncSession,
    user: User,
    token: str,
    salt: str,
    secret_key: str,
    redis: Optional[Redis],
) -> bool:
    """
    Check ``token == md5(api_token + salt)`` against the user's active API tokens.

    Candidate JWTs are reconstructed from the stored ``api_tokens`` rows —
    HS256 output is deterministic given identical claims, and ``created_at``
    is pinned to the ``iat`` claim at issuance. Tokens issued before that
    pin may have ``iat`` and ``created_at`` straddling a second boundary
    (PyJWT truncates to whole seconds), so the neighboring seconds are tried
    as well.
    """
    now = datetime.now(timezone.utc)
    stmt = select(ApiToken).where(
        ApiToken.user_id == user.id,
        ApiToken.revoked_at.is_(None),
        or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now),
    )
    result = await db.execute(stmt)
    salt_bytes = salt.encode("utf-8")
    expected = token.lower()
    for api_token in result.scalars():
        for offset in (-1, 0, 1):
            iat = api_token.created_at + timedelta(seconds=offset)
            candidate = create_api_token_jwt(str(user.id), secret_key, api_token.jti, api_token.expires_at, iat=iat)
            digest = hashlib.md5(candidate.encode("utf-8") + salt_bytes, usedforsecurity=False).hexdigest()
            if hmac.compare_digest(digest, expected):
                await validate_api_token(db, api_token.jti, redis=redis)
                return True
    return False


async def authenticate_subsonic(
    db: AsyncSession,
    params: Mapping[str, Any],
    secret_key: str,
    redis: Optional[Redis] = None,
) -> User:
    """
    Authenticate Subsonic request parameters and return the active user.

    Raises :class:`SubsonicError` with the appropriate protocol code on
    failure — 10 for missing parameters and 40 for wrong credentials.
    Salted-token (``t``/``s``) auth is supported when the client password
    is a Songhive API token; the real password only works via ``p``.
    """
    username = params.get("u")
    if not username:
        raise SubsonicError(MISSING_PARAMETER, "Required parameter is missing: u")

    token = params.get("t")
    salt = params.get("s")
    if token is not None or salt is not None:
        if token is None or salt is None:
            raise missing_parameter("t" if token is None else "s")
        user = await get_user_by_username(db, str(username))
        if user is None or not user.is_active:
            raise SubsonicError(WRONG_CREDENTIALS, "Wrong username or password")
        if await _verify_salted_token(db, user, str(token), str(salt), secret_key, redis):
            return user
        raise SubsonicError(WRONG_CREDENTIALS, "Wrong username or password")

    password = params.get("p")
    api_key = params.get("apiKey")
    if password is None and api_key is None:
        raise SubsonicError(MISSING_PARAMETER, _MISSING_CREDENTIALS)

    user = await get_user_by_username(db, str(username))
    if user is None or not user.is_active:
        raise SubsonicError(WRONG_CREDENTIALS, "Wrong username or password")

    if api_key is not None:
        token_user = await _user_for_api_token(db, str(api_key), secret_key, redis)
        if token_user is not None and str(token_user.id) == str(user.id):
            return user
        raise SubsonicError(WRONG_CREDENTIALS, "Wrong username or password")

    assert password is not None
    candidate = _decode_password(str(password))
    # bcrypt rejects inputs longer than 72 bytes; a ``p`` value carrying an
    # API token (JWT) is far longer, so skip straight to the token check.
    if len(candidate.encode("utf-8")) <= 72 and verify_password(candidate, user.password_hash):
        return user

    # ``p`` may also carry a Songhive API token instead of the password.
    token_user = await _user_for_api_token(db, candidate, secret_key, redis)
    if token_user is not None and str(token_user.id) == str(user.id):
        return user

    raise SubsonicError(WRONG_CREDENTIALS, "Wrong username or password")
