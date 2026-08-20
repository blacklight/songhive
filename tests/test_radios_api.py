"""
Tests for the radio API endpoints.
"""

import pytest

from songhive.models._enums import Visibility


@pytest.fixture
def sample_radios(client, regular_user, auth_headers):
    """Create a public, local, and private radio owned by ``regular_user``."""
    headers = auth_headers(regular_user)
    radios = []
    for name, visibility in [
        ("Public Radio", Visibility.PUBLIC),
        ("Local Radio", Visibility.LOCAL),
        ("Private Radio", Visibility.PRIVATE),
    ]:
        response = client.post(
            "/api/v1/radios/",
            params={"visibility": visibility.value},
            json={"name": name},
            headers=headers,
        )
        assert response.status_code == 201
        radios.append(response.json())
    return radios


def _names(response):
    """Return the set of radio names in a list response."""
    return {radio["name"] for radio in response.json()}


def test_list_radios_filters_by_visibility(client, sample_radios, regular_user, other_user, auth_headers):
    """List endpoints only return radios the requester may access."""
    assert _names(client.get("/api/v1/radios")) == {"Public Radio"}

    other = client.get("/api/v1/radios", headers=auth_headers(other_user))
    assert _names(other) == {"Public Radio", "Local Radio"}

    owner = client.get("/api/v1/radios", headers=auth_headers(regular_user))
    assert _names(owner) == {"Public Radio", "Local Radio", "Private Radio"}


def test_get_public_radio_redacts_owner_for_non_owner(client, sample_radios, other_user, auth_headers):
    """Non-owners see a null owner_id for public radios."""
    radio = next(r for r in sample_radios if r["visibility"] == "public")

    response = client.get(f"/api/v1/radios/{radio['id']}", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] is None
    assert response.json()["visibility"] == "public"


def test_get_private_radio_denied_for_other_user(client, sample_radios, other_user, auth_headers):
    """Private radios are denied (403) for other authenticated users."""
    radio = next(r for r in sample_radios if r["visibility"] == "private")

    response = client.get(f"/api/v1/radios/{radio['id']}", headers=auth_headers(other_user))
    assert response.status_code == 403


def test_get_radio_as_owner_sees_owner_id(client, sample_radios, regular_user, auth_headers):
    """The owner sees their own owner_id on a radio."""
    radio = next(r for r in sample_radios if r["visibility"] == "private")

    response = client.get(f"/api/v1/radios/{radio['id']}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] == str(regular_user.id)


def test_create_radio_sets_owner_and_visibility(client, regular_user, auth_headers):
    """Creating a radio sets owner and visibility from the query parameter."""
    response = client.post(
        "/api/v1/radios/?visibility=public",
        json={"name": "My Radio"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["owner_id"] == str(regular_user.id)
    assert data["visibility"] == "public"


def test_create_radio_invalid_visibility_returns_422(client, regular_user, auth_headers):
    """Creating a radio with an unknown visibility value returns 422."""
    response = client.post(
        "/api/v1/radios/?visibility=publick",
        json={"name": "Bad Radio"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_get_missing_radio_returns_404(client):
    """Requesting a missing radio returns 404."""
    response = client.get("/api/v1/radios/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
