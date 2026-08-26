"""Tests for the share-URL and public resolver API endpoints."""

from datetime import datetime, timedelta, timezone

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.track import Track


def _create_share_url(client, regular_user, auth_headers, item_type, item_id, **extra):
    """Helper to create a share URL for an item."""
    body = {"item_type": item_type, "item_id": item_id}
    body.update(extra)
    response = client.post(
        "/api/v1/share-urls",
        json=body,
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    return response.json()


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


@pytest.fixture
def audio_file(client, regular_user, auth_headers, tmp_path):
    """Upload a private audio file owned by ``regular_user``."""
    client.app.state.config.storage.local_path = tmp_path / "media"
    response = client.post(
        "/api/v1/files/upload",
        files={"file": ("song.mp3", b"fake audio content", "audio/mpeg")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture
async def private_track(db_session, regular_user, audio_file):
    """Create a private track backed by an audio file."""
    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Private Track",
        artist_id=artist.id,
        audio_file_id=audio_file["id"],
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.flush()
    return track


@pytest.fixture(autouse=True)
def disable_rate_limit(client):
    """Disable rate limiting for resolver and download tests."""
    client.app.state.config.auth.rate_limit_enabled = False


def test_create_share_url(client, regular_user, auth_headers, private_file):
    """Creating a share URL returns the raw token once and a public URL."""
    data = _create_share_url(client, regular_user, auth_headers, "file", private_file["id"])
    assert "id" in data
    assert "token" in data
    assert "url" in data
    assert data["url"] == f"http://testserver/api/v1/share/{data['token']}"


def test_create_share_url_with_instance_domain(client, regular_user, auth_headers, private_file):
    """When an instance_domain is configured, share URLs use that public host."""
    client.app.state.config.federation.instance_domain = "music.example.com"
    data = _create_share_url(client, regular_user, auth_headers, "file", private_file["id"])
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
    _create_share_url(client, regular_user, auth_headers, "file", private_file["id"])

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
    data = _create_share_url(client, regular_user, auth_headers, "file", private_file["id"])
    token = data["token"]

    with_token = client.get(f"/api/v1/files/{private_file['id']}?token={token}")
    assert with_token.status_code == 200
    assert with_token.json()["id"] == private_file["id"]

    without_token = client.get(f"/api/v1/files/{private_file['id']}")
    assert without_token.status_code == 403


def test_download_with_token(client, regular_user, auth_headers, private_file):
    """A share URL token grants anonymous download access to a private file."""
    data = _create_share_url(client, regular_user, auth_headers, "file", private_file["id"])
    token = data["token"]

    response = client.get(f"/api/v1/files/{private_file['id']}/download?token={token}")
    assert response.status_code == 200
    assert response.content == b"private content"


def test_revoke_share_url(client, regular_user, auth_headers, private_file):
    """Revoking a share URL prevents further access."""
    data = _create_share_url(client, regular_user, auth_headers, "file", private_file["id"])
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
    data = _create_share_url(client, regular_user, auth_headers, "file", private_file["id"], expires_at=past)
    token = data["token"]

    assert client.get(f"/api/v1/files/{private_file['id']}?token={token}").status_code == 403
    assert client.get(f"/api/v1/share/{token}", follow_redirects=False).status_code == 404


def test_public_resolver_redirects_for_json(client, regular_user, auth_headers, private_file):
    """The public resolver 302-redirects to the API endpoint for JSON clients."""
    data = _create_share_url(client, regular_user, auth_headers, "file", private_file["id"])
    token = data["token"]

    response = client.get(
        f"/api/v1/share/{token}",
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == f"/api/v1/files/{private_file['id']}"
    assert "share_token" in response.cookies
    assert response.cookies["share_token"] == token


def test_public_resolver_renders_html_for_file(client, regular_user, auth_headers, private_file):
    """The public resolver returns an HTML preview page for non-audio files."""
    data = _create_share_url(client, regular_user, auth_headers, "file", private_file["id"])
    token = data["token"]

    response = client.get(f"/api/v1/share/{token}", follow_redirects=False)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert private_file["original_filename"] in response.text
    assert f"/api/v1/files/{private_file['id']}/download" in response.text


def test_public_resolver_redirects_audio_file_to_download(client, regular_user, auth_headers, audio_file):
    """The public resolver redirects audio file shares to the direct download URL."""
    data = _create_share_url(client, regular_user, auth_headers, "file", audio_file["id"])
    token = data["token"]

    response = client.get(f"/api/v1/share/{token}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"] == (
        f"/api/v1/files/{audio_file['id']}/download?token={token}&disposition=attachment"
    )


def test_public_resolver_download_query_for_file(client, regular_user, auth_headers, private_file):
    """``?download=true`` on a non-audio file share redirects to the download endpoint."""
    data = _create_share_url(client, regular_user, auth_headers, "file", private_file["id"])
    token = data["token"]

    response = client.get(f"/api/v1/share/{token}?download=true", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"] == (
        f"/api/v1/files/{private_file['id']}/download?token={token}&disposition=attachment"
    )


def test_public_resolver_renders_html_for_track(client, regular_user, auth_headers, private_track):
    """The public resolver returns an HTML preview page for a shared track."""
    data = _create_share_url(client, regular_user, auth_headers, "track", str(private_track.id))
    token = data["token"]

    response = client.get(f"/api/v1/share/{token}", follow_redirects=False)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert private_track.title in response.text
    assert f"/api/v1/files/{private_track.audio_file_id}/download" in response.text


def test_public_resolver_download_query_for_track(client, regular_user, auth_headers, private_track):
    """``?download=true`` on a track share redirects to the audio file download."""
    data = _create_share_url(client, regular_user, auth_headers, "track", str(private_track.id))
    token = data["token"]

    response = client.get(f"/api/v1/share/{token}?download=true", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"] == (
        f"/api/v1/files/{private_track.audio_file_id}/download?token={token}&disposition=attachment"
    )


def test_public_resolver_invalid_token(client):
    """The public resolver returns 404 for an unknown token."""
    response = client.get("/api/v1/share/not-a-valid-token", follow_redirects=False)
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
