"""
Basic API tests.
"""

import pytest
from pydantic import ValidationError

from songhive.api.routes.admin import AdminUserResponse
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


def test_favorites_endpoint(client):
    """Test the favorites listing endpoint."""
    response = client.get("/api/v1/favorites/")
    assert response.status_code == 200
    assert response.json() == []


def test_history_endpoint(client):
    """Test the history listing endpoint."""
    response = client.get("/api/v1/history/")
    assert response.status_code == 200
    assert response.json() == []


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


def test_admin_user_response_role_enum():
    """Test that AdminUserResponse role is a UserRole enum and serializes to a string."""
    response = AdminUserResponse(
        id="user-1",
        username="alice",
        email="alice@example.com",
        is_active=True,
        role="admin",
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
            role="superuser",
        )
