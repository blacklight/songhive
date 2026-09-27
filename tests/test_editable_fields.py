"""Tests for provider ``editable_fields`` capability enforcement (S8)."""

from __future__ import annotations

import io

import pytest

from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.external_item import ExternalItem
from songhive.models.external_library import ExternalLibrary
from songhive.models.library import Library
from songhive.models.track import Track
from songhive.services import secrets


@pytest.fixture
async def locked_external_library(db_session, regular_user):
    """A TIDAL-like external library restricting edits to genres/tags."""
    library = Library(name="Tidal Lib", owner_id=str(regular_user.id), visibility="private")
    db_session.add(library)
    await db_session.flush()
    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type="tidal",
        config=secrets.encrypt_json({}),
        enabled=True,
        created_by_id=str(regular_user.id),
        capabilities={
            "stream_url": True,
            "limits": {"editable_fields": ["genres", "tags"], "immutable_tracks": True},
        },
    )
    db_session.add(external_library)
    await db_session.flush()
    return external_library


@pytest.fixture
async def provider_track(db_session, regular_user, locked_external_library):
    artist = Artist(name="Provider Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="Provider Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility="private",
    )
    db_session.add(track)
    await db_session.flush()
    db_session.add(
        ExternalItem(
            external_library_id=str(locked_external_library.id),
            kind="track",
            provider_key="t-1",
            track_id=str(track.id),
            state="active",
        )
    )
    await db_session.flush()
    return track


@pytest.fixture
async def local_track(db_session, regular_user):
    artist = Artist(name="Local Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(
        title="Local Track",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility="private",
    )
    db_session.add(track)
    await db_session.flush()
    return track


@pytest.fixture
async def provider_album(db_session, regular_user, locked_external_library):
    artist = Artist(name="Provider Album Artist")
    db_session.add(artist)
    await db_session.flush()
    album = Album(
        title="Provider Album",
        artist_id=artist.id,
        owner_id=str(regular_user.id),
        visibility="private",
    )
    db_session.add(album)
    await db_session.flush()
    db_session.add(
        ExternalItem(
            external_library_id=str(locked_external_library.id),
            kind="album",
            provider_key="a-1",
            album_id=str(album.id),
            state="active",
        )
    )
    await db_session.flush()
    return album


@pytest.fixture
async def provider_artist(db_session, locked_external_library):
    artist = Artist(name="Provider Artist")
    db_session.add(artist)
    await db_session.flush()
    db_session.add(
        ExternalItem(
            external_library_id=str(locked_external_library.id),
            kind="artist",
            provider_key="ar-1",
            artist_id=str(artist.id),
            state="active",
        )
    )
    await db_session.flush()
    return artist


# ---------------------------------------------------------------------------
# Tracks
# ---------------------------------------------------------------------------


def test_provider_track_metadata_edit_rejected(client, provider_track, regular_user, auth_headers):
    response = client.patch(
        f"/api/v1/tracks/{provider_track.id}",
        json={"title": "New Title"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422
    assert "managed by the external provider" in response.json()["detail"]


def test_provider_track_genre_and_tags_allowed(client, provider_track, regular_user, auth_headers):
    headers = auth_headers(regular_user)
    response = client.patch(f"/api/v1/tracks/{provider_track.id}", json={"genre": "Jazz"}, headers=headers)
    assert response.status_code == 200

    response = client.post(f"/api/v1/tracks/{provider_track.id}/tags", json={"tags": ["chill"]}, headers=headers)
    assert response.status_code == 200


def test_provider_track_visibility_still_editable(client, provider_track, regular_user, auth_headers):
    response = client.patch(
        f"/api/v1/tracks/{provider_track.id}",
        json={"visibility": "local"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200


def test_provider_track_image_upload_rejected(client, provider_track, regular_user, auth_headers):
    response = client.post(
        f"/api/v1/tracks/{provider_track.id}/image",
        files={"file": ("cover.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_provider_track_exposes_editable_fields(client, provider_track, regular_user, auth_headers):
    response = client.get(f"/api/v1/tracks/{provider_track.id}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["editable_fields"] == ["genres", "tags"]


def test_local_track_unrestricted(client, local_track, regular_user, auth_headers):
    headers = auth_headers(regular_user)
    response = client.patch(f"/api/v1/tracks/{local_track.id}", json={"title": "Renamed"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"
    assert response.json()["editable_fields"] is None


# ---------------------------------------------------------------------------
# Albums and artists
# ---------------------------------------------------------------------------


def test_provider_album_metadata_edit_rejected(client, provider_album, regular_user, auth_headers):
    response = client.patch(
        f"/api/v1/albums/{provider_album.id}",
        json={"title": "New Album Title"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_provider_album_genre_allowed(client, provider_album, regular_user, auth_headers):
    response = client.patch(
        f"/api/v1/albums/{provider_album.id}",
        json={"genre": "Ambient"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200


def test_provider_album_cover_rejected(client, provider_album, regular_user, auth_headers):
    response = client.post(
        f"/api/v1/albums/{provider_album.id}/cover",
        files={"file": ("cover.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png")},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


def test_provider_album_exposes_editable_fields(client, provider_album, regular_user, auth_headers):
    response = client.get(f"/api/v1/albums/{provider_album.id}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["editable_fields"] == ["genres", "tags"]


def test_provider_artist_name_edit_rejected(client, provider_artist, admin_user, auth_headers):
    # Artists are ownerless containers; only admins manage them.
    response = client.patch(
        f"/api/v1/artists/{provider_artist.id}",
        json={"name": "Renamed Artist"},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 422


def test_provider_artist_tags_allowed(client, provider_artist, admin_user, auth_headers):
    response = client.post(
        f"/api/v1/artists/{provider_artist.id}/tags",
        json={"tags": ["favorite"]},
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 200


def test_provider_artist_exposes_editable_fields(client, provider_artist, auth_headers, regular_user):
    response = client.get(f"/api/v1/artists/{provider_artist.id}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert response.json()["editable_fields"] == ["genres", "tags"]
