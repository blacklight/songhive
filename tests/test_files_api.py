"""
Tests for the file storage API endpoints.
"""

import hashlib
import io
import logging
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.services.metadata import AudioMetadata


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


def test_upload_audio_file_imports_as_track(files_client, regular_user, auth_headers, monkeypatch):
    """Uploading an audio file creates a track in the user's default Uploads library."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: AudioMetadata(
            title="Uploaded Song",
            artist="Uploaded Artist",
            album="Uploaded Album",
            mimetype="audio/mpeg",
        ),
    )
    headers = auth_headers(regular_user)

    response = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("song.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["content_type"] == "audio/mpeg"
    assert "X-Track-Id" in response.headers

    track_id = response.headers["X-Track-Id"]
    track_response = files_client.get(f"/api/v1/tracks/{track_id}", headers=headers)
    assert track_response.status_code == 200
    track = track_response.json()
    assert track["title"] == "Uploaded Song"
    assert track["audio_url"] == data["url"]

    libraries_response = files_client.get("/api/v1/libraries/", headers=headers)
    assert libraries_response.status_code == 200
    library_names = {lib["name"] for lib in libraries_response.json()}
    assert "Uploads" in library_names


async def test_upload_audio_file_creates_single_stored_file(
    files_client, regular_user, auth_headers, monkeypatch, db_session
):
    """Uploading an audio file creates exactly one StoredFile row.

    The audio-only hash should be used for deduplication, and the endpoint
    must not create a second full-file StoredFile alongside the track file.
    """
    content = b"fake audio content for audio hash test"
    full_hash = hashlib.sha256(content).hexdigest()
    audio_hash = "0" * 64

    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: AudioMetadata(
            title="Audio Hashed Song",
            artist="Audio Hashed Artist",
            album="Audio Hashed Album",
            mimetype="audio/mpeg",
        ),
    )
    monkeypatch.setattr(
        "songhive.services.import_.audio_hash",
        AsyncMock(return_value=audio_hash),
    )

    headers = auth_headers(regular_user)
    response = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("hashed.mp3", io.BytesIO(content), "audio/mpeg")},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["sha256"] == audio_hash
    assert data["sha256"] != full_hash
    assert "X-Track-Id" in response.headers

    track_response = files_client.get(
        f"/api/v1/tracks/{response.headers['X-Track-Id']}",
        headers=headers,
    )
    assert track_response.status_code == 200
    track = track_response.json()
    assert track["audio_url"] == data["url"]

    files_for_content = list(
        (await db_session.execute(select(StoredFile).where(StoredFile.size == len(content)))).scalars().all()
    )
    assert len(files_for_content) == 1
    assert str(files_for_content[0].id) == data["id"]


async def test_upload_audio_duplicate_uses_canonical_stored_file(
    files_client, regular_user, auth_headers, monkeypatch, db_session
):
    """Re-uploading the same audio returns the canonical StoredFile and track."""
    content = b"same audio for duplicate test"
    audio_hash = "0" * 64

    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: AudioMetadata(
            title="Same Song",
            artist="Same Artist",
            album="Same Album",
            mimetype="audio/mpeg",
        ),
    )
    monkeypatch.setattr(
        "songhive.services.import_.audio_hash",
        AsyncMock(return_value=audio_hash),
    )

    headers = auth_headers(regular_user)
    first = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("same.mp3", io.BytesIO(content), "audio/mpeg")},
        headers=headers,
    )
    assert first.status_code == 200
    first_data = first.json()
    first_track_id = first.headers["X-Track-Id"]

    second = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("same.mp3", io.BytesIO(content), "audio/mpeg")},
        headers=headers,
    )
    assert second.status_code == 200
    second_data = second.json()
    assert second.headers.get("X-Duplicate") == "true"
    assert second.headers["X-Track-Id"] == first_track_id
    assert second_data["id"] == first_data["id"]
    assert second_data["sha256"] == audio_hash

    files_for_content = list(
        (await db_session.execute(select(StoredFile).where(StoredFile.size == len(content)))).scalars().all()
    )
    assert len(files_for_content) == 1


def test_upload_audio_file_ignores_failed_import(files_client, regular_user, auth_headers, monkeypatch):
    """A storage-only fallback is returned if the audio import pipeline raises."""

    def _broken_import(*_, **__):
        raise RuntimeError("metadata extraction exploded")

    monkeypatch.setattr(
        "songhive.api.routes.files.import_audio_file",
        _broken_import,
    )

    headers = auth_headers(regular_user)

    response = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("noise.mp3", io.BytesIO(b"not an mp3"), "audio/mpeg")},
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert "X-Track-Id" not in response.headers
    assert data["content_type"] == "audio/mpeg"


def test_upload_audio_file_reuses_uploads_library(files_client, regular_user, auth_headers, monkeypatch):
    """Subsequent audio uploads reuse the existing Uploads library."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: AudioMetadata(
            title="Song Two",
            artist="Artist Two",
            album="Album Two",
            mimetype="audio/mpeg",
        ),
    )
    headers = auth_headers(regular_user)

    first = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("one.mp3", io.BytesIO(b"fake audio one"), "audio/mpeg")},
        headers=headers,
    )
    assert first.status_code == 200

    second = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("two.mp3", io.BytesIO(b"fake audio two"), "audio/mpeg")},
        headers=headers,
    )
    assert second.status_code == 200

    libraries_response = files_client.get("/api/v1/libraries/", headers=headers)
    assert libraries_response.status_code == 200
    uploads = [lib for lib in libraries_response.json() if lib["name"] == "Uploads"]
    assert len(uploads) == 1


def test_upload_audio_file_to_selected_library(files_client, regular_user, auth_headers, monkeypatch):
    """Uploading an audio file with library_id imports it into that library."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: AudioMetadata(
            title="Selected Library Song",
            artist="Selected Artist",
            album="Selected Album",
            mimetype="audio/mpeg",
        ),
    )
    headers = auth_headers(regular_user)

    library = files_client.post(
        "/api/v1/libraries/",
        json={"name": "My Upload Library"},
        headers=headers,
    )
    assert library.status_code == 201
    library_id = library.json()["id"]

    response = files_client.post(
        f"/api/v1/files/upload?visibility=public&library_id={library_id}",
        files={"file": ("song.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        headers=headers,
    )
    assert response.status_code == 200
    track_id = response.headers["X-Track-Id"]

    tracks_response = files_client.get(f"/api/v1/libraries/{library_id}/tracks", headers=headers)
    assert tracks_response.status_code == 200
    track_ids = {track["id"] for track in tracks_response.json()}
    assert track_id in track_ids

    # The default Uploads library should not be created.
    libraries_response = files_client.get("/api/v1/libraries/", headers=headers)
    assert libraries_response.status_code == 200
    library_names = {lib["name"] for lib in libraries_response.json()}
    assert "Uploads" not in library_names


def test_upload_audio_file_to_missing_library_returns_404(files_client, regular_user, auth_headers):
    """Uploading to a non-existent library returns 404 before storing the file."""
    headers = auth_headers(regular_user)

    response = files_client.post(
        "/api/v1/files/upload?library_id=00000000-0000-0000-0000-000000000000",
        files={"file": ("song.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        headers=headers,
    )
    assert response.status_code == 404


def test_upload_audio_file_to_unauthorized_library_returns_403(
    files_client, regular_user, other_user, auth_headers, monkeypatch
):
    """Uploading to a library the user cannot manage returns 403."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: AudioMetadata(
            title="Unauthorized Song",
            artist="Unauthorized Artist",
            album="Unauthorized Album",
            mimetype="audio/mpeg",
        ),
    )
    other_headers = auth_headers(other_user)

    other_library = files_client.post(
        "/api/v1/libraries/",
        json={"name": "Other Library"},
        headers=other_headers,
    )
    assert other_library.status_code == 201
    library_id = other_library.json()["id"]

    headers = auth_headers(regular_user)
    response = files_client.post(
        f"/api/v1/files/upload?library_id={library_id}",
        files={"file": ("song.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        headers=headers,
    )
    assert response.status_code == 403


def test_list_files(files_client, regular_user, auth_headers, upload_txt):
    """Listing files returns files visible to the requester with pagination totals."""
    data, _ = upload_txt
    headers = auth_headers(regular_user)

    response = files_client.get("/api/v1/files/", headers=headers)
    assert response.status_code == 200
    files = response.json()
    assert len(files) == 1
    assert files[0]["id"] == data["id"]
    assert files[0]["url"] == f"/api/v1/files/{data['id']}/download"
    assert "X-Total-Count" in response.headers
    assert response.headers["X-Total-Count"] == "1"


def test_list_files_private_denied_for_other_user(files_client, regular_user, other_user, auth_headers, upload_txt):
    """Private files are not included in another user's file list."""
    other_headers = auth_headers(other_user)

    response = files_client.get("/api/v1/files/", headers=other_headers)
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


def test_list_files_public_visible_to_anonymous(files_client, regular_user, auth_headers):
    """Public files appear in unauthenticated file lists."""
    content = b"public list data"
    headers = auth_headers(regular_user)

    upload = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("public.txt", content, "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200

    response = files_client.get("/api/v1/files/")
    assert response.status_code == 200
    files = response.json()
    assert len(files) == 1
    assert files[0]["visibility"] == "public"
    assert files[0]["owner_id"] is None


def test_list_files_local_visible_to_authenticated_users(files_client, regular_user, other_user, auth_headers):
    """Local files appear for any authenticated user but hide the owner."""
    content = b"local list data"
    headers = auth_headers(regular_user)

    upload = files_client.post(
        "/api/v1/files/upload?visibility=local",
        files={"file": ("local.txt", content, "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200

    other_headers = auth_headers(other_user)
    response = files_client.get("/api/v1/files/", headers=other_headers)
    assert response.status_code == 200
    files = response.json()
    assert len(files) == 1
    assert files[0]["visibility"] == "local"
    assert files[0]["owner_id"] is None


def test_list_files_search_by_original_filename(files_client, regular_user, auth_headers):
    """The q parameter filters the file list by original filename."""
    headers = auth_headers(regular_user)

    files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("apple.txt", b"a", "text/plain")},
        headers=headers,
    )
    files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("banana.txt", b"b", "text/plain")},
        headers=headers,
    )

    response = files_client.get("/api/v1/files/?q=apple", headers=headers)
    assert response.status_code == 200
    files = response.json()
    assert len(files) == 1
    assert files[0]["original_filename"] == "apple.txt"


def test_list_files_pagination(files_client, regular_user, auth_headers):
    """Limit and offset query parameters paginate the file list."""
    headers = auth_headers(regular_user)

    for i in range(3):
        files_client.post(
            "/api/v1/files/upload?visibility=public",
            files={"file": (f"file{i}.txt", f"data{i}".encode(), "text/plain")},
            headers=headers,
        )

    first = files_client.get("/api/v1/files/?limit=2&offset=0", headers=headers)
    assert first.status_code == 200
    assert len(first.json()) == 2
    assert first.headers["X-Total-Count"] == "3"

    second = files_client.get("/api/v1/files/?limit=2&offset=2", headers=headers)
    assert second.status_code == 200
    assert len(second.json()) == 1


def test_get_file_metadata_includes_uploaded_track(files_client, regular_user, auth_headers, monkeypatch):
    """File metadata includes the track created from an uploaded audio file."""
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _: AudioMetadata(
            title="File Preview Song",
            artist="File Preview Artist",
            album="File Preview Album",
            mimetype="audio/mpeg",
        ),
    )
    headers = auth_headers(regular_user)

    response = files_client.post(
        "/api/v1/files/upload?visibility=public",
        files={"file": ("preview.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    track_id = response.headers["X-Track-Id"]

    metadata = files_client.get(f"/api/v1/files/{data['id']}", headers=headers)
    assert metadata.status_code == 200
    tracks = metadata.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["id"] == track_id
    assert tracks[0]["title"] == "File Preview Song"


async def test_get_file_metadata_includes_track_image_file(files_client, regular_user, auth_headers, db_session):
    """File metadata for an image includes tracks that use it as track art."""
    from io import BytesIO

    from songhive.services.storage import StorageService
    from songhive.storage import get_storage

    config = files_client.app.state.config.storage
    storage = StorageService(get_storage(config), config)

    image = await storage.store_file(
        db_session,
        BytesIO(b"image content"),
        content_type="image/png",
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(image)
    await db_session.flush()

    artist = Artist(name="Cover Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Cover Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        image_file_id=image.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.commit()

    headers = auth_headers(regular_user)
    metadata = files_client.get(f"/api/v1/files/{image.id}", headers=headers)
    assert metadata.status_code == 200
    tracks = metadata.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["id"] == str(track.id)
    assert tracks[0]["title"] == "Cover Track"


async def test_get_file_metadata_includes_album_cover_tracks(files_client, regular_user, auth_headers, db_session):
    """File metadata for an album cover includes tracks from that album."""
    from io import BytesIO

    from songhive.services.storage import StorageService
    from songhive.storage import get_storage

    config = files_client.app.state.config.storage
    storage = StorageService(get_storage(config), config)

    cover = await storage.store_file(
        db_session,
        BytesIO(b"cover content"),
        content_type="image/png",
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(cover)
    await db_session.flush()

    artist = Artist(name="Album Artist")
    db_session.add(artist)
    await db_session.flush()

    album = Album(
        title="Covered Album",
        artist_id=artist.id,
        cover_file_id=cover.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(album)
    await db_session.flush()

    track = Track(
        title="Album Track",
        artist_id=artist.id,
        album_id=album.id,
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.commit()

    headers = auth_headers(regular_user)
    metadata = files_client.get(f"/api/v1/files/{cover.id}", headers=headers)
    assert metadata.status_code == 200
    tracks = metadata.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["id"] == str(track.id)


async def test_get_file_metadata_tracks_filtered_by_visibility(
    files_client, regular_user, other_user, auth_headers, db_session
):
    """Associated tracks in file metadata are filtered by the requester's ACL."""
    from io import BytesIO

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
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(stored_file)
    await db_session.flush()

    artist = Artist(name="ACL Artist")
    db_session.add(artist)
    await db_session.flush()

    public_track = Track(
        title="Public Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        audio_file_id=stored_file.id,
        visibility=Visibility.PUBLIC.value,
    )
    private_track = Track(
        title="Private Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        audio_file_id=stored_file.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(public_track)
    db_session.add(private_track)
    await db_session.commit()

    public_metadata = files_client.get(f"/api/v1/files/{stored_file.id}")
    assert public_metadata.status_code == 200
    tracks = public_metadata.json()["tracks"]
    assert len(tracks) == 1
    assert tracks[0]["title"] == "Public Track"

    owner_headers = auth_headers(regular_user)
    owner_metadata = files_client.get(f"/api/v1/files/{stored_file.id}", headers=owner_headers)
    assert owner_metadata.status_code == 200
    owner_tracks = owner_metadata.json()["tracks"]
    assert len(owner_tracks) == 2
    assert {t["title"] for t in owner_tracks} == {"Public Track", "Private Track"}
