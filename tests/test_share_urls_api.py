"""
Tests for the share-URL and public resolver API endpoints.
"""

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def private_file(client, regular_user, auth_headers, tmp_path):
    """Upload a private file owned by ``regular_user``."""
    client.app.state.config.storage.local_path = tmp_path / "media"
    response = client.post(
        "/api/v1/files/upload",
        files={"file": ("private.txt", b"private content", "text/plain")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture(autouse=True)
def disable_rate_limit(client):
    """Disable rate limiting for resolver and download tests."""
    client.app.state.config.auth.rate_limit_enabled = False


def _create_share_url(client, regular_user, auth_headers, private_file, **extra):
    """Helper to create a share URL for the private file."""
    body = {"item_type": "file", "item_id": private_file["id"]}
    body.update(extra)
    response = client.post(
        "/api/v1/share-urls",
        json=body,
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    return response.json()


def test_create_share_url(client, regular_user, auth_headers, private_file):
    """Creating a share URL returns the raw token once and a public URL."""
    data = _create_share_url(client, regular_user, auth_headers, private_file)
    assert "id" in data
    assert "token" in data
    assert "url" in data
    assert data["url"] == f"http://testserver/api/v1/share/{data['token']}"


def test_create_share_url_with_instance_domain(client, regular_user, auth_headers, private_file):
    """When an instance_domain is configured, share URLs use that public host."""
    client.app.state.config.federation.instance_domain = "music.example.com"
    data = _create_share_url(client, regular_user, auth_headers, private_file)
    assert data["url"] == f"https://music.example.com/api/v1/share/{data['token']}"


def test_create_share_url_non_owner_forbidden(client, other_user, auth_headers, private_file):
    """A non-owner cannot create a share URL for someone else's item."""
    response = client.post(
        "/api/v1/share-urls",
        json={"item_type": "file", "item_id": private_file["id"]},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_list_share_urls_hides_token(client, regular_user, auth_headers, private_file):
    """Listing share URLs never returns the raw token."""
    _create_share_url(client, regular_user, auth_headers, private_file)

    response = client.get(
        f"/api/v1/share-urls?item_type=file&item_id={private_file['id']}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert "token" not in data[0]
    assert "id" in data[0]
    assert "expires_at" in data[0]
    assert "revoked_at" in data[0]
    assert "created_at" in data[0]


def test_anonymous_file_access_with_token(client, regular_user, auth_headers, private_file):
    """A share URL token grants anonymous access to a private file."""
    data = _create_share_url(client, regular_user, auth_headers, private_file)
    token = data["token"]

    with_token = client.get(f"/api/v1/files/{private_file['id']}?token={token}")
    assert with_token.status_code == 200
    assert with_token.json()["id"] == private_file["id"]

    without_token = client.get(f"/api/v1/files/{private_file['id']}")
    assert without_token.status_code == 403


def test_download_with_token(client, regular_user, auth_headers, private_file):
    """A share URL token grants anonymous download access to a private file."""
    data = _create_share_url(client, regular_user, auth_headers, private_file)
    token = data["token"]

    response = client.get(f"/api/v1/files/{private_file['id']}/download?token={token}")
    assert response.status_code == 200
    assert response.content == b"private content"


def test_revoke_share_url(client, regular_user, auth_headers, private_file):
    """Revoking a share URL prevents further access."""
    data = _create_share_url(client, regular_user, auth_headers, private_file)
    token_id = data["id"]
    token = data["token"]

    assert client.get(f"/api/v1/files/{private_file['id']}?token={token}").status_code == 200

    delete = client.delete(f"/api/v1/share-urls/{token_id}", headers=auth_headers(regular_user))
    assert delete.status_code == 204

    assert client.get(f"/api/v1/files/{private_file['id']}?token={token}").status_code == 403
    assert client.get(f"/api/v1/share/{token}", follow_redirects=False).status_code == 404


def test_expired_share_url(client, regular_user, auth_headers, private_file):
    """An expired share URL token does not grant access."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    data = _create_share_url(client, regular_user, auth_headers, private_file, expires_at=past)
    token = data["token"]

    assert client.get(f"/api/v1/files/{private_file['id']}?token={token}").status_code == 403
    assert client.get(f"/api/v1/share/{token}", follow_redirects=False).status_code == 404


def test_public_resolver_redirects(client, regular_user, auth_headers, private_file):
    """The public resolver 302-redirects to the item URL and sets a short-lived cookie."""
    data = _create_share_url(client, regular_user, auth_headers, private_file)
    token = data["token"]

    response = client.get(f"/api/v1/share/{token}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"] == f"/api/v1/files/{private_file['id']}"
    assert "share_token" in response.cookies
    assert response.cookies["share_token"] == token


def test_public_resolver_invalid_token(client):
    """The public resolver returns 404 for an unknown token."""
    response = client.get("/api/v1/share/not-a-valid-token", follow_redirects=False)
    assert response.status_code == 404
