"""
Tests for collection collaborators: share-grant roles, editing gates,
self-leave, received-share listing, and per-entry attribution.
"""

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.track import Track


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
def collaborator_grant(client, regular_user, other_user, auth_headers, private_playlist):
    """Grant ``other_user`` collaborator access to ``private_playlist``."""
    response = client.post(
        "/api/v1/shares",
        json={
            "item_type": "playlist",
            "item_id": private_playlist["id"],
            "user_id": str(other_user.id),
            "collaborator": True,
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def viewer_grant(client, regular_user, other_user, auth_headers, private_playlist):
    """Grant ``other_user`` plain (read-only) access to ``private_playlist``."""
    response = client.post(
        "/api/v1/shares",
        json={
            "item_type": "playlist",
            "item_id": private_playlist["id"],
            "user_id": str(other_user.id),
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    return response.json()


async def _make_track(db_session, owner, visibility=Visibility.PUBLIC, title="Track One"):
    """Create a track row owned by ``owner``."""
    artist = Artist(name="Sample Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title=title,
        artist_id=artist.id,
        owner_id=str(owner.id),
        visibility=visibility.value,
    )
    db_session.add(track)
    await db_session.commit()
    return track


def test_create_collaborator_grant(client, collaborator_grant, other_user, private_playlist):
    """A collaborator grant on a playlist is stored with the flag set."""
    assert collaborator_grant["collaborator"] is True
    assert collaborator_grant["item_type"] == "playlist"
    assert collaborator_grant["user_id"] == str(other_user.id)


def test_collaborator_grant_rejected_for_file(client, regular_user, other_user, auth_headers, tmp_path):
    """Collaborator grants are only allowed on collaborative item types."""
    client.app.state.config.storage.local_path = tmp_path / "media"
    file_resp = client.post(
        "/api/v1/files/upload",
        files={"file": ("private.txt", b"private content", "text/plain")},
        headers=auth_headers(regular_user),
    )
    assert file_resp.status_code == 200

    response = client.post(
        "/api/v1/shares",
        json={
            "item_type": "file",
            "item_id": file_resp.json()["id"],
            "user_id": str(other_user.id),
            "collaborator": True,
        },
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_reshare_promotes_existing_grant(client, regular_user, other_user, auth_headers, private_playlist):
    """Re-sharing with collaborator=true updates the existing grant's flag."""
    first = client.post(
        "/api/v1/shares",
        json={
            "item_type": "playlist",
            "item_id": private_playlist["id"],
            "user_id": str(other_user.id),
        },
        headers=auth_headers(regular_user),
    )
    assert first.status_code == 201
    assert first.json()["collaborator"] is False

    second = client.post(
        "/api/v1/shares",
        json={
            "item_type": "playlist",
            "item_id": private_playlist["id"],
            "user_id": str(other_user.id),
            "collaborator": True,
        },
        headers=auth_headers(regular_user),
    )
    assert second.status_code == 201
    assert second.json()["collaborator"] is True

    grants = client.get(
        f"/api/v1/shares?item_type=playlist&item_id={private_playlist['id']}",
        headers=auth_headers(regular_user),
    ).json()
    assert len(grants) == 1
    assert grants[0]["collaborator"] is True


def test_patch_share_grant_toggles_collaborator(client, regular_user, auth_headers, viewer_grant):
    """PATCH promotes a viewer grant to collaborator and demotes it back."""
    grant_id = viewer_grant["id"]

    promote = client.patch(
        f"/api/v1/shares/{grant_id}",
        json={"collaborator": True},
        headers=auth_headers(regular_user),
    )
    assert promote.status_code == 200
    assert promote.json()["collaborator"] is True

    demote = client.patch(
        f"/api/v1/shares/{grant_id}",
        json={"collaborator": False},
        headers=auth_headers(regular_user),
    )
    assert demote.status_code == 200
    assert demote.json()["collaborator"] is False


def test_patch_share_grant_forbidden_for_grantee(client, other_user, auth_headers, viewer_grant):
    """The grantee cannot change their own grant's role (404 hides the id)."""
    response = client.patch(
        f"/api/v1/shares/{viewer_grant['id']}",
        json={"collaborator": True},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 404


def test_patch_share_grant_rejects_non_collection(client, regular_user, other_user, auth_headers, tmp_path):
    """PATCH cannot turn a file grant into a collaborator grant."""
    client.app.state.config.storage.local_path = tmp_path / "media"
    file_resp = client.post(
        "/api/v1/files/upload",
        files={"file": ("private.txt", b"private content", "text/plain")},
        headers=auth_headers(regular_user),
    )
    grant = client.post(
        "/api/v1/shares",
        json={
            "item_type": "file",
            "item_id": file_resp.json()["id"],
            "user_id": str(other_user.id),
        },
        headers=auth_headers(regular_user),
    ).json()

    response = client.patch(
        f"/api/v1/shares/{grant['id']}",
        json={"collaborator": True},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_list_received_shares(client, other_user, regular_user, auth_headers, collaborator_grant):
    """The grantee sees the share under /shares/received with role and sharer."""
    response = client.get("/api/v1/shares/received", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "1"
    data = response.json()
    assert data[0]["id"] == collaborator_grant["id"]
    assert data[0]["item_type"] == "playlist"
    assert data[0]["item_title"] == "Private Playlist"
    assert data[0]["collaborator"] is True
    assert data[0]["shared_by"]["username"] == regular_user.username

    # The creator's own list is empty — grants are received, not created.
    mine = client.get("/api/v1/shares/received", headers=auth_headers(regular_user))
    assert mine.json() == []


def test_list_received_shares_item_type_filter(
    client, regular_user, other_user, auth_headers, private_playlist, private_library
):
    """``item_type`` narrows the received list."""
    headers = auth_headers(regular_user)
    client.post(
        "/api/v1/shares",
        json={"item_type": "playlist", "item_id": private_playlist["id"], "user_id": str(other_user.id)},
        headers=headers,
    )
    client.post(
        "/api/v1/shares",
        json={"item_type": "library", "item_id": private_library["id"], "user_id": str(other_user.id)},
        headers=headers,
    )

    all_shares = client.get("/api/v1/shares/received", headers=auth_headers(other_user))
    assert len(all_shares.json()) == 2

    filtered = client.get(
        "/api/v1/shares/received?item_type=library",
        headers=auth_headers(other_user),
    )
    assert filtered.status_code == 200
    assert filtered.headers["X-Total-Count"] == "1"
    assert filtered.json()[0]["item_type"] == "library"


def test_list_received_shares_unauthenticated(client):
    """Received shares require authentication."""
    assert client.get("/api/v1/shares/received").status_code == 401


def test_grantee_can_leave_share(client, regular_user, other_user, auth_headers, private_playlist, collaborator_grant):
    """A grantee can DELETE their own grant, losing access to the item."""
    # Access works while the grant exists.
    get = client.get(f"/api/v1/playlists/{private_playlist['id']}", headers=auth_headers(other_user))
    assert get.status_code == 200

    response = client.delete(
        f"/api/v1/shares/{collaborator_grant['id']}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 204

    # Access is gone and the owner sees no remaining grants.
    get = client.get(f"/api/v1/playlists/{private_playlist['id']}", headers=auth_headers(other_user))
    assert get.status_code == 403
    grants = client.get(
        f"/api/v1/shares?item_type=playlist&item_id={private_playlist['id']}",
        headers=auth_headers(regular_user),
    )
    assert grants.json() == []


def test_collaborator_can_edit_playlist_metadata(
    client, other_user, auth_headers, private_playlist, collaborator_grant
):
    """A collaborator may edit playlist name/description."""
    response = client.patch(
        f"/api/v1/playlists/{private_playlist['id']}",
        json={"name": "Renamed", "description": "Edited by collaborator"},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["description"] == "Edited by collaborator"


def test_collaborator_cannot_change_playlist_visibility(
    client, other_user, auth_headers, private_playlist, collaborator_grant
):
    """Visibility changes stay owner/admin only — collaborators get 403."""
    response = client.patch(
        f"/api/v1/playlists/{private_playlist['id']}",
        json={"visibility": "public"},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403

    get = client.get(f"/api/v1/playlists/{private_playlist['id']}", headers=auth_headers(other_user))
    assert get.json()["visibility"] == "private"


def test_viewer_cannot_edit_playlist(client, other_user, auth_headers, private_playlist, viewer_grant):
    """A plain share grantee remains read-only."""
    response = client.patch(
        f"/api/v1/playlists/{private_playlist['id']}",
        json={"name": "Nope"},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_collaborator_cannot_delete_or_share_playlist(
    client, regular_user, other_user, admin_user, auth_headers, private_playlist, collaborator_grant
):
    """Delete and grant management stay behind can_manage."""
    delete = client.delete(
        f"/api/v1/playlists/{private_playlist['id']}",
        headers=auth_headers(other_user),
    )
    assert delete.status_code == 403

    share = client.post(
        "/api/v1/shares",
        json={
            "item_type": "playlist",
            "item_id": private_playlist["id"],
            "user_id": str(admin_user.id),
        },
        headers=auth_headers(other_user),
    )
    assert share.status_code == 403


@pytest.mark.asyncio
async def test_collaborator_adds_and_removes_playlist_tracks(
    client, db_session, regular_user, other_user, auth_headers, private_playlist, collaborator_grant
):
    """A collaborator can add tracks; each entry records who added it."""
    track = await _make_track(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{private_playlist['id']}/tracks",
        json={"track_ids": [str(track.id)]},
        headers=auth_headers(other_user),
    )
    assert add.status_code == 201

    items = client.get(
        f"/api/v1/playlists/{private_playlist['id']}/items",
        headers=auth_headers(other_user),
    )
    assert items.status_code == 200
    entry = items.json()[0]
    assert entry["added_by_id"] == str(other_user.id)
    assert entry["added_by"]["username"] == other_user.username

    remove = client.post(
        f"/api/v1/playlists/{private_playlist['id']}/tracks/remove",
        json={"track_ids": [str(track.id)]},
        headers=auth_headers(other_user),
    )
    assert remove.status_code == 200
    assert (
        client.get(
            f"/api/v1/playlists/{private_playlist['id']}/items",
            headers=auth_headers(other_user),
        ).json()
        == []
    )


@pytest.mark.asyncio
async def test_viewer_cannot_add_playlist_tracks(
    client, db_session, regular_user, other_user, auth_headers, private_playlist, viewer_grant
):
    """A plain grantee cannot add tracks."""
    track = await _make_track(db_session, regular_user)
    response = client.post(
        f"/api/v1/playlists/{private_playlist['id']}/tracks",
        json={"track_ids": [str(track.id)]},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_owner_added_entries_carry_owner_attribution(
    client, db_session, regular_user, auth_headers, private_playlist
):
    """Owner-added entries report the owner as the adder."""
    track = await _make_track(db_session, regular_user)
    add = client.post(
        f"/api/v1/playlists/{private_playlist['id']}/tracks",
        json={"track_ids": [str(track.id)]},
        headers=auth_headers(regular_user),
    )
    assert add.status_code == 201

    items = client.get(
        f"/api/v1/playlists/{private_playlist['id']}/items",
        headers=auth_headers(regular_user),
    ).json()
    assert items[0]["added_by_id"] == str(regular_user.id)
    assert items[0]["added_by"]["username"] == regular_user.username


def test_playlist_capability_flags(
    client, regular_user, other_user, auth_headers, private_playlist, collaborator_grant
):
    """Playlist responses carry can_write/can_manage/is_collaborator/share_grant_id."""
    collab = client.get(
        f"/api/v1/playlists/{private_playlist['id']}",
        headers=auth_headers(other_user),
    ).json()
    assert collab["can_write"] is True
    assert collab["can_manage"] is False
    assert collab["is_collaborator"] is True
    assert collab["share_grant_id"] == collaborator_grant["id"]

    owner = client.get(
        f"/api/v1/playlists/{private_playlist['id']}",
        headers=auth_headers(regular_user),
    ).json()
    assert owner["can_write"] is True
    assert owner["can_manage"] is True
    assert owner["is_collaborator"] is False
    assert owner["share_grant_id"] is None


def test_playlist_viewer_capability_flags(client, other_user, auth_headers, private_playlist, viewer_grant):
    """A plain grantee sees can_write=false but gets their grant id."""
    data = client.get(
        f"/api/v1/playlists/{private_playlist['id']}",
        headers=auth_headers(other_user),
    ).json()
    assert data["can_write"] is False
    assert data["can_manage"] is False
    assert data["is_collaborator"] is False
    assert data["share_grant_id"] == viewer_grant["id"]


def test_editable_filter_lists_collaborations(
    client, other_user, auth_headers, private_playlist, private_library, collaborator_grant
):
    """``editable=true`` returns collections the requester may edit."""
    headers = auth_headers(other_user)

    editable = client.get("/api/v1/playlists?editable=true", headers=headers)
    assert {p["id"] for p in editable.json()} == {private_playlist["id"]}

    # The library has no collaborator grant — excluded from editable.
    libraries = client.get("/api/v1/libraries?editable=true", headers=headers)
    assert all(lib["id"] != private_library["id"] for lib in libraries.json())


def test_editable_filter_excludes_plain_grants(client, other_user, auth_headers, private_playlist, viewer_grant):
    """A read-only share does not appear in the editable listing."""
    editable = client.get(
        "/api/v1/playlists?editable=true",
        headers=auth_headers(other_user),
    )
    assert all(p["id"] != private_playlist["id"] for p in editable.json())


@pytest.mark.asyncio
async def test_library_collaborator_adds_tracks_with_attribution(
    client, db_session, regular_user, other_user, auth_headers, private_library
):
    """A library collaborator can add tracks and is credited as the adder."""
    grant = client.post(
        "/api/v1/shares",
        json={
            "item_type": "library",
            "item_id": private_library["id"],
            "user_id": str(other_user.id),
            "collaborator": True,
        },
        headers=auth_headers(regular_user),
    )
    assert grant.status_code == 201

    track = await _make_track(db_session, regular_user)
    add = client.post(
        f"/api/v1/libraries/{private_library['id']}/tracks/add",
        json={"track_ids": [str(track.id)]},
        headers=auth_headers(other_user),
    )
    assert add.status_code == 201

    tracks = client.get(
        f"/api/v1/libraries/{private_library['id']}/tracks",
        headers=auth_headers(other_user),
    )
    assert tracks.status_code == 200
    entry = tracks.json()[0]
    assert entry["added_by_id"] == str(other_user.id)
    assert entry["added_by"]["username"] == other_user.username


@pytest.mark.asyncio
async def test_library_viewer_cannot_add_tracks(
    client, db_session, regular_user, other_user, auth_headers, private_library
):
    """A plain library grantee cannot add tracks."""
    client.post(
        "/api/v1/shares",
        json={
            "item_type": "library",
            "item_id": private_library["id"],
            "user_id": str(other_user.id),
        },
        headers=auth_headers(regular_user),
    )
    track = await _make_track(db_session, regular_user)
    response = client.post(
        f"/api/v1/libraries/{private_library['id']}/tracks/add",
        json={"track_ids": [str(track.id)]},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


def test_library_capability_flags(client, regular_user, other_user, auth_headers, private_library):
    """Library responses carry the same capability flags as playlists."""
    grant = client.post(
        "/api/v1/shares",
        json={
            "item_type": "library",
            "item_id": private_library["id"],
            "user_id": str(other_user.id),
            "collaborator": True,
        },
        headers=auth_headers(regular_user),
    ).json()

    collab = client.get(
        f"/api/v1/libraries/{private_library['id']}",
        headers=auth_headers(other_user),
    ).json()
    assert collab["can_write"] is True
    assert collab["can_manage"] is False
    assert collab["is_collaborator"] is True
    assert collab["share_grant_id"] == grant["id"]


@pytest.mark.asyncio
async def test_user_deletion_keeps_entries_and_clears_attribution(
    client, db_session, regular_user, other_user, admin_user, auth_headers, private_playlist, collaborator_grant
):
    """Deleting a collaborator nulls added_by_id but keeps the entries."""
    track = await _make_track(db_session, regular_user)
    client.post(
        f"/api/v1/playlists/{private_playlist['id']}/tracks",
        json={"track_ids": [str(track.id)]},
        headers=auth_headers(other_user),
    )

    playlist = await db_session.get(Playlist, private_playlist["id"])
    assert playlist is not None

    # Delete the collaborator via the admin endpoint.
    delete = client.delete(
        f"/api/v1/admin/users/{other_user.id}",
        headers=auth_headers(admin_user),
    )
    assert delete.status_code in (200, 204)

    items = client.get(
        f"/api/v1/playlists/{private_playlist['id']}/items",
        headers=auth_headers(regular_user),
    ).json()
    assert len(items) == 1
    assert items[0]["added_by_id"] is None
    assert items[0]["added_by"] is None


@pytest.mark.asyncio
async def test_backfilled_rows_attribute_owner(
    client, db_session, regular_user, other_user, auth_headers, private_playlist
):
    """Rows written before attribution show no adder (NULL added_by_id)."""
    playlist = await db_session.get(Playlist, private_playlist["id"])
    track = await _make_track(db_session, regular_user)

    db_session.add(PlaylistTrack(playlist_id=playlist.id, track_id=track.id, position=0))
    await db_session.commit()

    items = client.get(
        f"/api/v1/playlists/{private_playlist['id']}/items",
        headers=auth_headers(regular_user),
    ).json()
    assert items[0]["added_by_id"] is None
    assert items[0]["added_by"] is None


@pytest.mark.asyncio
async def test_collaborator_visible_in_collection_listing(
    client, db_session, regular_user, other_user, auth_headers, private_playlist, collaborator_grant
):
    """Collaborated collections appear in the grantee's collection listing."""
    listed = client.get("/api/v1/playlists", headers=auth_headers(other_user))
    assert any(p["id"] == private_playlist["id"] for p in listed.json())
