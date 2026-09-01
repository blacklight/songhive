"""Tests for external-track fields in TrackResponse and write-back enqueue."""

from unittest.mock import MagicMock

import pytest

from songhive.models._enums import Visibility
from songhive.models.artist import Artist
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User


async def _make_external_track(
    session,
    owner: User,
    state: str = "active",
    capabilities: dict | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> tuple[Track, ExternalLibrary, ExternalTrack]:
    """Create a track with an active external track and supporting objects."""
    artist = Artist(name="Test Artist")
    session.add(artist)
    await session.flush()

    library = Library(name="External Library", owner_id=owner.id, visibility=visibility)
    session.add(library)
    await session.flush()

    external_library = ExternalLibrary(
        library_id=str(library.id),
        provider_type="fake",
        scope="user",
        include_in_library_index=False,
        capabilities=capabilities
        or {
            "read_bytes": True,
            "stream_url": True,
            "download": True,
            "write_tags": True,
            "delete_source": False,
        },
        created_by_id=owner.id,
    )
    session.add(external_library)
    await session.flush()

    track = Track(
        title="External Track",
        artist_id=artist.id,
        owner_id=owner.id,
        visibility=visibility,
    )
    session.add(track)
    await session.flush()

    session.add(LibraryTrack(library_id=library.id, track_id=track.id, added_by_id=owner.id))
    await session.flush()

    external_track = ExternalTrack(
        external_library_id=str(external_library.id),
        track_id=track.id,
        provider_key="song.mp3",
        state=state,
    )
    session.add(external_track)
    await session.flush()

    return track, external_library, external_track


@pytest.mark.asyncio
async def test_track_response_includes_external_fields(client, regular_user, db_session, auth_headers):
    """GET /tracks/{id} returns external metadata and capability flags."""
    track, _, _ = await _make_external_track(db_session, regular_user)

    response = client.get(f"/api/v1/tracks/{track.id}", headers=auth_headers(regular_user))
    assert response.status_code == 200
    data = response.json()

    assert data["is_external"] is True
    assert data["external_provider_type"] == "fake"
    assert data["external_state"] == "active"
    assert data["can_stream"] is True
    assert data["can_download"] is True
    assert data["can_write_tags"] is True
    assert data["can_delete_source"] is False
    assert data["audio_url"] == f"/api/v1/tracks/{track.id}/download"


@pytest.mark.asyncio
async def test_track_list_excludes_tombstoned_external_tracks(client, regular_user, db_session, auth_headers):
    """Tombstoned external tracks do not appear in GET /tracks."""
    active_track, _, _ = await _make_external_track(db_session, regular_user, state="active")
    tombstoned_track, _, _ = await _make_external_track(db_session, regular_user, state="tombstoned")
    shadowed_track, _, _ = await _make_external_track(db_session, regular_user, state="shadowed")

    response = client.get("/api/v1/tracks", headers=auth_headers(regular_user))
    assert response.status_code == 200
    data = response.json()
    ids = {item["id"] for item in data}

    assert str(active_track.id) in ids
    assert str(tombstoned_track.id) not in ids
    assert str(shadowed_track.id) not in ids


@pytest.mark.asyncio
async def test_update_external_track_enqueues_write_back(client, regular_user, db_session, auth_headers, monkeypatch):
    """PATCH /tracks/{id} enqueues write-back for external tracks with write_tags."""
    track, _, external_track = await _make_external_track(db_session, regular_user)

    write_back_mock = MagicMock()
    sync_mock = MagicMock()
    monkeypatch.setattr("songhive.api.routes.tracks.write_back_metadata_task.delay", write_back_mock)
    monkeypatch.setattr("songhive.tasks.tags.sync_track_tags.delay", sync_mock)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        headers=auth_headers(regular_user),
        json={"title": "Updated Title"},
    )
    assert response.status_code == 200

    write_back_mock.assert_called_once_with(str(external_track.id))
    sync_mock.assert_not_called()

    await db_session.refresh(external_track)
    await db_session.refresh(track)
    assert external_track.write_back_pending is True
    assert track.metadata_updated_at is not None


@pytest.mark.asyncio
async def test_update_local_track_enqueues_tag_sync(client, regular_user, db_session, auth_headers, monkeypatch):
    """PATCH /tracks/{id} enqueues tag sync for Songhive-managed tracks."""
    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()

    stored = StoredFile(
        storage_path="/tmp/test.mp3",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1234,
        sha256="a" * 64,
        owner_id=regular_user.id,
    )
    db_session.add(stored)
    await db_session.flush()

    track = Track(
        title="Local Track",
        artist_id=artist.id,
        owner_id=regular_user.id,
        visibility=Visibility.PUBLIC.value,
        audio_file_id=str(stored.id),
    )
    db_session.add(track)
    await db_session.flush()

    write_back_mock = MagicMock()
    sync_mock = MagicMock()
    monkeypatch.setattr("songhive.api.routes.tracks.write_back_metadata_task.delay", write_back_mock)
    monkeypatch.setattr("songhive.tasks.tags.sync_track_tags.delay", sync_mock)

    response = client.patch(
        f"/api/v1/tracks/{track.id}",
        headers=auth_headers(regular_user),
        json={"title": "Updated Local Title"},
    )
    assert response.status_code == 200

    sync_mock.assert_called_once_with(str(track.id))
    write_back_mock.assert_not_called()
