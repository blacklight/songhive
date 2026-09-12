"""
Server-managed authentication cookies.

Login and refresh responses carry the token pair in the JSON body (for API
clients) and also set ``HttpOnly`` cookies so browser media elements
(``<img>``, ``<audio>``, WebSocket handshakes) can authenticate without
JavaScript ever touching the tokens.

A third, non-``HttpOnly`` ``csrf_token`` cookie backs the double-submit CSRF
check in :mod:`songhive.api.middleware.csrf`: the SPA reads it and echoes it
back in the ``X-CSRF-Token`` header on unsafe requests.
"""

import secrets
from typing import Optional

from starlette.responses import Response

from ..config.schema import SonghiveConfig
from ..users.tokens import TokenPair

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"
CSRF_TOKEN_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"

# The refresh cookie is only needed by the session lifecycle endpoints, so it
# is scoped to the auth prefix instead of being sent on every request.
REFRESH_COOKIE_PATH = "/api/v1/auth"


def generate_csrf_token() -> str:
    """Return a fresh random double-submit CSRF token."""
    return secrets.token_urlsafe(32)


def _cookie_secure(config: SonghiveConfig) -> bool:
    """Resolve the Secure flag: explicit config wins, else infer from debug."""
    if config.auth.cookie_secure is not None:
        return config.auth.cookie_secure
    return not config.server.debug


def set_auth_cookies(
    response: Response,
    config: SonghiveConfig,
    token_pair: TokenPair,
    csrf_token: Optional[str] = None,
) -> str:
    """Set access, refresh, and CSRF cookies for a newly issued token pair.

    Returns the CSRF token that was set (useful for tests and callers that
    want to reuse it).
    """
    secure = _cookie_secure(config)
    samesite = config.auth.cookie_samesite
    domain = config.auth.cookie_domain
    access_max_age = token_pair.expires_in or config.auth.access_token_expiry_minutes * 60
    refresh_max_age = config.auth.refresh_token_expiry_days * 86400

    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        token_pair.access_token,
        max_age=access_max_age,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        token_pair.refresh_token,
        max_age=refresh_max_age,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path=REFRESH_COOKIE_PATH,
    )
    csrf = csrf_token or generate_csrf_token()
    response.set_cookie(
        CSRF_TOKEN_COOKIE,
        csrf,
        max_age=refresh_max_age,
        httponly=False,
        secure=secure,
        samesite=samesite,
        domain=domain,
        path="/",
    )
    return csrf


def clear_auth_cookies(response: Response, config: SonghiveConfig) -> None:
    """Expire all auth cookies; paths and domain must match the set calls."""
    domain = config.auth.cookie_domain
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/", domain=domain)
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path=REFRESH_COOKIE_PATH, domain=domain)
    response.delete_cookie(CSRF_TOKEN_COOKIE, path="/", domain=domain)
