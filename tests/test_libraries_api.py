"""
Tests for the library API endpoints.
"""

import io

import pytest

from songhive.models._enums import Visibility
from songhive.models.track import Track
from songhive.services.metadata import AudioMetadata


@pytest.fixture
def sample_libraries(client, regular_user, auth_headers):
    """Create a public, local, and private library owned by ``regular_user``."""
    headers = auth_headers(regular_user)
    libraries = []
    for name, visibility in [
        ("Public Library", Visibility.PUBLIC),
        ("Local Library", Visibility.LOCAL),
        ("Private Library", Visibility.PRIVATE),
    ]:
        response = client.post(
            "/api/v1/libraries/",
            params={"visibility": visibility.value},
            json={"name": name},
            headers=headers,
        )
        assert response.status_code == 201
        libraries.append(response.json())
    return libraries


def _names(response):
    """Return the set of library names in a list response."""
    return {library["name"] for library in response.json()}


def test_list_libraries_filters_by_visibility(client, sample_libraries, regular_user, other_user, auth_headers):
    """List endpoints only return libraries the requester may access."""
    assert _names(client.get("/api/v1/libraries")) == {"Public Library"}

    other = client.get("/api/v1/libraries", headers=auth_headers(other_user))
    assert _names(other) == {"Public Library", "Local Library"}

    owner = client.get("/api/v1/libraries", headers=auth_headers(regular_user))
    assert _names(owner) == {"Public Library", "Local Library", "Private Library"}


def test_get_public_library_redacts_owner_for_non_owner(client, sample_libraries, other_user, auth_headers):
    """Non-owners see a null owner_id for public libraries."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")

    response = client.get(f"/api/v1/libraries/{library['id']}", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] is None
    assert response.json()["visibility"] == "public"


def test_get_private_library_denied_for_other_user(client, sample_libraries, other_user, auth_headers):
    """Private libraries are denied (403) for other authenticated users."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")

    response = client.get(f"/api/v1/libraries/{library['id']}", headers=auth_headers(other_user))
    assert response.status_code == 403


def test_get_library_as_owner_sees_owner_id(client, sample_libraries, regular_user, auth_headers):
    """The owner sees their own owner_id on a library."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")

    response = client.get(f"/api/v1/libraries/{library['id']}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] == str(regular_user.id)


def test_list_libraries_includes_can_write_flag(client, sample_libraries, regular_user, other_user, auth_headers):
    """Owners see can_write as true; non-owners see false for public libraries."""
    owner_response = client.get("/api/v1/libraries", headers=auth_headers(regular_user))
    assert owner_response.status_code == 200
    owner_data = owner_response.json()
    for library in owner_data:
        assert library["can_write"] is True

    other_response = client.get("/api/v1/libraries", headers=auth_headers(other_user))
    assert other_response.status_code == 200
    public_library = next(lib for lib in other_response.json() if lib["visibility"] == "public")
    assert public_library["can_write"] is False


def test_get_library_includes_can_write_flag(client, sample_libraries, regular_user, other_user, auth_headers):
    """The owner sees can_write as true; a non-owner sees it as false for public libraries."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")

    owner_response = client.get(f"/api/v1/libraries/{library['id']}", headers=auth_headers(regular_user))
    assert owner_response.status_code == 200
    assert owner_response.json()["can_write"] is True

    other_response = client.get(f"/api/v1/libraries/{library['id']}", headers=auth_headers(other_user))
    assert other_response.status_code == 200
    assert other_response.json()["can_write"] is False


def test_create_library_sets_owner_and_visibility(client, regular_user, auth_headers):
    """Creating a library sets owner and visibility from the query parameter."""
    response = client.post(
        "/api/v1/libraries/?visibility=public",
        json={"name": "My Library"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["owner_id"] == str(regular_user.id)
    assert data["visibility"] == "public"


def test_create_library_invalid_visibility_returns_422(client, regular_user, auth_headers):
    """Creating a library with an unknown visibility value returns 422."""
    response = client.post(
        "/api/v1/libraries/?visibility=publick",
        json={"name": "Bad Library"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_get_missing_library_returns_404(client):
    """Requesting a missing library returns 404."""
    response = client.get("/api/v1/libraries/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_private_library_with_share_token(client, sample_libraries, regular_user, auth_headers):
    """A share URL token grants anonymous access to a private library."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")

    create = client.post(
        "/api/v1/share-urls",
        json={"item_type": "library", "item_id": library["id"]},
        headers=auth_headers(regular_user),
    )
    assert create.status_code == 201
    token = create.json()["token"]

    response = client.get(f"/api/v1/libraries/{library['id']}?token={token}")
    assert response.status_code == 200
    assert response.json()["id"] == library["id"]

    no_token = client.get(f"/api/v1/libraries/{library['id']}")
    assert no_token.status_code == 403


def _fake_metadata():
    return AudioMetadata(
        title="Uploaded Song",
        artist="Uploaded Artist",
        album="Uploaded Album",
        mimetype="audio/mpeg",
    )


def test_upload_track_to_library(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """Owners can upload an audio file into a library."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())

    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    headers = auth_headers(regular_user)

    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks",
        files={"file": ("song.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        headers=headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["track"]["title"] == "Uploaded Song"
    assert data["track"]["artist_id"]
    assert "upload_id" in data


def test_bulk_upload_tracks_sync(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """Small bulk uploads are processed synchronously."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())

    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    headers = auth_headers(regular_user)

    files = [
        ("files", ("one.mp3", io.BytesIO(b"fake audio one"), "audio/mpeg")),
        ("files", ("two.mp3", io.BytesIO(b"fake audio two"), "audio/mpeg")),
    ]
    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks/bulk?force=true",
        files=files,
        headers=headers,
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 2
    assert all(r["status"] == "created" for r in results)


def test_scan_library_enqueues_task(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """Directory scan enqueues a Celery task when scan roots are configured."""
    monkeypatch.setattr(
        "songhive.tasks.import_.scan_directory.delay",
        lambda *args, **kwargs: type("Result", (), {"id": "task-123"})(),
    )

    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    headers = auth_headers(regular_user)
    client.app.state.config.imports.scan_roots = ["/music"]

    response = client.post(
        f"/api/v1/libraries/{library['id']}/scan",
        json={"path": "/music"},
        headers=headers,
    )
    assert response.status_code == 202
    assert response.json()["task_id"] == "task-123"


def test_update_library(client, sample_libraries, regular_user, auth_headers):
    """Owners can update a library's metadata."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")
    headers = auth_headers(regular_user)

    response = client.patch(
        f"/api/v1/libraries/{library['id']}",
        json={"name": "Updated Library", "visibility": "public"},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Library"
    assert data["visibility"] == "public"


def test_delete_library(client, sample_libraries, regular_user, auth_headers):
    """Owners can delete a library."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")
    headers = auth_headers(regular_user)

    response = client.delete(f"/api/v1/libraries/{library['id']}", headers=headers)
    assert response.status_code == 204

    get_response = client.get(f"/api/v1/libraries/{library['id']}", headers=headers)
    assert get_response.status_code == 404


def test_delete_library_missing(client, regular_user, auth_headers):
    """Deleting a missing library returns 404."""
    response = client.delete(
        "/api/v1/libraries/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_delete_library_unauthorized(client, sample_libraries, other_user, auth_headers):
    """A non-owner cannot delete a library."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")
    response = client.delete(
        f"/api/v1/libraries/{library['id']}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_update_library_description(client, sample_libraries, regular_user, auth_headers):
    """Patching a library can update the description only."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")
    headers = auth_headers(regular_user)

    response = client.patch(
        f"/api/v1/libraries/{library['id']}",
        json={"description": "New description"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["description"] == "New description"


def test_update_library_missing(client, regular_user, auth_headers):
    """Patching a missing library returns 404."""
    response = client.patch(
        "/api/v1/libraries/00000000-0000-0000-0000-000000000000",
        json={"name": "X"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_update_library_unauthorized(client, sample_libraries, other_user, auth_headers):
    """A non-owner cannot update a library."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")
    response = client.patch(
        f"/api/v1/libraries/{library['id']}",
        json={"name": "Hacked"},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_upload_track_missing_library(client, regular_user, auth_headers, monkeypatch):
    """Uploading to a missing library returns 404."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())

    response = client.post(
        "/api/v1/libraries/00000000-0000-0000-0000-000000000000/tracks",
        files={"file": ("song.mp3", io.BytesIO(b"fake"), "audio/mpeg")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_upload_track_anonymous_forbidden(client, sample_libraries, monkeypatch):
    """Anonymous users cannot upload tracks."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")

    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks",
        files={"file": ("song.mp3", io.BytesIO(b"fake"), "audio/mpeg")},
    )
    assert response.status_code == 403


def test_upload_track_other_user_forbidden(client, sample_libraries, other_user, auth_headers, monkeypatch):
    """A non-owner cannot upload tracks to a public library."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")

    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks",
        files={"file": ("song.mp3", io.BytesIO(b"fake"), "audio/mpeg")},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


async def test_upload_track_public_triggers_federation_publish(
    client, sample_libraries, regular_user, auth_headers, monkeypatch, db_session
):
    """Uploading a public track sets a federation object id."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")

    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks?visibility=public",
        files={"file": ("song.mp3", io.BytesIO(b"public audio"), "audio/mpeg")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    track_id = response.json()["track"]["id"]

    track = await db_session.get(Track, track_id)
    assert track.federation_object_id is not None


def test_upload_track_duplicate_returns_409(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """Re-uploading the same file to the same library returns 409."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    headers = auth_headers(regular_user)
    content = b"duplicate audio"

    first = client.post(
        f"/api/v1/libraries/{library['id']}/tracks",
        files={"file": ("song.mp3", io.BytesIO(content), "audio/mpeg")},
        headers=headers,
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/libraries/{library['id']}/tracks",
        files={"file": ("song.mp3", io.BytesIO(content), "audio/mpeg")},
        headers=headers,
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "duplicate"
    assert second.json()["existing_track_id"] == first.json()["track"]["id"]
    assert second.json()["track"]["id"] == first.json()["track"]["id"]


def test_bulk_upload_tracks_sync_duplicate(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """A duplicate in a sync bulk upload is reported per-file."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    headers = auth_headers(regular_user)
    content = b"bulk duplicate audio"

    files = [
        ("files", ("one.mp3", io.BytesIO(content), "audio/mpeg")),
        ("files", ("two.mp3", io.BytesIO(content), "audio/mpeg")),
    ]
    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks/bulk",
        files=files,
        headers=headers,
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 2
    assert results[0]["status"] == "created"
    assert results[1]["status"] == "duplicate"
    assert results[1]["existing_track_id"] == results[0]["track_id"]


def test_bulk_upload_tracks_sync_generic_error(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """A generic import error is surfaced as a per-file error."""

    def _boom(*args, **kwargs):
        raise RuntimeError("import exploded")

    monkeypatch.setattr("songhive.api.routes.libraries.import_audio_file", _boom)
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    headers = auth_headers(regular_user)

    files = [
        ("files", ("one.mp3", io.BytesIO(b"a"), "audio/mpeg")),
        ("files", ("two.mp3", io.BytesIO(b"b"), "audio/mpeg")),
    ]
    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks/bulk",
        files=files,
        headers=headers,
    )
    assert response.status_code == 200
    results = response.json()
    assert all(r["status"] == "error" for r in results)
    assert any("import exploded" in r["error"] for r in results)


def test_bulk_upload_tracks_sync_public_federation(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """A public sync bulk upload sets federation object ids."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    headers = auth_headers(regular_user)

    files = [
        ("files", ("one.mp3", io.BytesIO(b"batch public one"), "audio/mpeg")),
    ]
    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks/bulk?visibility=public",
        files=files,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()[0]["status"] == "created"


def test_bulk_upload_tracks_missing(client, regular_user, auth_headers):
    """Bulk uploading to a missing library returns 404."""
    response = client.post(
        "/api/v1/libraries/00000000-0000-0000-0000-000000000000/tracks/bulk",
        files=[("files", ("song.mp3", io.BytesIO(b"x"), "audio/mpeg"))],
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_bulk_upload_tracks_unauthorized(client, sample_libraries):
    """Anonymous users cannot bulk upload tracks."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks/bulk",
        files=[("files", ("song.mp3", io.BytesIO(b"x"), "audio/mpeg"))],
    )
    assert response.status_code == 403


def test_bulk_upload_tracks_async_batch(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """A batch above the sync threshold is queued for background processing."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    client.app.state.config.imports.bulk_import_sync_threshold = 1
    headers = auth_headers(regular_user)

    monkeypatch.setattr(
        "songhive.tasks.import_.process_upload.delay",
        lambda *args, **kwargs: type("Result", (), {"id": "task-123"})(),
    )

    files = [
        ("files", ("one.mp3", io.BytesIO(b"batch one"), "audio/mpeg")),
        ("files", ("two.mp3", io.BytesIO(b"batch two"), "audio/mpeg")),
    ]
    response = client.post(
        f"/api/v1/libraries/{library['id']}/tracks/bulk",
        files=files,
        headers=headers,
    )
    assert response.status_code == 202
    assert response.json()["enqueued"] == 2


def test_scan_library_missing(client, regular_user, auth_headers):
    """Scanning a missing library returns 404."""
    response = client.post(
        "/api/v1/libraries/00000000-0000-0000-0000-000000000000/scan",
        json={"path": "/music"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_scan_library_unauthorized(client, sample_libraries):
    """Anonymous users cannot scan a library."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    response = client.post(
        f"/api/v1/libraries/{library['id']}/scan",
        json={"path": "/music"},
    )
    assert response.status_code == 403


def test_scan_library_no_roots(client, sample_libraries, regular_user, auth_headers):
    """Scanning without configured scan roots returns 503."""
    client.app.state.config.imports.scan_roots = []
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")

    response = client.post(
        f"/api/v1/libraries/{library['id']}/scan",
        json={"path": "/music"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 503


def test_scan_library_path_outside_roots(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """Scanning a path outside configured roots returns 400."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    client.app.state.config.imports.scan_roots = ["/music"]
    monkeypatch.setattr("songhive.tasks.import_.scan_directory.delay", lambda *a, **k: None)

    response = client.post(
        f"/api/v1/libraries/{library['id']}/scan",
        json={"path": "/other"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 400


def test_list_library_tracks(client, sample_libraries, regular_user, auth_headers, monkeypatch):
    """Owners can list a library's tracks with pagination."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    headers = auth_headers(regular_user)

    upload = client.post(
        f"/api/v1/libraries/{library['id']}/tracks",
        files={"file": ("song.mp3", io.BytesIO(b"list audio"), "audio/mpeg")},
        headers=headers,
    )
    assert upload.status_code == 201

    response = client.get(
        f"/api/v1/libraries/{library['id']}/tracks?limit=1&offset=0",
        headers=headers,
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.headers["x-total-count"] == "1"


def test_list_library_tracks_missing(client, regular_user, auth_headers):
    """Listing tracks for a missing library returns 404."""
    response = client.get(
        "/api/v1/libraries/00000000-0000-0000-0000-000000000000/tracks",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_list_library_tracks_unauthorized(client, sample_libraries, other_user, auth_headers):
    """A non-owner cannot list a private library's tracks."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")
    response = client.get(
        f"/api/v1/libraries/{library['id']}/tracks",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_update_library_metadata(client, sample_libraries, regular_user, auth_headers):
    """Owners can update a library's name, description, and visibility."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "private")
    response = client.patch(
        f"/api/v1/libraries/{library['id']}",
        json={"name": "New Name", "description": "New description"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New Name"
    assert body["description"] == "New description"
    assert "image_url" in body
    assert "cover_url" in body


def test_upload_and_delete_library_images(client, sample_libraries, regular_user, auth_headers):
    """Owners can upload and remove library image and cover."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    headers = auth_headers(regular_user)

    image = client.post(
        f"/api/v1/libraries/{library['id']}/image",
        files={"file": ("image.jpg", io.BytesIO(b"fake image"), "image/jpeg")},
        headers=headers,
    )
    assert image.status_code == 200
    assert image.json()["image_url"] is not None

    cover = client.post(
        f"/api/v1/libraries/{library['id']}/cover",
        files={"file": ("cover.jpg", io.BytesIO(b"fake cover"), "image/jpeg")},
        headers=headers,
    )
    assert cover.status_code == 200
    assert cover.json()["cover_url"] is not None

    delete = client.delete(f"/api/v1/libraries/{library['id']}/image", headers=headers)
    assert delete.status_code == 200
    assert delete.json()["image_url"] is None

    delete_cover = client.delete(f"/api/v1/libraries/{library['id']}/cover", headers=headers)
    assert delete_cover.status_code == 200
    assert delete_cover.json()["cover_url"] is None


def test_library_image_isolation(client, regular_user, auth_headers):
    """Uploading an image to one library must not set it on another."""
    headers = auth_headers(regular_user)

    lib_a = client.post(
        "/api/v1/libraries/",
        params={"visibility": "private"},
        json={"name": "Library A"},
        headers=headers,
    )
    assert lib_a.status_code == 201
    lib_a_id = lib_a.json()["id"]

    lib_b = client.post(
        "/api/v1/libraries/",
        params={"visibility": "private"},
        json={"name": "Library B"},
        headers=headers,
    )
    assert lib_b.status_code == 201
    lib_b_id = lib_b.json()["id"]

    image = client.post(
        f"/api/v1/libraries/{lib_a_id}/image",
        files={"file": ("image.jpg", io.BytesIO(b"fake image"), "image/jpeg")},
        headers=headers,
    )
    assert image.status_code == 200
    lib_a_image = image.json()["image_url"]
    assert lib_a_image is not None

    detail_a = client.get(f"/api/v1/libraries/{lib_a_id}", headers=headers)
    assert detail_a.status_code == 200
    assert detail_a.json()["image_url"] == lib_a_image

    detail_b = client.get(f"/api/v1/libraries/{lib_b_id}", headers=headers)
    assert detail_b.status_code == 200
    assert detail_b.json()["image_url"] is None

    listed = client.get("/api/v1/libraries/", headers=headers)
    assert listed.status_code == 200
    by_id = {lib["id"]: lib for lib in listed.json()}
    assert by_id[lib_a_id]["image_url"] == lib_a_image
    assert by_id[lib_b_id]["image_url"] is None


def test_library_image_size_limit(client, sample_libraries, regular_user, auth_headers):
    """Oversized image uploads are rejected."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    big = b"x" * (10 * 1024 * 1024 + 1)
    response = client.post(
        f"/api/v1/libraries/{library['id']}/image",
        files={"file": ("big.jpg", io.BytesIO(big), "image/jpeg")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 413


def test_library_image_unsupported_type(client, sample_libraries, regular_user, auth_headers):
    """Non-image uploads are rejected."""
    library = next(lib for lib in sample_libraries if lib["visibility"] == "public")
    response = client.post(
        f"/api/v1/libraries/{library['id']}/image",
        files={"file": ("audio.mp3", io.BytesIO(b"audio"), "audio/mpeg")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 415
