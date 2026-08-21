"""
Tests for the library API endpoints.
"""

import io

import pytest

from songhive.models._enums import Visibility
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
