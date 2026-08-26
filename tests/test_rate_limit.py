"""
Tests for the Redis-backed sliding-window rate limiter.
"""

import io
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request, status

from songhive.api.middleware import rate_limit as rate_limit_module
from songhive.api.middleware.rate_limit import (
    _check_rate_limit,
    _client_ip,
    _rate_limit_key,
    check_rate_limit,
)
from songhive.config.schema import SonghiveConfig
from songhive.services.auth import create_user
from songhive.services.metadata import AudioMetadata


def _request(
    path: str = "/api/v1/auth/login",
    headers: list[tuple[bytes, bytes]] | None = None,
    client: tuple[str, int] | None = None,
) -> Request:
    """Build a minimal ASGI request scope for unit tests."""
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": headers or [],
        "server": ("testserver", 80),
    }
    if client:
        scope["client"] = client
    return Request(scope)


def test_client_ip_from_x_forwarded_for():
    """Test that X-Forwarded-For is parsed when at least one hop is trusted."""
    request = _request(
        headers=[(b"x-forwarded-for", b"203.0.113.1, 70.0.0.1")],
    )
    assert _client_ip(request, trusted_hops=1) == "203.0.113.1"


def test_client_ip_ignores_x_forwarded_for_without_trusted_hops():
    """Test that X-Forwarded-For is ignored when no proxy hops are trusted."""
    request = _request(
        headers=[(b"x-forwarded-for", b"203.0.113.1, 70.0.0.1")],
        client=("127.0.0.1", 12345),
    )
    assert _client_ip(request, trusted_hops=0) == "127.0.0.1"


def test_client_ip_from_real_ip():
    """Test that X-Real-IP is used when X-Forwarded-For is absent."""
    request = _request(
        headers=[(b"x-real-ip", b"192.0.2.1")],
    )
    assert _client_ip(request) == "192.0.2.1"


def test_client_ip_from_request_client():
    """Test that request.client is used as a fallback."""
    request = _request(client=("127.0.0.1", 12345))
    assert _client_ip(request) == "127.0.0.1"


def test_client_ip_returns_unknown_when_missing():
    """Test that an unknown IP is returned when nothing else is available."""
    request = _request()
    assert _client_ip(request) == "unknown"


def test_rate_limit_key_without_identifier():
    """Test the rate limit key for scope + path."""
    assert _rate_limit_key("1.2.3.4", "/api/v1/auth/login") == "rl:1.2.3.4:/api/v1/auth/login"


def test_rate_limit_key_with_identifier():
    """Test the rate limit key including an optional identifier."""
    assert _rate_limit_key("1.2.3.4", "/api/v1/auth/login", "alice") == ("rl:1.2.3.4:/api/v1/auth/login:alice")


@pytest.mark.asyncio
async def test_check_rate_limit_allows_requests_under_limit(fake_redis):
    """Test that requests under the limit are allowed."""
    config = SonghiveConfig(
        auth={
            "rate_limit_enabled": True,
            "rate_limit_requests": 2,
            "rate_limit_window_seconds": 60,
        }
    )
    request = _request()

    await check_rate_limit(request, config, fake_redis)
    await check_rate_limit(request, config, fake_redis)


@pytest.mark.asyncio
async def test_check_rate_limit_rejects_over_limit(fake_redis):
    """Test that the request over the limit is rejected with 429."""
    config = SonghiveConfig(
        auth={
            "rate_limit_enabled": True,
            "rate_limit_requests": 2,
            "rate_limit_window_seconds": 60,
        }
    )
    request = _request()

    await check_rate_limit(request, config, fake_redis)
    await check_rate_limit(request, config, fake_redis)

    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(request, config, fake_redis)

    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert "Rate limit exceeded" in exc_info.value.detail


@pytest.mark.asyncio
async def test_check_rate_limit_disabled_ignores_limit(fake_redis):
    """Test that rate limiting is skipped when disabled in config."""
    config = SonghiveConfig(
        auth={
            "rate_limit_enabled": False,
            "rate_limit_requests": 1,
            "rate_limit_window_seconds": 60,
        }
    )
    request = _request()

    await check_rate_limit(request, config, fake_redis)
    await check_rate_limit(request, config, fake_redis)
    await check_rate_limit(request, config, fake_redis)


@pytest.mark.asyncio
async def test_check_rate_limit_uses_identifier(fake_redis):
    """Test that an identifier produces a separate rate limit bucket."""
    config = SonghiveConfig(
        auth={
            "rate_limit_enabled": True,
            "rate_limit_requests": 1,
            "rate_limit_window_seconds": 60,
        }
    )
    request = _request()

    await check_rate_limit(request, config, fake_redis, identifier="alice")
    # A different identifier should still be allowed.
    await check_rate_limit(request, config, fake_redis, identifier="bob")

    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(request, config, fake_redis, identifier="alice")

    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


class _FakeTime:
    """A stand-in for the ``time`` module with a controllable ``.time()`` value."""

    def __init__(self, t: float):
        self.t = t

    def time(self) -> float:
        return self.t


@pytest.mark.asyncio
async def test_check_rate_limit_includes_retry_after_header(fake_redis, monkeypatch):
    """Test that a 429 response includes a positive Retry-After header."""
    config = SonghiveConfig(
        auth={
            "rate_limit_enabled": True,
            "rate_limit_requests": 2,
            "rate_limit_window_seconds": 60,
        }
    )
    request = _request()

    fake_time = _FakeTime(1000.0)
    monkeypatch.setattr(rate_limit_module, "time", fake_time)

    await check_rate_limit(request, config, fake_redis)
    await check_rate_limit(request, config, fake_redis)

    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(request, config, fake_redis)

    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert "Retry-After" in exc_info.value.headers
    retry_after = int(exc_info.value.headers["Retry-After"])
    assert 1 <= retry_after <= config.auth.rate_limit_window_seconds


@pytest.mark.asyncio
async def test_check_rate_limit_sliding_window(fake_redis, monkeypatch):
    """Test that requests older than the window do not count against the limit."""
    config = SonghiveConfig(
        auth={
            "rate_limit_enabled": True,
            "rate_limit_requests": 2,
            "rate_limit_window_seconds": 60,
        }
    )
    request = _request()

    fake_time = _FakeTime(1000.0)
    monkeypatch.setattr(rate_limit_module, "time", fake_time)

    await check_rate_limit(request, config, fake_redis)
    await check_rate_limit(request, config, fake_redis)

    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(request, config, fake_redis)

    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    # Advance past the window; old entries should be evicted.
    fake_time.t += config.auth.rate_limit_window_seconds
    await check_rate_limit(request, config, fake_redis)
    await check_rate_limit(request, config, fake_redis)


@pytest.mark.asyncio
async def test_rate_limit_user_or_ip_uses_different_scopes(fake_redis, monkeypatch):
    """Test that different scopes maintain independent sliding windows."""
    config = SonghiveConfig(
        auth={
            "rate_limit_enabled": True,
            "rate_limit_requests": 1,
            "rate_limit_window_seconds": 60,
        }
    )
    request = _request()

    await _check_rate_limit(request, config, fake_redis, scope="user-a")
    await _check_rate_limit(request, config, fake_redis, scope="user-b")

    with pytest.raises(HTTPException) as exc_info:
        await _check_rate_limit(request, config, fake_redis, scope="user-a")

    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_check_rate_limit_fails_open_on_redis_error(caplog):
    """Test that Redis errors fail open and log a warning."""
    config = SonghiveConfig(
        auth={
            "rate_limit_enabled": True,
            "rate_limit_requests": 1,
            "rate_limit_window_seconds": 60,
        }
    )
    request = _request()
    broken_pipeline = MagicMock()
    broken_pipeline.execute = AsyncMock(side_effect=ConnectionError("redis down"))
    broken_redis = MagicMock()
    broken_redis.pipeline.return_value = broken_pipeline

    with caplog.at_level(logging.WARNING, logger="songhive.api.middleware.rate_limit"):
        await check_rate_limit(request, config, broken_redis)

    assert "Rate limiting unavailable" in caplog.text


@pytest.mark.asyncio
async def test_register_endpoint_rate_limited(client):
    """Test that repeated registration requests are blocked with 429."""
    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for i in range(2):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": f"rate-user-{i}",
                "email": f"rate-user-{i}@example.com",
                "password": "secret",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED

    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "rate-user-blocked",
            "email": "rate-user-blocked@example.com",
            "password": "secret",
        },
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert "Rate limit exceeded" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_endpoint_rate_limited(client):
    """Test that repeated login requests are blocked with 429."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice@example.com",
            "password": "secret",
        },
    )
    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "secret"},
        )
        assert response.status_code == status.HTTP_200_OK

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "secret"},
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_refresh_endpoint_rate_limited(client):
    """Test that repeated refresh requests are blocked with 429."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "refresh-alice",
            "email": "refresh-alice@example.com",
            "password": "secret",
        },
    )
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "refresh-alice", "password": "secret"},
    )
    refresh_token = login_response.json()["refresh_token"]

    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        if response.status_code == status.HTTP_200_OK:
            refresh_token = response.json()["refresh_token"]

    # The next request should be rate limited. By this point the original token
    # may have been rotated out, so an invalid token is acceptable for the 429
    # check; the limiter runs before the route handler.
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_rate_limit_disabled_allows_requests(client):
    """Test that disabling rate limiting prevents 429 responses."""
    client.app.state.config.auth.rate_limit_enabled = False
    client.app.state.config.auth.rate_limit_requests = 1
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for i in range(3):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": f"no-limit-{i}",
                "email": f"no-limit-{i}@example.com",
                "password": "secret",
            },
        )
        assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.asyncio
async def test_verify_email_endpoint_rate_limited(client):
    """Test that repeated verification attempts are blocked with 429."""
    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/verify-email",
            json={"token": "some-token"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    response = client.post(
        "/api/v1/auth/verify-email",
        json={"token": "some-token"},
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_password_reset_request_endpoint_rate_limited(client):
    """Test that repeated password reset requests are blocked with 429."""
    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/password-reset/request",
            json={"username": "nobody@example.com"},
        )
        assert response.status_code == status.HTTP_200_OK

    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "nobody@example.com"},
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_password_reset_confirm_endpoint_rate_limited(client):
    """Test that repeated password reset confirmations are blocked with 429."""
    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "some-token", "new_password": "secret"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": "some-token", "new_password": "secret"},
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_logout_endpoint_rate_limited(client):
    """Test that repeated logout requests are blocked with 429."""
    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for _ in range(2):
        response = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "some-token"},
        )
        assert response.status_code == status.HTTP_200_OK

    response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": "some-token"},
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_login_endpoint_rate_limited_per_username(client):
    """Test that login throttling is keyed by username, not just by IP."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "per-user-alice",
            "email": "per-user-alice@example.com",
            "password": "secret",
        },
    )
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "per-user-bob",
            "email": "per-user-bob@example.com",
            "password": "secret",
        },
    )

    client.app.state.config.auth.rate_limit_requests = 1
    client.app.state.config.auth.rate_limit_window_seconds = 60

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "per-user-alice", "password": "secret"},
    )
    assert response.status_code == status.HTTP_200_OK

    blocked = client.post(
        "/api/v1/auth/login",
        json={"username": "per-user-alice", "password": "secret"},
    )
    assert blocked.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    # A different username should not be throttled by alice's bucket.
    other = client.post(
        "/api/v1/auth/login",
        json={"username": "per-user-bob", "password": "wrong-password"},
    )
    assert other.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_patch_me_endpoint_rate_limited(client, db_session):
    """Test that repeated PATCH /me requests are blocked with 429."""
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "patch-alice",
            "email": "patch-alice@example.com",
            "password": "secret",
        },
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "patch-alice", "password": "secret"},
    )
    token = login.json()["access_token"]

    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for _ in range(2):
        response = client.patch(
            "/api/v1/users/me",
            json={"display_name": "Alice"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == status.HTTP_200_OK

    response = client.patch(
        "/api/v1/users/me",
        json={"display_name": "Alice"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_admin_invite_creation_rate_limited(client, db_session):
    """Test that repeated admin invite creations are blocked with 429."""
    await create_user(db_session, "admin-rate", "admin-rate@example.com", "secret", role="admin")
    await db_session.flush()

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin-rate", "password": "secret"},
    )
    token = login.json()["access_token"]

    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60

    for _ in range(2):
        response = client.post(
            "/api/v1/admin/invites",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == status.HTTP_201_CREATED

    response = client.post(
        "/api/v1/admin/invites",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_password_reset_request_rate_limited_per_username(client):
    """Test that password reset throttling is keyed by username, not just by IP."""
    client.app.state.config.auth.rate_limit_requests = 1
    client.app.state.config.auth.rate_limit_window_seconds = 60

    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "reset-alice@example.com"},
    )
    assert response.status_code == status.HTTP_200_OK

    blocked = client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "reset-alice@example.com"},
    )
    assert blocked.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    # A different username/email from the same IP should not be throttled.
    other = client.post(
        "/api/v1/auth/password-reset/request",
        json={"username": "reset-bob@example.com"},
    )
    assert other.status_code == status.HTTP_200_OK


def _fake_metadata():
    """Return minimal audio metadata so test uploads do not need a real file."""
    return AudioMetadata(
        title="Rate Limit Song",
        artist="Rate Limit Artist",
        album="Rate Limit Album",
        mimetype="audio/mpeg",
    )


def _upload_audio(client, auth_headers, user, content):
    """Upload an audio file and return (file_id, track_id)."""
    response = client.post(
        "/api/v1/files/upload?visibility=private",
        files={"file": ("song.mp3", io.BytesIO(content), "audio/mpeg")},
        headers=auth_headers(user),
    )
    assert response.status_code == status.HTTP_200_OK
    track_id = response.headers.get("X-Track-Id")
    assert track_id is not None
    return response.json()["id"], track_id


def _configure_rate_limit(client):
    """Set a low per-user rate limit for delete endpoints."""
    client.app.state.config.auth.rate_limit_requests = 2
    client.app.state.config.auth.rate_limit_window_seconds = 60


@pytest.mark.asyncio
async def test_delete_track_rate_limited(client, regular_user, auth_headers, monkeypatch):
    """DELETE /tracks/{id} is rate-limited."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)
    _configure_rate_limit(client)

    _, track_id = _upload_audio(client, auth_headers, regular_user, b"track rate limit")

    for _ in range(2):
        response = client.delete(f"/api/v1/tracks/{track_id}", headers=headers)
        assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_404_NOT_FOUND)

    response = client.delete(f"/api/v1/tracks/{track_id}", headers=headers)
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_delete_album_rate_limited(client, regular_user, auth_headers, monkeypatch):
    """DELETE /albums/{id} is rate-limited."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)
    _configure_rate_limit(client)

    _, track_id = _upload_audio(client, auth_headers, regular_user, b"album rate limit")
    album_id = client.get(f"/api/v1/tracks/{track_id}", headers=headers).json()["album_id"]

    for _ in range(2):
        response = client.delete(f"/api/v1/albums/{album_id}", headers=headers)
        assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_404_NOT_FOUND)

    response = client.delete(f"/api/v1/albums/{album_id}", headers=headers)
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_delete_artist_rate_limited(client, admin_user, auth_headers, monkeypatch):
    """DELETE /artists/{id} is rate-limited for admins (artists have no owner)."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(admin_user)
    _configure_rate_limit(client)

    _, track_id = _upload_audio(client, auth_headers, admin_user, b"artist rate limit")
    artist_id = client.get(f"/api/v1/tracks/{track_id}", headers=headers).json()["artist_id"]

    for _ in range(2):
        response = client.delete(f"/api/v1/artists/{artist_id}?recursive=true", headers=headers)
        assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_404_NOT_FOUND)

    response = client.delete(f"/api/v1/artists/{artist_id}?recursive=true", headers=headers)
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_delete_playlist_rate_limited(client, regular_user, auth_headers, monkeypatch):
    """DELETE /playlists/{id} is rate-limited."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)
    _configure_rate_limit(client)

    _, track_id = _upload_audio(client, auth_headers, regular_user, b"playlist rate limit")
    playlist = client.post("/api/v1/playlists/", json={"name": "Rate Limit Playlist"}, headers=headers)
    playlist_id = playlist.json()["id"]
    client.post(f"/api/v1/playlists/{playlist_id}/tracks", json={"track_ids": [track_id]}, headers=headers)

    for _ in range(2):
        response = client.delete(f"/api/v1/playlists/{playlist_id}", headers=headers)
        assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_404_NOT_FOUND)

    response = client.delete(f"/api/v1/playlists/{playlist_id}", headers=headers)
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_delete_library_rate_limited(client, regular_user, auth_headers, monkeypatch):
    """DELETE /libraries/{id} is rate-limited."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)
    _configure_rate_limit(client)

    library = client.post("/api/v1/libraries/", json={"name": "Rate Limit Library"}, headers=headers)
    library_id = library.json()["id"]

    for _ in range(2):
        response = client.delete(f"/api/v1/libraries/{library_id}", headers=headers)
        assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_404_NOT_FOUND)

    response = client.delete(f"/api/v1/libraries/{library_id}", headers=headers)
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS


@pytest.mark.asyncio
async def test_delete_file_rate_limited(client, regular_user, auth_headers, monkeypatch):
    """DELETE /files/{id} is rate-limited."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)
    _configure_rate_limit(client)

    file_id, _ = _upload_audio(client, auth_headers, regular_user, b"file rate limit")

    for _ in range(2):
        response = client.delete(f"/api/v1/files/{file_id}", headers=headers)
        assert response.status_code in (status.HTTP_204_NO_CONTENT, status.HTTP_404_NOT_FOUND)

    response = client.delete(f"/api/v1/files/{file_id}", headers=headers)
    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
