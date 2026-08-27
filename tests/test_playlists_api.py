"""
Tests for the playlist API endpoints.
"""

import io
from datetime import datetime, timezone

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.track import Track


@pytest.fixture
def sample_playlists(client, regular_user, auth_headers):
    """Create a public, local, and private playlist owned by ``regular_user``."""
    headers = auth_headers(regular_user)
    playlists = []
    for name, visibility in [
        ("Public Playlist", Visibility.PUBLIC),
        ("Local Playlist", Visibility.LOCAL),
        ("Private Playlist", Visibility.PRIVATE),
    ]:
        response = client.post(
            "/api/v1/playlists/",
            params={"visibility": visibility.value},
            json={"name": name},
            headers=headers,
        )
        assert response.status_code == 201
        playlists.append(response.json())
    return playlists


def _names(response):
    """Return the set of playlist names in a list response."""
    return {playlist["name"] for playlist in response.json()}


def test_list_playlists_filters_by_visibility(client, sample_playlists, regular_user, other_user, auth_headers):
    """List endpoints only return playlists the requester may access."""
    assert _names(client.get("/api/v1/playlists")) == {"Public Playlist"}

    other = client.get("/api/v1/playlists", headers=auth_headers(other_user))
    assert _names(other) == {"Public Playlist", "Local Playlist"}

    owner = client.get("/api/v1/playlists", headers=auth_headers(regular_user))
    assert _names(owner) == {"Public Playlist", "Local Playlist", "Private Playlist"}


def test_get_public_playlist_redacts_owner_for_non_owner(client, sample_playlists, other_user, auth_headers):
    """Non-owners see a null owner_id for public playlists."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "public")

    response = client.get(f"/api/v1/playlists/{playlist['id']}", headers=auth_headers(other_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] is None
    assert response.json()["visibility"] == "public"


def test_get_private_playlist_denied_for_other_user(client, sample_playlists, other_user, auth_headers):
    """Private playlists are denied (403) for other authenticated users."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "private")

    response = client.get(f"/api/v1/playlists/{playlist['id']}", headers=auth_headers(other_user))
    assert response.status_code == 403


def test_get_playlist_as_owner_sees_owner_id(client, sample_playlists, regular_user, auth_headers):
    """The owner sees their own owner_id on a playlist."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "private")

    response = client.get(f"/api/v1/playlists/{playlist['id']}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["owner_id"] == str(regular_user.id)


def test_create_playlist_sets_owner_and_visibility(client, regular_user, auth_headers):
    """Creating a playlist sets owner and visibility from the query parameter."""
    response = client.post(
        "/api/v1/playlists/?visibility=public",
        json={"name": "My Playlist"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["owner_id"] == str(regular_user.id)
    assert data["visibility"] == "public"


def test_create_playlist_invalid_visibility_returns_422(client, regular_user, auth_headers):
    """Creating a playlist with an unknown visibility value returns 422."""
    response = client.post(
        "/api/v1/playlists/?visibility=publick",
        json={"name": "Bad Playlist"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_get_missing_playlist_returns_404(client):
    """Requesting a missing playlist returns 404."""
    response = client.get("/api/v1/playlists/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_private_playlist_with_share_token(client, sample_playlists, regular_user, auth_headers):
    """A share URL token grants anonymous access to a private playlist."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "private")

    create = client.post(
        "/api/v1/share-urls",
        json={"item_type": "playlist", "item_id": playlist["id"]},
        headers=auth_headers(regular_user),
    )
    assert create.status_code == 201
    token = create.json()["token"]

    response = client.get(f"/api/v1/playlists/{playlist['id']}?token={token}")
    assert response.status_code == 200
    assert response.json()["id"] == playlist["id"]


def test_update_playlist_metadata(client, sample_playlists, regular_user, auth_headers):
    """Owners can update a playlist's name, description, and visibility."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "private")
    response = client.patch(
        f"/api/v1/playlists/{playlist['id']}",
        json={"name": "Updated", "description": "Updated description", "visibility": "public"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Updated"
    assert body["description"] == "Updated description"
    assert body["visibility"] == "public"
    assert "image_url" in body
    assert "cover_url" in body


def test_upload_and_delete_playlist_images(client, sample_playlists, regular_user, auth_headers):
    """Owners can upload and remove playlist image and cover."""
    playlist = next(p for p in sample_playlists if p["visibility"] == "public")
    headers = auth_headers(regular_user)

    image = client.post(
        f"/api/v1/playlists/{playlist['id']}/image",
        files={"file": ("image.jpg", io.BytesIO(b"fake image"), "image/jpeg")},
        headers=headers,
    )
    assert image.status_code == 200
    assert image.json()["image_url"] is not None

    cover = client.post(
        f"/api/v1/playlists/{playlist['id']}/cover",
        files={"file": ("cover.jpg", io.BytesIO(b"fake cover"), "image/jpeg")},
        headers=headers,
    )
    assert cover.status_code == 200
    assert cover.json()["cover_url"] is not None

    delete = client.delete(f"/api/v1/playlists/{playlist['id']}/image", headers=headers)
    assert delete.status_code == 200
    assert delete.json()["image_url"] is None

    delete_cover = client.delete(f"/api/v1/playlists/{playlist['id']}/cover", headers=headers)
    assert delete_cover.status_code == 200
    assert delete_cover.json()["cover_url"] is None


@pytest.fixture
async def sortable_playlists(db_session, regular_user):
    """Create playlists with distinct names, creation and update times."""
    playlists = [
        Playlist(
            name="C Playlist",
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            created_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        ),
        Playlist(
            name="A Playlist",
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            created_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
        ),
        Playlist(
            name="B Playlist",
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    for playlist in playlists:
        db_session.add(playlist)
    await db_session.commit()
    return playlists


@pytest.mark.parametrize(
    "sort_by,sort_dir,expected",
    [
        ("name", "asc", ["A Playlist", "B Playlist", "C Playlist"]),
        ("name", "desc", ["C Playlist", "B Playlist", "A Playlist"]),
        ("created_at", "asc", ["C Playlist", "A Playlist", "B Playlist"]),
        ("created_at", "desc", ["B Playlist", "A Playlist", "C Playlist"]),
        ("updated_at", "asc", ["B Playlist", "A Playlist", "C Playlist"]),
        ("updated_at", "desc", ["C Playlist", "A Playlist", "B Playlist"]),
    ],
)
@pytest.mark.asyncio
async def test_list_playlists_sorts(
    client,
    regular_user,
    auth_headers,
    sortable_playlists,
    sort_by,
    sort_dir,
    expected,
):
    """The playlist list endpoint honours every supported sort key and direction."""
    response = client.get(
        f"/api/v1/playlists?sort_by={sort_by}&sort_dir={sort_dir}",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert [playlist["name"] for playlist in data] == expected


@pytest.fixture
async def sortable_playlist_tracks(db_session, regular_user):
    """Create a playlist with tracks in a known positional order."""
    playlist = Playlist(
        name="Sortable Playlist",
        owner_id=str(regular_user.id),
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(playlist)
    await db_session.flush()

    artist = Artist(name="Sample Artist")
    db_session.add(artist)
    await db_session.flush()

    tracks = [
        Track(
            title="C Track",
            artist_id=artist.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
        ),
        Track(
            title="A Track",
            artist_id=artist.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
        ),
        Track(
            title="B Track",
            artist_id=artist.id,
            owner_id=str(regular_user.id),
            visibility=Visibility.PUBLIC.value,
        ),
    ]
    for track in tracks:
        db_session.add(track)
    await db_session.flush()

    for position, track in enumerate(tracks):
        db_session.add(
            PlaylistTrack(
                playlist_id=playlist.id,
                track_id=track.id,
                position=position,
            )
        )
    await db_session.commit()

    return playlist, tracks


@pytest.mark.asyncio
async def test_list_playlist_tracks_sorted_by_position_and_title(
    client,
    regular_user,
    auth_headers,
    sortable_playlist_tracks,
):
    """Playlist tracks can be sorted by position (default) or title."""
    playlist, tracks = sortable_playlist_tracks

    response = client.get(
        f"/api/v1/playlists/{playlist.id}/tracks?sort_by=position&sort_dir=asc",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert [track["title"] for track in data] == ["C Track", "A Track", "B Track"]

    response = client.get(
        f"/api/v1/playlists/{playlist.id}/tracks?sort_by=title&sort_dir=asc",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    data = response.json()
    assert [track["title"] for track in data] == ["A Track", "B Track", "C Track"]
