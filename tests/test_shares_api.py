"""
Tests for the share-grant API endpoints.
"""

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


def test_create_share_grant(client, regular_user, other_user, auth_headers, private_file):
    """An owner can create a share grant for another user."""
    response = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["item_type"] == "file"
    assert data["item_id"] == private_file["id"]
    assert data["user_id"] == str(other_user.id)
    assert "id" in data
    assert "created_at" in data


def test_create_share_grant_non_owner_forbidden(client, other_user, auth_headers, private_file):
    """A non-owner cannot create a share grant for someone else's item."""
    response = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_create_share_grant_unauthenticated(client, private_file, other_user):
    """Creating a share grant requires authentication."""
    response = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
    )
    assert response.status_code == 401


def test_create_share_grant_invalid_item_type(client, regular_user, other_user, auth_headers, private_file):
    """Unknown item types are rejected with 422."""
    response = client.post(
        "/api/v1/shares",
        json={"item_type": "invalid", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_list_share_grants(client, regular_user, other_user, auth_headers, private_file):
    """An owner can list share grants for an item."""
    client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    )

    response = client.get(
        f"/api/v1/shares?item_type=file&item_id={private_file['id']}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["user_id"] == str(other_user.id)


def test_list_share_grants_non_owner_forbidden(client, other_user, auth_headers, private_file):
    """A non-owner cannot list grants for someone else's item."""
    response = client.get(
        f"/api/v1/shares?item_type=file&item_id={private_file['id']}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_list_share_grants_missing_item(client, regular_user, auth_headers):
    """Listing grants for a missing item returns 404."""
    response = client.get(
        "/api/v1/shares?item_type=file&item_id=00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_delete_share_grant(client, regular_user, other_user, auth_headers, private_file):
    """An owner can delete a share grant."""
    created = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    ).json()

    response = client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers(regular_user))
    assert response.status_code == 204

    list_response = client.get(
        f"/api/v1/shares?item_type=file&item_id={private_file['id']}",
        headers=auth_headers(regular_user),
    )
    assert list_response.json() == []


def test_delete_share_grant_non_owner_forbidden(client, regular_user, other_user, auth_headers, private_file):
    """A non-owner cannot enumerate or delete someone else's share grant."""
    created = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    ).json()

    response = client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers(other_user))
    assert response.status_code == 404


def test_delete_share_grant_missing(client, regular_user, auth_headers):
    """Deleting a missing share grant returns 404."""
    response = client.delete(
        "/api/v1/shares/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


def test_shared_user_can_access_private_file(client, regular_user, other_user, auth_headers, private_file):
    """A shared user can access a private file through a share grant."""
    client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    )

    response = client.get(f"/api/v1/files/{private_file['id']}", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert response.json()["id"] == private_file["id"]
    assert response.json()["owner_id"] is None


def test_admin_can_manage_shares(client, admin_user, other_user, auth_headers, private_file):
    """An admin can create and delete share grants on another user's item."""
    created = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(admin_user),
    )
    assert created.status_code == 201

    delete = client.delete(f"/api/v1/shares/{created.json()['id']}", headers=auth_headers(admin_user))
    assert delete.status_code == 204
