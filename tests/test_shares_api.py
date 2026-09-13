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


@pytest.fixture
def private_library(client, regular_user, auth_headers):
    """Create a private library owned by ``regular_user``."""
    response = client.post(
        "/api/v1/libraries/?visibility=private",
        json={"name": "Private Library"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def private_playlist(client, regular_user, auth_headers):
    """Create a private playlist owned by ``regular_user``."""
    response = client.post(
        "/api/v1/playlists/?visibility=private",
        json={"name": "Private Playlist"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
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
    assert data.get("username") == other_user.username
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
    assert data[0].get("username") == other_user.username


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


def test_create_share_grant_by_username(client, regular_user, other_user, auth_headers, private_file):
    """A share grant accepts a username and stores the resolved user id."""
    response = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": other_user.username},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["user_id"] == str(other_user.id)
    assert data["username"] == other_user.username


def test_create_share_grant_unknown_user(client, regular_user, auth_headers, private_file):
    """A share grant for an unknown or inactive user returns 422."""
    response = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": "no-such-user"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_shared_user_can_access_private_library(client, regular_user, other_user, auth_headers, private_library):
    """A shared user can see and access a private library."""
    client.post(
        "/api/v1/shares",
        json={"item_type": "library", "item_id": private_library["id"], "user_id": other_user.username},
        headers=auth_headers(regular_user),
    )

    list_response = client.get("/api/v1/libraries", headers=auth_headers(other_user))
    assert list_response.status_code == 200
    assert any(lib["id"] == private_library["id"] for lib in list_response.json())

    get_response = client.get(f"/api/v1/libraries/{private_library['id']}", headers=auth_headers(other_user))
    assert get_response.status_code == 200
    assert get_response.json()["id"] == private_library["id"]


def test_shared_user_can_access_private_playlist(client, regular_user, other_user, auth_headers, private_playlist):
    """A shared user can see and access a private playlist."""
    client.post(
        "/api/v1/shares",
        json={"item_type": "playlist", "item_id": private_playlist["id"], "user_id": other_user.username},
        headers=auth_headers(regular_user),
    )

    list_response = client.get("/api/v1/playlists", headers=auth_headers(other_user))
    assert list_response.status_code == 200
    assert any(p["id"] == private_playlist["id"] for p in list_response.json())

    get_response = client.get(f"/api/v1/playlists/{private_playlist['id']}", headers=auth_headers(other_user))
    assert get_response.status_code == 200
    assert get_response.json()["id"] == private_playlist["id"]


def test_list_created_shares_unauthenticated(client):
    """Listing created shares requires authentication."""
    response = client.get("/api/v1/shares/mine")
    assert response.status_code == 401


def test_list_created_shares_empty(client, regular_user, auth_headers):
    """A user with no shares gets an empty list."""
    response = client.get("/api/v1/shares/mine", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


def test_list_created_shares_returns_grants_and_tokens(client, regular_user, other_user, auth_headers, private_file):
    """The endpoint returns both share grants and share URL tokens with metadata."""
    client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    )
    client.post(
        "/api/v1/share-urls",
        json={"item_type": "file", "item_id": private_file["id"]},
        headers=auth_headers(regular_user),
    )

    response = client.get("/api/v1/shares/mine", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    data = response.json()
    assert len(data) == 2

    by_kind = {entry["kind"]: entry for entry in data}
    grant = by_kind["grant"]
    assert grant["item_type"] == "file"
    assert grant["item_id"] == private_file["id"]
    assert grant["item_title"] == "private.txt"
    assert grant["item_url"] == f"/files/{private_file['id']}"
    assert grant["user_id"] == str(other_user.id)
    assert grant["username"] == other_user.username
    assert grant["created_at"]
    assert grant["expires_at"] is None
    assert grant["revoked_at"] is None

    token = by_kind["url"]
    assert token["item_type"] == "file"
    assert token["item_id"] == private_file["id"]
    assert token["item_title"] == "private.txt"
    assert token["user_id"] is None
    assert token["username"] is None
    assert token["expires_at"] is None
    assert token["revoked_at"] is None


def test_list_created_shares_scoped_to_creator(client, regular_user, other_user, auth_headers, private_file):
    """Each user only sees the shares they created themselves."""
    client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    )

    other_library = client.post(
        "/api/v1/libraries/?visibility=private",
        json={"name": "Other Library"},
        headers=auth_headers(other_user),
    ).json()
    client.post(
        "/api/v1/shares",
        json={"item_type": "library", "item_id": other_library["id"], "user_id": str(regular_user.id)},
        headers=auth_headers(other_user),
    )

    mine = client.get("/api/v1/shares/mine", headers=auth_headers(regular_user)).json()
    assert len(mine) == 1
    assert mine[0]["item_type"] == "file"
    assert mine[0]["item_id"] == private_file["id"]

    theirs = client.get("/api/v1/shares/mine", headers=auth_headers(other_user)).json()
    assert len(theirs) == 1
    assert theirs[0]["item_type"] == "library"
    assert theirs[0]["item_id"] == other_library["id"]
    assert theirs[0]["item_title"] == "Other Library"


def test_list_created_shares_hides_revoked_tokens_by_default(client, regular_user, auth_headers, private_file):
    """Revoked share URL tokens are hidden unless include_revoked is set."""
    active = client.post(
        "/api/v1/share-urls",
        json={"item_type": "file", "item_id": private_file["id"]},
        headers=auth_headers(regular_user),
    ).json()
    revoked = client.post(
        "/api/v1/share-urls",
        json={"item_type": "file", "item_id": private_file["id"]},
        headers=auth_headers(regular_user),
    ).json()
    client.delete(f"/api/v1/share-urls/{revoked['id']}", headers=auth_headers(regular_user))

    mine = client.get("/api/v1/shares/mine", headers=auth_headers(regular_user)).json()
    assert [entry["id"] for entry in mine] == [active["id"]]

    mine_all = client.get("/api/v1/shares/mine?include_revoked=true", headers=auth_headers(regular_user)).json()
    assert len(mine_all) == 2
    by_id = {entry["id"]: entry for entry in mine_all}
    assert by_id[revoked["id"]]["revoked_at"] is not None
    assert by_id[active["id"]]["revoked_at"] is None


@pytest.mark.asyncio
async def test_list_created_shares_missing_item(
    client, db_session, regular_user, other_user, auth_headers, private_library
):
    """Shares pointing at deleted items are listed without a title or URL."""
    from songhive.models.library import Library

    client.post(
        "/api/v1/shares",
        json={"item_type": "library", "item_id": private_library["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    )
    # Delete the row directly: the API-level delete cascades share rows, so a
    # dangling grant can only arise when the cleanup did not run.
    library = await db_session.get(Library, private_library["id"])
    await db_session.delete(library)
    await db_session.flush()

    mine = client.get("/api/v1/shares/mine", headers=auth_headers(regular_user)).json()
    assert len(mine) == 1
    assert mine[0]["item_id"] == private_library["id"]
    assert mine[0]["item_title"] is None
    assert mine[0]["item_url"] is None


def test_list_created_shares_pagination(client, regular_user, other_user, auth_headers, private_file):
    """Limit and offset paginate the merged grant/token list."""
    for _ in range(2):
        client.post(
            "/api/v1/share-urls",
            json={"item_type": "file", "item_id": private_file["id"]},
            headers=auth_headers(regular_user),
        )
    client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    )

    page = client.get("/api/v1/shares/mine?limit=2", headers=auth_headers(regular_user))
    assert page.status_code == 200
    assert page.headers["X-Total-Count"] == "3"
    assert len(page.json()) == 2

    rest = client.get("/api/v1/shares/mine?limit=2&offset=2", headers=auth_headers(regular_user)).json()
    assert len(rest) == 1


@pytest.mark.asyncio
async def test_creator_can_delete_grant_without_manage_rights(
    client, db_session, regular_user, other_user, admin_user, auth_headers, private_file
):
    """The user who created a grant can revoke it even after losing admin rights."""
    created = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(admin_user),
    ).json()

    admin_user.role = "user"
    await db_session.flush()

    response = client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers(admin_user))
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_creator_can_delete_share_url_without_manage_rights(
    client, db_session, regular_user, admin_user, auth_headers, private_file
):
    """The user who created a share token can revoke it even after losing admin rights."""
    created = client.post(
        "/api/v1/share-urls",
        json={"item_type": "file", "item_id": private_file["id"]},
        headers=auth_headers(admin_user),
    ).json()

    admin_user.role = "user"
    await db_session.flush()

    response = client.delete(f"/api/v1/share-urls/{created['id']}", headers=auth_headers(admin_user))
    assert response.status_code == 204

    mine = client.get("/api/v1/shares/mine?include_revoked=true", headers=auth_headers(admin_user)).json()
    assert mine[0]["revoked_at"] is not None


def test_non_creator_cannot_delete_grant(client, regular_user, other_user, auth_headers, private_file):
    """A third party who neither owns the item nor created the grant gets a 404."""
    created = client.post(
        "/api/v1/shares",
        json={"item_type": "file", "item_id": private_file["id"], "user_id": str(other_user.id)},
        headers=auth_headers(regular_user),
    ).json()

    response = client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers(other_user))
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_share_grant_creates_notification(
    client, db_session, regular_user, other_user, auth_headers, private_library
):
    """Granting a share to another user creates a share notification."""
    from sqlalchemy import select

    from songhive.models.notification import Notification

    response = client.post(
        "/api/v1/shares",
        json={
            "item_type": "library",
            "item_id": private_library["id"],
            "user_id": str(other_user.id),
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201

    result = await db_session.execute(
        select(Notification).where(
            Notification.user_id == other_user.id,
            Notification.type == "share",
        )
    )
    notification = result.scalar_one()
    assert notification.source_url == f"/libraries/{private_library['id']}"
    assert notification.payload["item_type"] == "library"
    assert notification.payload["item_title"] == "Private Library"


@pytest.mark.asyncio
async def test_share_grant_to_self_creates_no_notification(
    client, db_session, regular_user, auth_headers, private_library
):
    """Granting a share to yourself does not create a notification."""
    from sqlalchemy import select

    from songhive.models.notification import Notification

    response = client.post(
        "/api/v1/shares",
        json={
            "item_type": "library",
            "item_id": private_library["id"],
            "user_id": str(regular_user.id),
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201

    result = await db_session.execute(select(Notification).where(Notification.user_id == regular_user.id))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_duplicate_share_grant_creates_no_notification(
    client, db_session, regular_user, other_user, auth_headers, private_library
):
    """Re-granting an existing share does not create a second notification."""
    from sqlalchemy import select

    from songhive.models.notification import Notification

    payload = {
        "item_type": "library",
        "item_id": private_library["id"],
        "user_id": str(other_user.id),
    }
    headers = auth_headers(regular_user)
    client.post("/api/v1/shares", json=payload, headers=headers)
    client.post("/api/v1/shares", json=payload, headers=headers)

    result = await db_session.execute(select(Notification).where(Notification.user_id == other_user.id))
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_revoke_share_grant_removes_notification(
    client, db_session, regular_user, other_user, auth_headers, private_library
):
    """Revoking a share grant retracts the share notification it produced."""
    from sqlalchemy import select

    from songhive.models.notification import Notification

    created = client.post(
        "/api/v1/shares",
        json={
            "item_type": "library",
            "item_id": private_library["id"],
            "user_id": str(other_user.id),
        },
        headers=auth_headers(regular_user),
    ).json()

    result = await db_session.execute(select(Notification).where(Notification.user_id == other_user.id))
    assert [n.type for n in result.scalars().all()] == ["share"]

    response = client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers(regular_user))
    assert response.status_code == 204

    result = await db_session.execute(select(Notification).where(Notification.user_id == other_user.id))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_deleting_shared_item_removes_notification(
    client, db_session, regular_user, other_user, auth_headers, private_library
):
    """Deleting a shared item removes the share notifications pointing at it."""
    from sqlalchemy import select

    from songhive.models.notification import Notification

    client.post(
        "/api/v1/shares",
        json={
            "item_type": "library",
            "item_id": private_library["id"],
            "user_id": str(other_user.id),
        },
        headers=auth_headers(regular_user),
    )

    response = client.delete(
        f"/api/v1/libraries/{private_library['id']}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 204

    result = await db_session.execute(select(Notification).where(Notification.user_id == other_user.id))
    assert result.scalars().all() == []
