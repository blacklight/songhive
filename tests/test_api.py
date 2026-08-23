"""
Basic API tests.
"""

import logging

import pytest
from fastapi import Request
from pydantic import ValidationError

from songhive.api._common import Pagination, client_ip, get_pagination
from songhive.api.app import create_app
from songhive.api.routes.admin import AdminUserResponse
from songhive.config.schema import SonghiveConfig
from songhive.models.user import UserRole


def test_app_creates(client):
    """Test that the FastAPI app can be created and responds."""
    response = client.get("/api/v1/artists/")
    assert response.status_code == 200
    assert response.json() == []


def test_tracks_endpoint(client):
    """Test the tracks listing endpoint."""
    response = client.get("/api/v1/tracks/")
    assert response.status_code == 200
    assert response.json() == []


def test_albums_endpoint(client):
    """Test the albums listing endpoint."""
    response = client.get("/api/v1/albums/")
    assert response.status_code == 200
    assert response.json() == []


def test_playlists_endpoint(client):
    """Test the playlists listing endpoint."""
    response = client.get("/api/v1/playlists/")
    assert response.status_code == 200
    assert response.json() == []


def test_favorites_endpoint(client, regular_user, auth_headers):
    """Test the favorites listing endpoint for an authenticated user."""
    response = client.get("/api/v1/favorites/", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json() == []


def test_history_endpoint_requires_auth(client):
    """GET /history without authentication returns 401."""
    response = client.get("/api/v1/history/")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_radios_endpoint(client):
    """Test the radios listing endpoint."""
    response = client.get("/api/v1/radios/")
    assert response.status_code == 200
    assert response.json() == []


def test_libraries_endpoint(client):
    """Test the libraries listing endpoint."""
    response = client.get("/api/v1/libraries/")
    assert response.status_code == 200
    assert response.json() == []


def test_cors_preflight(client):
    """Test that CORS preflight requests are handled using server.cors_origins."""
    response = client.options(
        "/api/v1/artists/",
        headers={
            "Origin": "http://localhost:8080",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert "http://localhost:8080" in response.headers.get("Access-Control-Allow-Origin", "")
    assert response.headers.get("Access-Control-Allow-Credentials") == "true"


def test_cors_wildcard_origin_disables_credentials(caplog, monkeypatch):
    """Test that a wildcard CORS origin logs a warning and disables credentials."""
    monkeypatch.delenv("SONGHIVE_AUTH__SECRET_KEY", raising=False)
    config = SonghiveConfig(
        server={"cors_origins": ["*"]},  # type: ignore
        auth={"secret_key": "a" * 32},  # type: ignore
        federation={"enabled": False},  # type: ignore
    )
    with caplog.at_level(logging.WARNING, logger="songhive.api.app"):
        create_app(config)
    assert "Wildcard CORS origin" in caplog.text


def test_admin_user_response_role_enum():
    """Test that AdminUserResponse role is a UserRole enum and serializes to a string."""
    response = AdminUserResponse(
        id="user-1",
        username="alice",
        email="alice@example.com",
        is_active=True,
        role="admin",  # type: ignore
    )
    assert response.role == UserRole.ADMIN
    assert response.model_dump()["role"] == "admin"


def test_admin_user_response_rejects_invalid_role():
    """Test that AdminUserResponse rejects an invalid role."""
    with pytest.raises(ValidationError):
        AdminUserResponse(
            id="user-1",
            username="alice",
            email="alice@example.com",
            is_active=True,
            role="superuser",  # type: ignore
        )


def _make_request(
    *,
    headers=None,
    query_string=b"",
    client_host=None,
):
    """Build a minimal FastAPI Request scope for client_ip tests."""
    headers = headers or {}
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "query_string": query_string,
    }
    if client_host:
        scope["client"] = (client_host, 12345)
    return Request(scope)


def test_client_ip_uses_x_forwarded_for():
    """client_ip returns the leftmost valid X-Forwarded-For address."""
    request = _make_request(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
    assert client_ip(request) == "1.2.3.4"


def test_client_ip_uses_x_forwarded_for_with_trusted_hops():
    """trusted_hops skips the rightmost proxy entries."""
    request = _make_request(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8, 9.10.11.12"})
    assert client_ip(request, trusted_hops=1) == "5.6.7.8"


def test_client_ip_x_forwarded_for_insufficient_hops_ignored():
    """If trusted_hops exceeds the chain length, X-Forwarded-For is ignored."""
    request = _make_request(headers={"X-Forwarded-For": "1.2.3.4"})
    assert client_ip(request, trusted_hops=5) is None


def test_client_ip_x_forwarded_for_zero_hops_ignored():
    """trusted_hops=0 disables X-Forwarded-For processing."""
    request = _make_request(headers={"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "2.3.4.5"})
    assert client_ip(request, trusted_hops=0) == "2.3.4.5"


def test_client_ip_uses_x_real_ip():
    """client_ip falls back to X-Real-IP."""
    request = _make_request(headers={"X-Real-IP": "2.3.4.5"})
    assert client_ip(request) == "2.3.4.5"


def test_client_ip_uses_forwarded_header():
    """client_ip parses the RFC 7239 Forwarded header."""
    request = _make_request(headers={"Forwarded": "for=3.4.5.6"})
    assert client_ip(request) == "3.4.5.6"


def test_client_ip_forwarded_ipv6_bracket():
    """client_ip strips brackets and ports from IPv6 Forwarded values."""
    request = _make_request(headers={"Forwarded": 'for="[2001:db8::1]:8080"'})
    assert client_ip(request) == "2001:db8::1"


def test_client_ip_forwarded_ipv4_port():
    """client_ip strips ports from IPv4 Forwarded values."""
    request = _make_request(headers={"Forwarded": "for=4.5.6.7:8080"})
    assert client_ip(request) == "4.5.6.7"


def test_client_ip_forwarded_hidden_ignored():
    """client_ip ignores the RFC 7239 obfuscated and underscore values."""
    request = _make_request(headers={"Forwarded": "for=_hidden, for=5.6.7.8"})
    assert client_ip(request) == "5.6.7.8"


def test_client_ip_uses_request_client():
    """client_ip falls back to request.client.host."""
    request = _make_request(client_host="127.0.0.1")
    assert client_ip(request) == "127.0.0.1"


def test_client_ip_returns_none_when_no_source():
    """client_ip returns None when no source is available."""
    request = _make_request()
    assert client_ip(request) is None


def test_client_ip_skips_invalid_addresses():
    """client_ip skips invalid addresses and logs them."""
    request = _make_request(headers={"X-Forwarded-For": "not-an-ip, 1.2.3.4"})
    assert client_ip(request) == "1.2.3.4"


def test_pagination_set_total():
    """Pagination.set_total writes the X-Total-Count response header."""
    from fastapi import Response

    response = Response()
    pagination = Pagination(limit=20, offset=0)
    pagination.set_total(response, 42)
    assert response.headers["X-Total-Count"] == "42"


@pytest.mark.asyncio
async def test_get_pagination():
    """get_pagination returns a Pagination instance from limit/offset values."""
    result = await get_pagination(limit=10, offset=5)
    assert result.limit == 10
    assert result.offset == 5
