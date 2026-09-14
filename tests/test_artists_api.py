"""
Tests for the artist API endpoints.
"""

import io
from datetime import datetime, timezone

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.share_grant import ShareGrant
from songhive.models.track import Track


@pytest.fixture
async def sample_artists(db_session):
    """Create artists for testing."""
    artists = []
    for name in ["Public Artist", "Local Artist"]:
        artist = Artist(name=name)
        db_session.add(artist)
        artists.append(artist)
    await db_session.flush()
    return artists


def test_get_artist(client, sample_artists):
    """Fetching an artist returns its public profile."""
    artist = sample_artists[0]
    response = client.get(f"/api/v1/artists/{artist.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(artist.id)
    assert body["name"] == artist.name


def test_admin_update_artist(client, admin_user, sample_artists, auth_headers):
    """Admins can update artist metadata."""
    artist = sample_artists[0]
    response = client.patch(
        f"/api/v1/artists/{artist.id}",
        json={"name": "Renamed Artist", "bio": "A bio"},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed Artist"
    assert body["bio"] == "A bio"


def test_non_admin_cannot_update_artist(client, regular_user, sample_artists, auth_headers):
    """Regular users cannot update artist metadata."""
    artist = sample_artists[0]
    response = client.patch(
        f"/api/v1/artists/{artist.id}",
        json={"name": "Renamed"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 403


def test_admin_upload_and_delete_artist_images(client, admin_user, sample_artists, auth_headers):
    """Admins can upload and remove artist image and cover."""
    artist = sample_artists[0]
    headers = auth_headers(admin_user)

    image = client.post(
        f"/api/v1/artists/{artist.id}/image",
        files={"file": ("image.jpg", io.BytesIO(b"fake image"), "image/jpeg")},
        headers=headers,
    )
    assert image.status_code == 200
    assert image.json()["image_url"] is not None

    cover = client.post(
        f"/api/v1/artists/{artist.id}/cover",
        files={"file": ("cover.jpg", io.BytesIO(b"fake cover"), "image/jpeg")},
        headers=headers,
    )
    assert cover.status_code == 200
    assert cover.json()["cover_url"] is not None

    delete = client.delete(f"/api/v1/artists/{artist.id}/image", headers=headers)
    assert delete.status_code == 200
    assert delete.json()["image_url"] is None

    delete_cover = client.delete(f"/api/v1/artists/{artist.id}/cover", headers=headers)
    assert delete_cover.status_code == 200
    assert delete_cover.json()["cover_url"] is None


@pytest.fixture
async def sortable_artists(db_session):
    """Create artists with distinct names, creation and update times."""
    artists = [
        Artist(
            name="C",
            created_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        ),
        Artist(
            name="A",
            created_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
        ),
        Artist(
            name="B",
            created_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    for artist in artists:
        db_session.add(artist)
    await db_session.flush()
    for artist in artists:
        db_session.add(
            Track(
                title=f"{artist.name} track",
                artist_id=artist.id,
                visibility=Visibility.PUBLIC.value,
            )
        )
    await db_session.commit()
    return artists


@pytest.mark.parametrize(
    "sort_by,sort_dir,expected",
    [
        ("name", "asc", ["A", "B", "C"]),
        ("name", "desc", ["C", "B", "A"]),
        ("created_at", "asc", ["C", "A", "B"]),
        ("created_at", "desc", ["B", "A", "C"]),
        ("updated_at", "asc", ["B", "A", "C"]),
        ("updated_at", "desc", ["C", "A", "B"]),
    ],
)
@pytest.mark.asyncio
async def test_list_artists_sorts(client, sortable_artists, sort_by, sort_dir, expected):
    """The artist list endpoint honours every supported sort key and direction."""
    response = client.get(f"/api/v1/artists?sort_by={sort_by}&sort_dir={sort_dir}")
    assert response.status_code == 200
    data = response.json()
    assert [artist["name"] for artist in data] == expected


async def _artist_names(response) -> set:
    """Return the set of artist names in a list response."""
    assert response.status_code == 200
    return {artist["name"] for artist in response.json()}


@pytest.mark.asyncio
async def test_list_artists_hides_fully_private_artists(client, db_session, regular_user, other_user, auth_headers):
    """Artists whose tracks are all private and unshared are hidden from others."""
    private_artist = Artist(name="Private Artist")
    public_artist = Artist(name="Public Artist")
    db_session.add_all([private_artist, public_artist])
    await db_session.flush()
    db_session.add_all(
        [
            Track(
                title="Secret Track",
                artist_id=private_artist.id,
                owner_id=regular_user.id,
                visibility=Visibility.PRIVATE.value,
            ),
            Track(
                title="Open Track",
                artist_id=public_artist.id,
                owner_id=regular_user.id,
                visibility=Visibility.PUBLIC.value,
            ),
        ]
    )
    await db_session.commit()

    names = await _artist_names(client.get("/api/v1/artists/"))
    assert names == {"Public Artist"}

    names = await _artist_names(client.get("/api/v1/artists/", headers=auth_headers(other_user)))
    assert names == {"Public Artist"}

    names = await _artist_names(client.get("/api/v1/artists/", headers=auth_headers(regular_user)))
    assert names == {"Private Artist", "Public Artist"}


@pytest.mark.asyncio
async def test_list_artists_share_grant_reveals_artist(client, db_session, regular_user, other_user, auth_headers):
    """A share grant on an artist's track makes the artist visible again."""
    artist = Artist(name="Shared Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="Shared Track",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(track)
    await db_session.flush()
    db_session.add(
        ShareGrant(
            item_type="track",
            item_id=track.id,
            user_id=other_user.id,
            created_by=regular_user.id,
        )
    )
    await db_session.commit()

    names = await _artist_names(client.get("/api/v1/artists/"))
    assert names == set()

    names = await _artist_names(client.get("/api/v1/artists/", headers=auth_headers(other_user)))
    assert names == {"Shared Artist"}


@pytest.mark.asyncio
async def test_list_artists_visible_through_public_album(client, db_session, regular_user, auth_headers):
    """An artist with a public album stays listed even when its tracks are private."""
    artist = Artist(name="Album Artist")
    db_session.add(artist)
    await db_session.flush()
    album = Album(
        title="Public Album",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(album)
    await db_session.flush()
    db_session.add(
        Track(
            title="Private Track",
            artist_id=artist.id,
            album_id=album.id,
            owner_id=regular_user.id,
            visibility=Visibility.PRIVATE.value,
        )
    )
    await db_session.commit()

    names = await _artist_names(client.get("/api/v1/artists/"))
    assert names == {"Album Artist"}


@pytest.mark.asyncio
async def test_list_artists_admin_sees_all(client, db_session, regular_user, admin_user, auth_headers):
    """Admins bypass the artist access filter entirely."""
    empty_artist = Artist(name="Empty Artist")
    private_artist = Artist(name="Private Artist")
    db_session.add_all([empty_artist, private_artist])
    await db_session.flush()
    db_session.add(
        Track(
            title="Secret Track",
            artist_id=private_artist.id,
            owner_id=regular_user.id,
            visibility=Visibility.PRIVATE.value,
        )
    )
    await db_session.commit()

    names = await _artist_names(client.get("/api/v1/artists/", headers=auth_headers(admin_user)))
    assert names == {"Empty Artist", "Private Artist"}


@pytest.mark.asyncio
async def test_list_artists_local_tracks_require_auth(client, db_session, regular_user, other_user, auth_headers):
    """Artists with only LOCAL tracks are visible to authenticated users only."""
    artist = Artist(name="Local Artist")
    db_session.add(artist)
    await db_session.flush()
    db_session.add(
        Track(
            title="Local Track",
            artist_id=artist.id,
            owner_id=regular_user.id,
            visibility=Visibility.LOCAL.value,
        )
    )
    await db_session.commit()

    names = await _artist_names(client.get("/api/v1/artists/"))
    assert names == set()

    names = await _artist_names(client.get("/api/v1/artists/", headers=auth_headers(other_user)))
    assert names == {"Local Artist"}
