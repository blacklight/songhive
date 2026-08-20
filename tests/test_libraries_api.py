"""
Tests for the library API endpoints.
"""

import pytest

from songhive.models._enums import Visibility


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
