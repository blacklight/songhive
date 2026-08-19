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
    """Extract a Bearer token from the Authorization header."""
    auth_header: str = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None
