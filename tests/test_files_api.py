"""
Tests for the file storage API endpoints.
"""

import hashlib
import logging

import pytest


@pytest.fixture
def files_client(client, tmp_path):
    """Return a test client with a temp local storage path."""
    client.app.state.config.storage.local_path = tmp_path / "media"
    return client


@pytest.fixture
def upload_txt(files_client, regular_user, auth_headers):
    """Upload a small text file and return its API response."""
    content = b"hello world"
    headers = auth_headers(regular_user)
    response = files_client.post(
        "/api/v1/files/upload",
        files={"file": ("test.txt", content, "text/plain")},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json(), content


def test_upload_file(files_client, regular_user, auth_headers):
    """Uploading a file returns stored metadata including a URL."""
    content = b"hello world"
    headers = auth_headers(regular_user)

    response = files_client.post(
        "/api/v1/files/upload",
        files={"file": ("test.txt", content, "text/plain")},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["content_type"] == "text/plain"
    assert data["size"] == len(content)
    assert data["sha256"] == hashlib.sha256(content).hexdigest()
    assert data["original_filename"] == "test.txt"
    assert data["storage_backend"] == "local"
    assert data["storage_path"] == (f"files/{data['sha256'][:2]}/{data['sha256'][2:4]}/{data['sha256']}")
    assert data["url"].endswith(data["storage_path"])
    assert data["url"].startswith(str(files_client.app.state.config.storage.local_path))


def test_upload_file_requires_auth(files_client):
    """Uploading a file without authentication returns 401."""
    response = files_client.post(
        "/api/v1/files/upload",
        files={"file": ("test.txt", b"data", "text/plain")},
    )
    assert response.status_code == 401


def test_get_file_metadata(files_client, regular_user, auth_headers, upload_txt):
    """Getting file metadata returns the stored record with its URL."""
    data, _ = upload_txt
    headers = auth_headers(regular_user)

    response = files_client.get(f"/api/v1/files/{data['id']}", headers=headers)
    assert response.status_code == 200

    metadata = response.json()
    assert metadata["id"] == data["id"]
    assert metadata["sha256"] == data["sha256"]
    assert metadata["storage_path"] == data["storage_path"]
    assert metadata["url"] == data["url"]


def test_get_file_metadata_missing(files_client, regular_user, auth_headers):
    """Requesting metadata for a missing file returns 404."""
    response = files_client.get(
        "/api/v1/files/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_get_file_metadata_requires_auth(files_client, upload_txt):
    """File metadata endpoints require authentication."""
    data, _ = upload_txt
    response = files_client.get(f"/api/v1/files/{data['id']}")
    assert response.status_code == 401


def test_download_file(files_client, regular_user, auth_headers, upload_txt):
    """Downloading a file returns its bytes and correct headers."""
    data, content = upload_txt
    headers = auth_headers(regular_user)

    response = files_client.get(
        f"/api/v1/files/{data['id']}/download",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["Content-Type"].startswith("text/plain")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in response.headers["Content-Disposition"]
    assert 'filename="test.txt"' in response.headers["Content-Disposition"]


def test_download_file_range(files_client, regular_user, auth_headers, upload_txt):
    """Range requests are honored with a 206 Partial Content response."""
    data, content = upload_txt
    headers = {**auth_headers(regular_user), "Range": "bytes=0-3"}

    response = files_client.get(
        f"/api/v1/files/{data['id']}/download",
        headers=headers,
    )

    assert response.status_code == 206
    assert response.content == content[:4]
    assert response.headers["Content-Type"].startswith("text/plain")
    assert "bytes" in response.headers.get("Accept-Ranges", "")


def test_download_file_missing(files_client, regular_user, auth_headers):
    """Downloading a missing file returns 404."""
    response = files_client.get(
        "/api/v1/files/00000000-0000-0000-0000-000000000000/download",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_download_file_requires_auth(files_client, upload_txt):
    """Download endpoints require authentication."""
    data, _ = upload_txt
    response = files_client.get(f"/api/v1/files/{data['id']}/download")
    assert response.status_code == 401


def test_download_safe_inline_disposition(files_client, regular_user, auth_headers):
    """Playable media is served inline by default and can be forced to attachment."""
    content = b"fake audio data"
    headers = auth_headers(regular_user)

    upload = files_client.post(
        "/api/v1/files/upload",
        files={"file": ("song.mp3", content, "audio/mpeg")},
        headers=headers,
    )
    assert upload.status_code == 200
    file_id = upload.json()["id"]

    inline = files_client.get(
        f"/api/v1/files/{file_id}/download",
        headers=headers,
    )
    assert inline.status_code == 200
    assert "inline" in inline.headers["Content-Disposition"]
    assert 'filename="song.mp3"' in inline.headers["Content-Disposition"]

    attachment = files_client.get(
        f"/api/v1/files/{file_id}/download?disposition=attachment",
        headers=headers,
    )
    assert attachment.status_code == 200
    assert "attachment" in attachment.headers["Content-Disposition"]


def test_download_untrusted_type_forces_attachment(files_client, regular_user, auth_headers):
    """Untrusted content types are always served as attachments."""
    content = b"<html>evil</html>"
    headers = auth_headers(regular_user)

    upload = files_client.post(
        "/api/v1/files/upload",
        files={"file": ("evil.html", content, "text/html")},
        headers=headers,
    )
    assert upload.status_code == 200
    file_id = upload.json()["id"]

    response = files_client.get(
        f"/api/v1/files/{file_id}/download?disposition=inline",
        headers=headers,
    )
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert "inline" not in response.headers["Content-Disposition"]


def test_download_sanitizes_filename(files_client, regular_user, auth_headers):
    """Newlines, path separators, and control characters are stripped from filenames."""
    content = b"data"
    headers = auth_headers(regular_user)
    bad_name = "/etc/passwd\r\nsafe.txt"

    upload = files_client.post(
        "/api/v1/files/upload",
        files={"file": (bad_name, content, "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200
    file_id = upload.json()["id"]

    response = files_client.get(
        f"/api/v1/files/{file_id}/download",
        headers=headers,
    )
    assert response.status_code == 200
    disposition = response.headers["Content-Disposition"]
    assert "safe.txt" in disposition
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "/etc/passwd" not in disposition


async def test_download_empty_or_whitespace_filename_uses_fallback(
    files_client, regular_user, auth_headers, db_session
):
    """StoredFile records with empty or whitespace-only original_filename fall back to the safe name."""
    from io import BytesIO

    from songhive.services.storage import StorageService
    from songhive.storage import get_storage

    config = files_client.app.state.config.storage
    storage = StorageService(get_storage(config), config)
    headers = auth_headers(regular_user)

    # None original_filename exercises the first fallback branch in _sanitize_filename.
    file_like = BytesIO(b"empty-name-content")
    stored_none = await storage.store_file(
        db_session,
        file_like,
        content_type="text/plain",
        original_filename=None,
    )
    db_session.add(stored_none)
    await db_session.commit()

    response_none = files_client.get(
        f"/api/v1/files/{stored_none.id}/download",
        headers=headers,
    )
    assert response_none.status_code == 200
    assert 'filename="file"' in response_none.headers["Content-Disposition"]

    # Whitespace-only original_filename exercises the post-sanitization fallback branch.
    file_like2 = BytesIO(b"whitespace-name-content")
    stored_ws = await storage.store_file(
        db_session,
        file_like2,
        content_type="text/plain",
        original_filename="   ",
    )
    db_session.add(stored_ws)
    await db_session.commit()

    response_ws = files_client.get(
        f"/api/v1/files/{stored_ws.id}/download",
        headers=headers,
    )
    assert response_ws.status_code == 200
    assert 'filename="file"' in response_ws.headers["Content-Disposition"]


def test_download_orphaned_record_returns_404(files_client, regular_user, auth_headers):
    """A StoredFile record with a missing backing file returns 404 on download."""
    content = b"orphaned content"
    headers = auth_headers(regular_user)

    upload = files_client.post(
        "/api/v1/files/upload",
        files={"file": ("orphan.txt", content, "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200
    data = upload.json()

    # Delete the backing file while leaving the database row intact.
    base_path = files_client.app.state.config.storage.local_path
    backing_file = base_path / data["storage_path"]
    backing_file.unlink()

    response = files_client.get(
        f"/api/v1/files/{data['id']}/download",
        headers=headers,
    )
    assert response.status_code == 404


def test_upload_logs_stored_file(files_client, regular_user, auth_headers, caplog):
    """Uploading a file logs the stored file id and size for observability."""
    content = b"hello log"
    headers = auth_headers(regular_user)

    with caplog.at_level(logging.INFO, logger="songhive.api.routes.files"):
        response = files_client.post(
            "/api/v1/files/upload",
            files={"file": ("log.txt", content, "text/plain")},
            headers=headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert f"Uploaded file {data['id']}" in caplog.text
    assert str(data["size"]) in caplog.text
