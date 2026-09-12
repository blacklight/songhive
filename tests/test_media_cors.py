"""
Tests for permissive CORS headers on read-only media endpoints.

Federated clients embed audio by fetching media URLs cross-origin, so
file/track downloads answer preflights and carry
``Access-Control-Allow-Origin: *`` regardless of the configured
``server.cors_origins`` allowlist.
"""

import pytest


@pytest.fixture
def media_client(client, tmp_path):
    """Return a test client with a temp local storage path."""
    client.app.state.config.storage.local_path = tmp_path / "media"
    return client


@pytest.fixture
def public_file(media_client, regular_user, auth_headers):
    """Upload a public file and return its API response."""
    response = media_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("song.bin", b"public data", "application/octet-stream")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    return response.json()


def test_download_preflight_allows_any_origin(media_client):
    """Preflights on media endpoints get a wildcard response for any origin."""
    response = media_client.options(
        "/api/v1/files/some-file/download",
        headers={
            "Origin": "https://akkoma.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Range",
        },
    )

    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert "GET" in response.headers["Access-Control-Allow-Methods"]
    assert "Range" in response.headers["Access-Control-Allow-Headers"]
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_track_download_preflight_allows_any_origin(media_client):
    """The track download endpoint answers preflights the same way."""
    response = media_client.options(
        "/api/v1/tracks/some-track/download",
        headers={
            "Origin": "https://akkoma.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_download_response_has_wildcard_origin(media_client, public_file):
    """Cross-origin GETs on file downloads get a wildcard allow-origin."""
    response = media_client.get(
        f"/api/v1/files/{public_file['id']}/download",
        headers={"Origin": "https://akkoma.example"},
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert "Content-Range" in response.headers["Access-Control-Expose-Headers"]
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_download_denied_response_has_wildcard_origin(media_client, regular_user, auth_headers):
    """Even a 403 on a private file carries the wildcard origin."""
    upload = media_client.post(
        "/api/v1/files/upload",
        files={"file": ("private.bin", b"private", "application/octet-stream")},
        headers=auth_headers(regular_user),
    )
    assert upload.status_code == 200

    response = media_client.get(
        f"/api/v1/files/{upload.json()['id']}/download",
        headers={"Origin": "https://akkoma.example"},
    )

    assert response.status_code == 403
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_allowlisted_origin_keeps_credentialed_cors(media_client, public_file):
    """Configured origins still get the credentialed allowlist policy."""
    response = media_client.get(
        f"/api/v1/files/{public_file['id']}/download",
        headers={"Origin": "http://localhost:8080"},
    )

    assert response.status_code == 200
    assert response.headers.get_list("Access-Control-Allow-Origin") == ["http://localhost:8080"]
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_allowlisted_origin_preflight_uses_credentialed_cors(media_client, public_file):
    """Preflights from configured origins are still handled by CORSMiddleware."""
    response = media_client.options(
        f"/api/v1/files/{public_file['id']}/download",
        headers={
            "Origin": "http://localhost:8080",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Range",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:8080"
    assert response.headers["Access-Control-Allow-Credentials"] == "true"


def test_non_media_endpoint_has_no_wildcard(media_client, public_file):
    """The wildcard policy does not leak onto other API endpoints."""
    response = media_client.get(
        f"/api/v1/files/{public_file['id']}",
        headers={"Origin": "https://akkoma.example"},
    )

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers
