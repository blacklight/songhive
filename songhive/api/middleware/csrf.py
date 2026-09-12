"""
Double-submit CSRF protection for cookie-authenticated requests.

Once browsers authenticate through the ``access_token``/``refresh_token``
HttpOnly cookies, unsafe methods become CSRF-able in principle: cookies are
attached automatically. The primary mitigation is ``SameSite=Lax`` (cookies
are never sent on cross-site subrequests), and this middleware adds
defense-in-depth for same-site threats such as a compromised sibling
subdomain: unsafe requests that carry auth cookies and no ``Authorization``
header must echo the ``csrf_token`` cookie in the ``X-CSRF-Token`` header.

The check only engages when an auth cookie is present, so bearer-token API
clients, safe methods, and the unauthenticated auth endpoints are unaffected.
"""

import secrets
from http.cookies import SimpleCookie

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from ..cookies import ACCESS_TOKEN_COOKIE, CSRF_HEADER, CSRF_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE
from ..errors import ProblemDetails, ProblemJSONResponse

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Endpoints that establish or tear down sessions. They cannot require a CSRF
# token that does not exist yet (login) or that rotate the credentials
# themselves (refresh/logout); machine-facing OAuth endpoints are exempt so
# third-party clients posting with a cookie-bearing jar are not rejected.
_EXEMPT_PATHS = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/verify-email",
        "/api/v1/auth/verify-email/resend",
        "/api/v1/auth/password-reset/request",
        "/api/v1/auth/password-reset/confirm",
        "/api/v1/auth/oauth/token",
        "/api/v1/auth/oauth/revoke",
        "/api/v1/auth/oauth/introspect",
    }
)


class CsrfMiddleware:
    """Reject unsafe cookie-authenticated requests lacking a matching CSRF token."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _UNSAFE_METHODS or scope["path"] in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if "authorization" in headers:
            await self.app(scope, receive, send)
            return

        cookies = _parse_cookies(headers.get("cookie", ""))
        if ACCESS_TOKEN_COOKIE not in cookies and REFRESH_TOKEN_COOKIE not in cookies:
            await self.app(scope, receive, send)
            return

        expected = cookies.get(CSRF_TOKEN_COOKIE, "")
        presented = headers.get(CSRF_HEADER, "")
        if expected and presented and secrets.compare_digest(expected, presented):
            await self.app(scope, receive, send)
            return

        problem = ProblemDetails(
            status=403,
            detail="CSRF token missing or invalid",
            instance=scope["path"],
        )
        response = ProblemJSONResponse(problem.model_dump(mode="json"), status_code=403)
        await response(scope, receive, send)


def _parse_cookies(header: str) -> dict[str, str]:
    """Parse a Cookie header into a name → value mapping."""
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(header)
    except Exception:
        return {}
    return {name: morsel.value for name, morsel in jar.items()}
