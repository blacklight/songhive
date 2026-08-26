"""
Authentication middleware: JWT validation and OAuth2 token handling.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, cast

import jwt
from fastapi import Request


def create_access_token(user_id: str, secret_key: str, expires_minutes: Optional[int] = 15) -> str:
    """Create a JWT access token."""
    payload = {
        "sub": user_id,
        "iat": datetime.now(timezone.utc),
    }
    if expires_minutes is not None:
        payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    return cast(str, jwt.encode(payload, secret_key, algorithm="HS256"))


def decode_access_token(token: str, secret_key: str) -> Optional[str]:
    """Decode a JWT token and return the user_id, or None if invalid."""
    try:
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        sub = payload.get("sub")
        return cast(Optional[str], sub) if sub is not None else None
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def extract_token(request: Request) -> Optional[str]:
    """Extract an access token from the Authorization header or access_token cookie.

    Cookies are supported because browser media tags (<img>, <audio>) cannot
    send custom Authorization headers. The cookie is kept in sync by the SPA
    auth store.
    """
    auth_header: str = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    token = request.cookies.get("access_token")
    if token:
        return token
    return None


def create_api_token_jwt(
    user_id: str,
    secret_key: str,
    jti: str,
    expires_at: Optional[datetime],
) -> str:
    """Create a long-lived API-token JWT."""
    payload = {
        "sub": user_id,
        "jti": jti,
        "token_type": "api_token",
        "iat": datetime.now(timezone.utc),
    }
    if expires_at is not None:
        payload["exp"] = expires_at
    return cast(str, jwt.encode(payload, secret_key, algorithm="HS256"))


def decode_token_payload(token: str, secret_key: str) -> Optional[dict]:
    """Decode a JWT with ``verify_exp=False`` and return the payload, or None."""
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return None

    if not isinstance(payload, dict):
        return None
    return payload
