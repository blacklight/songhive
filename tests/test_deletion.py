"""
Tests for cascade deletion of tracks, files, playlists, albums, artists, and libraries.
"""

import io
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from songhive.config.schema import StorageConfig
from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.services import deletion
from songhive.services.metadata import AudioMetadata
from songhive.services.storage import StorageService
from songhive.storage import get_storage


def _fake_metadata():
    return AudioMetadata(
        title="Uploaded Song",
        artist="Uploaded Artist",
        album="Uploaded Album",
        mimetype="audio/mpeg",
    )


@pytest.fixture
def files_client(client, tmp_path):
    """Return a test client with a temp local storage path."""
    client.app.state.config.storage.local_path = tmp_path / "media"
    return client


def _upload_audio(files_client, auth_headers, user, content, visibility="private", library_id=None):
    """Upload an audio file and return (file_id, track_id)."""
    url = f"/api/v1/files/upload?visibility={visibility}"
    if library_id:
        url += f"&library_id={library_id}"
    response = files_client.post(
        url,
        files={"file": ("song.mp3", io.BytesIO(content), "audio/mpeg")},
        headers=auth_headers(user),
    )
    assert response.status_code == 200
    file_id = response.json()["id"]
    track_id = response.headers.get("X-Track-Id")
    assert track_id is not None
    return file_id, track_id


def test_delete_track_removes_upload_and_memberships(files_client, regular_user, auth_headers, monkeypatch):
    """Deleting a track deletes its upload, file, and playlist/favorite/history entries."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)

    content = b"delete track test audio"
    file_id, track_id = _upload_audio(files_client, auth_headers, regular_user, content)

    playlist = files_client.post(
        "/api/v1/playlists/",
        json={"name": "My Playlist"},
        headers=headers,
    )
    assert playlist.status_code == 201
    playlist_id = playlist.json()["id"]

    add = files_client.post(
        f"/api/v1/playlists/{playlist_id}/tracks",
        json={"track_ids": [track_id]},
        headers=headers,
    )
    assert add.status_code == 201

    fav = files_client.post(f"/api/v1/favorites/{track_id}", headers=headers)
    assert fav.status_code == 201
    hist = files_client.post(f"/api/v1/history/{track_id}", headers=headers)
    assert hist.status_code == 201

    response = files_client.delete(f"/api/v1/tracks/{track_id}", headers=headers)
    assert response.status_code == 204

    assert files_client.get(f"/api/v1/tracks/{track_id}", headers=headers).status_code == 404
    assert files_client.get(f"/api/v1/files/{file_id}", headers=headers).status_code == 404

    tracks = files_client.get(f"/api/v1/playlists/{playlist_id}/tracks", headers=headers)
    assert tracks.json() == []

    assert files_client.get("/api/v1/favorites/", headers=headers).json() == []
    assert files_client.get("/api/v1/history/", headers=headers).json() == []


def test_delete_file_removes_track_and_references(files_client, regular_user, auth_headers, monkeypatch):
    """Deleting a stored file removes the track that uses it and playlist references."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)

    content = b"delete file test audio"
    file_id, track_id = _upload_audio(files_client, auth_headers, regular_user, content)

    playlist = files_client.post(
        "/api/v1/playlists/",
        json={"name": "File Playlist"},
        headers=headers,
    )
    playlist_id = playlist.json()["id"]
    files_client.post(
        f"/api/v1/playlists/{playlist_id}/tracks",
        json={"track_ids": [track_id]},
        headers=headers,
    )

    response = files_client.delete(f"/api/v1/files/{file_id}", headers=headers)
    assert response.status_code == 204

    assert files_client.get(f"/api/v1/files/{file_id}", headers=headers).status_code == 404
    assert files_client.get(f"/api/v1/tracks/{track_id}", headers=headers).status_code == 404
    tracks = files_client.get(f"/api/v1/playlists/{playlist_id}/tracks", headers=headers)
    assert tracks.json() == []


def test_delete_playlist_default_keeps_tracks(files_client, regular_user, auth_headers, monkeypatch):
    """Deleting a playlist without the recursive flag keeps its tracks."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)

    content = b"playlist keep audio"
    _, track_id = _upload_audio(files_client, auth_headers, regular_user, content)

    playlist = files_client.post(
        "/api/v1/playlists/",
        json={"name": "Keep Playlist"},
        headers=headers,
    )
    playlist_id = playlist.json()["id"]
    files_client.post(
        f"/api/v1/playlists/{playlist_id}/tracks",
        json={"track_ids": [track_id]},
        headers=headers,
    )

    response = files_client.delete(f"/api/v1/playlists/{playlist_id}", headers=headers)
    assert response.status_code == 204

    assert files_client.get(f"/api/v1/playlists/{playlist_id}", headers=headers).status_code == 404
    track = files_client.get(f"/api/v1/tracks/{track_id}", headers=headers)
    assert track.status_code == 200
    assert track.json()["id"] == track_id


def test_delete_playlist_recursive_deletes_tracks(files_client, regular_user, auth_headers, monkeypatch):
    """Deleting a playlist with recursive=true removes its tracks and uploads."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)

    content = b"playlist recursive audio"
    file_id, track_id = _upload_audio(files_client, auth_headers, regular_user, content)

    playlist = files_client.post(
        "/api/v1/playlists/",
        json={"name": "Recursive Playlist"},
        headers=headers,
    )
    playlist_id = playlist.json()["id"]
    files_client.post(
        f"/api/v1/playlists/{playlist_id}/tracks",
        json={"track_ids": [track_id]},
        headers=headers,
    )

    response = files_client.delete(
        f"/api/v1/playlists/{playlist_id}?recursive=true",
        headers=headers,
    )
    assert response.status_code == 204

    assert files_client.get(f"/api/v1/playlists/{playlist_id}", headers=headers).status_code == 404
    assert files_client.get(f"/api/v1/tracks/{track_id}", headers=headers).status_code == 404
    assert files_client.get(f"/api/v1/files/{file_id}", headers=headers).status_code == 404


def test_delete_library_default_keeps_tracks(files_client, regular_user, auth_headers, monkeypatch):
    """Deleting a library without the recursive flag keeps its tracks."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)

    library = files_client.post(
        "/api/v1/libraries/",
        json={"name": "Keep Library", "visibility": "private"},
        headers=headers,
    )
    assert library.status_code == 201
    library_id = library.json()["id"]

    content = b"library keep audio"
    file_id, track_id = _upload_audio(
        files_client,
        auth_headers,
        regular_user,
        content,
        library_id=library_id,
    )

    response = files_client.delete(f"/api/v1/libraries/{library_id}", headers=headers)
    assert response.status_code == 204
    assert files_client.get(f"/api/v1/libraries/{library_id}", headers=headers).status_code == 404

    track = files_client.get(f"/api/v1/tracks/{track_id}", headers=headers)
    assert track.status_code == 200
    assert files_client.get(f"/api/v1/files/{file_id}", headers=headers).status_code == 200


def test_delete_library_recursive_deletes_tracks(files_client, regular_user, auth_headers, monkeypatch):
    """Deleting a library with recursive=true removes its tracks and uploads."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)

    library = files_client.post(
        "/api/v1/libraries/",
        json={"name": "Recursive Library", "visibility": "private"},
        headers=headers,
    )
    library_id = library.json()["id"]

    content = b"library recursive audio"
    file_id, track_id = _upload_audio(
        files_client,
        auth_headers,
        regular_user,
        content,
        library_id=library_id,
    )

    response = files_client.delete(
        f"/api/v1/libraries/{library_id}?recursive=true",
        headers=headers,
    )
    assert response.status_code == 204

    assert files_client.get(f"/api/v1/libraries/{library_id}", headers=headers).status_code == 404
    assert files_client.get(f"/api/v1/tracks/{track_id}", headers=headers).status_code == 404
    assert files_client.get(f"/api/v1/files/{file_id}", headers=headers).status_code == 404


def test_delete_album_default_recursive(files_client, regular_user, auth_headers, monkeypatch):
    """Albums are deleted recursively by default, removing their tracks."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)

    content = b"album recursive audio"
    file_id, track_id = _upload_audio(files_client, auth_headers, regular_user, content)

    track = files_client.get(f"/api/v1/tracks/{track_id}", headers=headers).json()
    album_id = track["album_id"]
    assert album_id is not None

    response = files_client.delete(f"/api/v1/albums/{album_id}", headers=headers)
    assert response.status_code == 204

    assert files_client.get(f"/api/v1/albums/{album_id}", headers=headers).status_code == 404
    assert files_client.get(f"/api/v1/tracks/{track_id}", headers=headers).status_code == 404
    assert files_client.get(f"/api/v1/files/{file_id}", headers=headers).status_code == 404


def test_delete_album_non_recursive_keeps_tracks(files_client, regular_user, auth_headers, monkeypatch):
    """Albums can be deleted without removing their tracks."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())
    headers = auth_headers(regular_user)

    content = b"album keep audio"
    file_id, track_id = _upload_audio(files_client, auth_headers, regular_user, content)

    track = files_client.get(f"/api/v1/tracks/{track_id}", headers=headers).json()
    album_id = track["album_id"]

    response = files_client.delete(
        f"/api/v1/albums/{album_id}?recursive=false",
        headers=headers,
    )
    assert response.status_code == 204

    assert files_client.get(f"/api/v1/albums/{album_id}", headers=headers).status_code == 404
    track = files_client.get(f"/api/v1/tracks/{track_id}", headers=headers)
    assert track.status_code == 200
    assert track.json()["album_id"] is None
    assert files_client.get(f"/api/v1/files/{file_id}", headers=headers).status_code == 200


def test_delete_artist_non_recursive_with_tracks_fails(
    files_client, regular_user, admin_user, auth_headers, monkeypatch
):
    """Non-recursive artist deletion fails when the artist has tracks."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())

    content = b"artist with tracks audio"
    _, track_id = _upload_audio(files_client, auth_headers, regular_user, content)

    track = files_client.get(f"/api/v1/tracks/{track_id}", headers=auth_headers(regular_user)).json()
    artist_id = track["artist_id"]

    response = files_client.delete(
        f"/api/v1/artists/{artist_id}",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 409


def test_delete_artist_recursive(files_client, regular_user, admin_user, auth_headers, monkeypatch):
    """Recursive artist deletion removes the artist, albums, and tracks."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())

    content = b"artist recursive audio"
    file_id, track_id = _upload_audio(files_client, auth_headers, regular_user, content)

    track = files_client.get(f"/api/v1/tracks/{track_id}", headers=auth_headers(regular_user)).json()
    artist_id = track["artist_id"]
    album_id = track["album_id"]

    response = files_client.delete(
        f"/api/v1/artists/{artist_id}?recursive=true",
        headers=auth_headers(admin_user),
    )
    assert response.status_code == 204

    admin_headers = auth_headers(admin_user)
    assert files_client.get(f"/api/v1/artists/{artist_id}", headers=admin_headers).status_code == 404
    assert files_client.get(f"/api/v1/albums/{album_id}", headers=admin_headers).status_code == 404
    assert files_client.get(f"/api/v1/tracks/{track_id}", headers=admin_headers).status_code == 404
    assert files_client.get(f"/api/v1/files/{file_id}", headers=admin_headers).status_code == 404


def test_delete_artist_forbidden_for_non_admin(files_client, regular_user, other_user, auth_headers, monkeypatch):
    """Regular users cannot delete artists."""
    monkeypatch.setattr("songhive.services.import_.extract_metadata", lambda _: _fake_metadata())

    content = b"artist forbidden audio"
    _, track_id = _upload_audio(files_client, auth_headers, regular_user, content)
    artist_id = files_client.get(f"/api/v1/tracks/{track_id}", headers=auth_headers(regular_user)).json()["artist_id"]

    response = files_client.delete(
        f"/api/v1/artists/{artist_id}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 403


@pytest.fixture
def storage_service(tmp_path):
    """Create a StorageService backed by a local temp directory."""
    config = StorageConfig(backend="local", local_path=tmp_path / "media")
    backend = get_storage(config)
    return StorageService(backend, config)


@pytest.mark.asyncio
async def test_delete_artist_recursive_fails_early_for_unmanageable_tracks(
    db_session, regular_user, other_user, storage_service
):
    """A non-admin cannot partially delete an artist that contains tracks owned by others."""
    artist = Artist(name="Shared Artist")
    db_session.add(artist)
    await db_session.flush()

    own_track = Track(
        title="Own Track",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    other_track = Track(
        title="Other Track",
        artist_id=artist.id,
        owner_id=other_user.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add_all([own_track, other_track])
    await db_session.flush()

    track_count_before = await db_session.scalar(select(func.count(Track.id)).where(Track.artist_id == artist.id))

    with pytest.raises(deletion.DeletionError, match="tracks owned by another user") as exc_info:
        await deletion.delete_artist(
            db_session,
            storage_service,
            str(artist.id),
            recursive=True,
            user=regular_user,
            is_admin=False,
        )
    assert exc_info.value.status_code == 403

    track_count_after = await db_session.scalar(select(func.count(Track.id)).where(Track.artist_id == artist.id))
    assert track_count_before == track_count_after


@pytest.mark.asyncio
async def test_delete_track_keeps_stored_file_when_storage_delete_fails(
    db_session, regular_user, storage_service, monkeypatch
):
    """If the storage backend cannot delete a file, the StoredFile row is kept."""
    content = b"audio content"
    file = io.BytesIO(content)
    stored_file = await storage_service.store_file(db_session, file, "audio/mpeg", owner_id=str(regular_user.id))
    await db_session.flush()

    artist = Artist(name="Storage Failure Artist")
    db_session.add(artist)
    await db_session.flush()

    track = Track(
        title="Storage Failure Track",
        artist_id=artist.id,
        owner_id=regular_user.id,
        audio_file_id=stored_file.id,
        visibility=Visibility.PUBLIC.value,
    )
    db_session.add(track)
    await db_session.flush()

    monkeypatch.setattr(storage_service, "delete_file", AsyncMock(side_effect=RuntimeError("storage down")))

    await deletion.delete_track(db_session, storage_service, str(track.id))
    await db_session.flush()

    assert await db_session.get(Track, track.id) is None
    assert await db_session.get(StoredFile, stored_file.id) is not None
    assert await storage_service.backend.exists(stored_file.storage_path) is True
