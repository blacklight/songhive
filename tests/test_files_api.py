"""
Tests for the file storage API endpoints.
"""

import hashlib
import logging

import pytest

from songhive.models._enums import Visibility


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
    assert "storage_backend" not in data
    assert "storage_path" not in data
    assert data["url"] == f"/api/v1/files/{data['id']}/download"


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
    assert "storage_path" not in metadata
    assert "storage_backend" not in metadata
    assert metadata["url"] == f"/api/v1/files/{data['id']}/download"


def test_get_file_metadata_missing(files_client, regular_user, auth_headers):
    """Requesting metadata for a missing file returns 404."""
    response = files_client.get(
        "/api/v1/files/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_get_file_metadata_private_denied_anonymous(files_client, upload_txt):
    """Private file metadata is denied (403) for anonymous requesters."""
    data, _ = upload_txt
    response = files_client.get(f"/api/v1/files/{data['id']}")
    assert response.status_code == 403


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


def test_download_file_private_denied_anonymous(files_client, upload_txt):
    """Private file downloads are denied (403) for anonymous requesters."""
    data, _ = upload_txt
    response = files_client.get(f"/api/v1/files/{data['id']}/download")
    assert response.status_code == 403


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
        owner_id=str(regular_user.id),
        visibility=Visibility.LOCAL.value,
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
        owner_id=str(regular_user.id),
        visibility=Visibility.LOCAL.value,
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
    sha = hashlib.sha256(content).hexdigest()
    storage_path = f"files/{sha[:2]}/{sha[2:4]}/{sha}"
    backing_file = base_path / storage_path
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


def test_upload_sets_owner_and_visibility(files_client, regular_user, auth_headers):
    """Uploading with a visibility query parameter sets owner and visibility."""
    content = b"public content"
    headers = auth_headers(regular_user)

    response = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("public.txt", content, "text/plain")},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["owner_id"] == str(regular_user.id)
    assert data["visibility"] == "public"


def test_public_file_accessible_anonymously(files_client, regular_user, auth_headers):
    """Public files can be accessed and downloaded without authentication."""
    content = b"public data"
    headers = auth_headers(regular_user)

    upload = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("anon.txt", content, "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200
    data = upload.json()

    metadata = files_client.get(f"/api/v1/files/{data['id']}")
    assert metadata.status_code == 200
    assert metadata.json()["visibility"] == "public"
    assert metadata.json()["owner_id"] is None

    download = files_client.get(f"/api/v1/files/{data['id']}/download")
    assert download.status_code == 200
    assert download.content == content


def test_local_file_accessible_to_other_users(files_client, regular_user, other_user, auth_headers):
    """Local files are accessible to any authenticated user; owner_id is redacted."""
    content = b"local data"
    headers = auth_headers(regular_user)

    upload = files_client.post(
        "/api/v1/files/upload?visibility=local",
        files={"file": ("local.txt", content, "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200
    data = upload.json()

    other_headers = auth_headers(other_user)
    metadata = files_client.get(f"/api/v1/files/{data['id']}", headers=other_headers)
    assert metadata.status_code == 200
    assert metadata.json()["visibility"] == "local"
    assert metadata.json()["owner_id"] is None

    download = files_client.get(f"/api/v1/files/{data['id']}/download", headers=other_headers)
    assert download.status_code == 200
    assert download.content == content


def test_private_file_denied_for_other_user(files_client, regular_user, other_user, auth_headers):
    """Private files are denied (403) for other authenticated users."""
    content = b"private data"
    headers = auth_headers(regular_user)

    upload = files_client.post(
        "/api/v1/files/upload",
        files={"file": ("private.txt", content, "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200
    data = upload.json()

    other_headers = auth_headers(other_user)
    assert files_client.get(f"/api/v1/files/{data['id']}", headers=other_headers).status_code == 403
    assert files_client.get(f"/api/v1/files/{data['id']}/download", headers=other_headers).status_code == 403


def test_owner_id_redacted_for_non_owner(files_client, regular_user, other_user, auth_headers):
    """Non-owners see a null owner_id in file metadata for local files."""
    content = b"local data"
    headers = auth_headers(regular_user)

    upload = files_client.post(
        "/api/v1/files/upload?visibility=local",
        files={"file": ("redacted.txt", content, "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200
    data = upload.json()

    other_headers = auth_headers(other_user)
    metadata = files_client.get(f"/api/v1/files/{data['id']}", headers=other_headers)
    assert metadata.status_code == 200
    assert metadata.json()["owner_id"] is None


def test_upload_invalid_visibility_returns_422(files_client, regular_user, auth_headers):
    """Uploading with an unknown visibility value returns 422."""
    headers = auth_headers(regular_user)

    response = files_client.post(
        "/api/v1/files/upload?visibility=publick",
        files={"file": ("bad.txt", b"data", "text/plain")},
        headers=headers,
    )
    assert response.status_code == 422


def test_upload_duplicate_returns_canonical_row_and_header(files_client, regular_user, auth_headers):
    """Uploading duplicate content returns the canonical row with X-Duplicate: true."""
    content = b"duplicate content"
    headers = auth_headers(regular_user)

    first = files_client.post(
        "/api/v1/files/upload",
        files={"file": ("first.txt", content, "text/plain")},
        headers=headers,
    )
    assert first.status_code == 200
    first_data = first.json()
    assert first.headers.get("X-Duplicate") is None

    second = files_client.post(
        "/api/v1/files/upload",
        files={"file": ("second.txt", content, "text/plain")},
        headers=headers,
    )
    assert second.status_code == 200
    second_data = second.json()
    assert second.headers.get("X-Duplicate") == "true"
    assert second_data["id"] == first_data["id"]


async def test_derived_file_download_through_public_track(files_client, regular_user, db_session):
    """An anonymous user can download a private file through a public track."""
    from io import BytesIO

    from songhive.models._enums import Visibility
    from songhive.models.artist import Artist
    from songhive.models.track import Track
    from songhive.services.storage import StorageService
    from songhive.storage import get_storage

    config = files_client.app.state.config.storage
    storage = StorageService(get_storage(config), config)

    file_like = BytesIO(b"audio content")
    stored_file = await storage.store_file(
        db_session,
        file_like,
        content_type="audio/mpeg",
        owner_id=str(regular_user.id),
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(stored_file)
    await db_session.flush()

    artist = Artist(name="Derived Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Derived Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        audio_file_id=stored_file.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.commit()

    public_download = files_client.get(f"/api/v1/files/{stored_file.id}/download")
    assert public_download.status_code == 200
    assert public_download.content == b"audio content"

    track.visibility = Visibility.PRIVATE.value
    await db_session.commit()

    private_download = files_client.get(f"/api/v1/files/{stored_file.id}/download")
    assert private_download.status_code == 403
