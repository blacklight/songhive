"""
Tests for server-managed HttpOnly auth cookies and the CSRF middleware.
"""

import pytest
from fastapi import status

from songhive.api.cookies import (
    ACCESS_TOKEN_COOKIE,
    CSRF_TOKEN_COOKIE,
    REFRESH_TOKEN_COOKIE,
)
from songhive.api.middleware.auth import create_access_token
from songhive.services.auth import create_user


def _set_cookies(response) -> dict[str, str]:
    """Return raw Set-Cookie header values keyed by cookie name."""
    cookies: dict[str, str] = {}
    for header in response.headers.get_list("set-cookie"):
        name, _, rest = header.partition("=")
        cookies[name] = rest
    return cookies


def _register_and_login(client, username: str = "alice") -> dict:
    client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret",
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "secret"},
    )
    assert response.status_code == status.HTTP_200_OK
    return response


@pytest.mark.asyncio
async def test_login_sets_auth_cookies(client):
    """Successful login sets HttpOnly access/refresh cookies and a JS-readable CSRF cookie."""
    response = _register_and_login(client)
    cookies = _set_cookies(response)

    assert ACCESS_TOKEN_COOKIE in cookies
    assert REFRESH_TOKEN_COOKIE in cookies
    assert CSRF_TOKEN_COOKIE in cookies

    access = cookies[ACCESS_TOKEN_COOKIE]
    assert "httponly" in access.lower()
    assert "samesite=lax" in access.lower()
    assert "path=/" in access.lower()

    refresh = cookies[REFRESH_TOKEN_COOKIE]
    assert "httponly" in refresh.lower()
    assert "path=/api/v1/auth" in refresh.lower()

    # The CSRF cookie must be readable by the SPA.
    csrf = cookies[CSRF_TOKEN_COOKIE]
    assert "httponly" not in csrf.lower()

    # The test config runs in debug mode, so cookies are not Secure.
    assert "secure" not in access.lower()

    # The JSON body still carries the token pair for API clients.
    data = response.json()
    assert data["access_token"]
    assert data["refresh_token"]


@pytest.mark.asyncio
async def test_login_cookies_secure_when_configured(app, client):
    """cookie_secure=true forces the Secure flag on auth cookies."""
    app.state.config.auth.cookie_secure = True
    response = _register_and_login(client, "secure-user")
    access = _set_cookies(response)[ACCESS_TOKEN_COOKIE]
    assert "secure" in access.lower()


@pytest.mark.asyncio
async def test_cookie_auth_authenticates_request(client):
    """A request carrying only the access_token cookie is authenticated."""
    _register_and_login(client)

    response = client.get("/api/v1/users/me")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["username"] == "alice"


@pytest.mark.asyncio
async def test_bearer_token_takes_priority_over_cookie(client, db_session, config):
    """An explicit Authorization header wins over the ambient cookie."""
    _register_and_login(client)  # alice via cookie
    other = await create_user(db_session, "bob", "bob@example.com", "secret")
    await db_session.flush()
    token = create_access_token(other.id, config.auth.secret_key)

    response = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["username"] == "bob"


@pytest.mark.asyncio
async def test_refresh_with_cookie_only(client):
    """The refresh endpoint accepts the refresh_token cookie without a body."""
    login = _register_and_login(client)
    original_refresh = login.json()["refresh_token"]

    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["refresh_token"] != original_refresh
    assert data["access_token"]

    # The rotated pair is written back to the cookies.
    assert client.cookies.get(REFRESH_TOKEN_COOKIE) != original_refresh


@pytest.mark.asyncio
async def test_refresh_with_json_body_still_works(client):
    """API clients can keep sending the refresh token in the JSON body."""
    login = _register_and_login(client)
    client.cookies.clear()

    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login.json()["refresh_token"]},
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["access_token"]


@pytest.mark.asyncio
async def test_refresh_without_token_returns_401(client):
    """A refresh with neither body nor cookie token is rejected."""
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_failed_refresh_clears_cookies(client):
    """An invalid refresh token clears the auth cookies."""
    _register_and_login(client)
    client.cookies.clear()
    client.cookies.set(REFRESH_TOKEN_COOKIE, "not-a-real-token")

    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    cookies = _set_cookies(response)
    assert "max-age=0" in cookies[ACCESS_TOKEN_COOKIE].lower()
    assert REFRESH_TOKEN_COOKIE in cookies


@pytest.mark.asyncio
async def test_logout_with_cookie_clears_cookies(client):
    """Logout reads the refresh cookie and clears all auth cookies."""
    login = _register_and_login(client)
    refresh_token = login.json()["refresh_token"]

    response = client.post("/api/v1/auth/logout")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True
    assert client.cookies.get(ACCESS_TOKEN_COOKIE) is None
    assert client.cookies.get(REFRESH_TOKEN_COOKIE) is None
    assert client.cookies.get(CSRF_TOKEN_COOKIE) is None

    # The revoked refresh token can no longer be used.
    client.cookies.set(REFRESH_TOKEN_COOKIE, refresh_token)
    replay = client.post("/api/v1/auth/refresh")
    assert replay.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_logout_without_token_still_succeeds(client):
    """Best-effort logout clears cookies even with no refresh token."""
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_sessions_marks_current_session_from_cookie(client):
    """GET /auth/sessions marks the caller's session via the refresh cookie."""
    login = _register_and_login(client)

    response = client.get("/api/v1/auth/sessions")
    assert response.status_code == status.HTTP_200_OK
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["is_current"] is True
    # The session id is the hash of the refresh token, not the token itself.
    assert items[0]["id"] != login.json()["refresh_token"]


@pytest.mark.asyncio
async def test_unsafe_cookie_request_without_csrf_is_rejected(client):
    """Unsafe cookie-authenticated requests must present the CSRF token."""
    _register_and_login(client)

    response = client.delete("/api/v1/auth/sessions/nonexistent")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "CSRF token missing or invalid"


@pytest.mark.asyncio
async def test_unsafe_cookie_request_with_csrf_is_accepted(client):
    """A matching X-CSRF-Token header satisfies the middleware."""
    _register_and_login(client)
    csrf = client.cookies.get(CSRF_TOKEN_COOKIE)
    assert csrf is not None

    response = client.delete(
        "/api/v1/auth/sessions/nonexistent",
        headers={"X-CSRF-Token": csrf},
    )
    # The middleware passes; the handler 404s on the missing session.
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_bearer_request_needs_no_csrf(client, db_session, config):
    """Requests authenticated via Authorization header skip the CSRF check."""
    _register_and_login(client)  # cookie is present alongside the header
    user = await create_user(db_session, "bob", "bob@example.com", "secret")
    await db_session.flush()
    token = create_access_token(user.id, config.auth.secret_key)

    response = client.delete(
        "/api/v1/auth/sessions/nonexistent",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_safe_cookie_request_needs_no_csrf(client):
    """Safe methods never require a CSRF token."""
    _register_and_login(client)
    response = client.get("/api/v1/users/me")
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_auth_endpoints_are_csrf_exempt(client):
    """Login/refresh/logout work without a CSRF token even when cookies exist."""
    _register_and_login(client)

    # Re-login with cookies present: no CSRF header needed.
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_file_download_accepts_cookie_auth(client):
    """Media downloads authenticate through the access_token cookie."""
    login = _register_and_login(client)
    assert login.status_code == status.HTTP_200_OK
    csrf = client.cookies.get(CSRF_TOKEN_COOKIE)

    upload = client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("hello.txt", b"hello", "text/plain")},
        headers={"X-CSRF-Token": csrf},
    )
    assert upload.status_code == status.HTTP_200_OK
    file_id = upload.json()["id"]

    # The download endpoint authenticates via the cookie alone.
    response = client.get(f"/api/v1/files/{file_id}/download")
    assert response.status_code == status.HTTP_200_OK
    assert response.content == b"hello"
