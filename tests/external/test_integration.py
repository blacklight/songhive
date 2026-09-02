"""End-to-end integration tests for the external-library subsystem."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.external.sync import sync_external_library
from songhive.models.audit_log import AuditLog
from songhive.models.external_track import ExternalTrack
from songhive.models.history import ListeningHistory
from songhive.models.track import Track
from songhive.services.metadata import AudioMetadata


def _sync_payload() -> dict:
    """Return the create payload for the full-flow integration library."""
    return {
        "provider_type": "fake",
        "library_name": "Integration External Library",
        "include_in_library_index": True,
        "config": {
            "items": {
                "track1.flac": {
                    "data": list(b"X" * 32000),
                    "mimetype": "audio/flac",
                    "metadata": {
                        "title": "Stream / Download One",
                        "artist": "Artist A",
                        "album": "Album A",
                        "duration": 31.0,
                    },
                },
                "track2.flac": {
                    "data": list(b"external2"),
                    "mimetype": "audio/flac",
                    "sha256": "a" * 64,
                    "metadata": {
                        "title": "Second Track",
                        "artist": "Artist B",
                        "album": "Album B",
                        "duration": 200.0,
                    },
                },
            },
            "secret_key": "integration-secret",
        },
    }


@pytest.mark.asyncio
async def test_full_external_library_flow(
    client,
    admin_user,
    auth_headers,
    db_session,
    fake_redis,
    tornado_client,
    monkeypatch,
):
    """Create, sync, stream, download, edit, tombstone, restore, and resolve an upload duplicate."""
    headers = auth_headers(admin_user)

    sync_mock = MagicMock()
    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        sync_mock,
    )
    write_back_mock = MagicMock()
    monkeypatch.setattr(
        "songhive.api.routes.tracks.write_back_metadata_task.delay",
        write_back_mock,
    )

    # Create admin external library.
    create_response = client.post(
        "/api/v1/admin/external-libraries",
        json=_sync_payload(),
        headers=headers,
    )
    assert create_response.status_code == status.HTTP_201_CREATED
    library_data = create_response.json()
    assert library_data["scope"] == "admin"
    assert library_data["include_in_library_index"] is True
    assert library_data["config"].get("secret_key") == "<redacted>"
    library_id = library_data["id"]

    # Enqueue a manual sync and then run it synchronously with the test session.
    sync_response = client.post(
        f"/api/v1/admin/external-libraries/{library_id}/sync",
        json={"include_tombstones": False},
        headers=headers,
    )
    assert sync_response.status_code == status.HTTP_202_ACCEPTED
    sync_data = sync_response.json()
    assert "sync_run_id" in sync_data
    assert sync_mock.called
    assert sync_mock.call_args[0][0] == library_id
    assert sync_mock.call_args[0][1] == "manual"
    assert sync_mock.call_args[0][3] is False

    run = await sync_external_library(
        db_session,
        library_id,
        triggered_by="manual",
        triggered_by_user_id=str(admin_user.id),
        redis=fake_redis,
    )
    assert run.status == "success"
    assert run.items_seen == 2
    assert run.tracks_created == 2

    # Two tracks appear in /tracks and the library is visible in /libraries.
    tracks_response = client.get("/api/v1/tracks", headers=headers)
    assert tracks_response.status_code == status.HTTP_200_OK
    tracks = tracks_response.json()
    assert len(tracks) == 2

    libraries_response = client.get("/api/v1/libraries", headers=headers)
    assert libraries_response.status_code == status.HTTP_200_OK
    libraries = libraries_response.json()
    assert any(lib["id"] == library_data["library_id"] for lib in libraries)

    # Locate the first track by provider key.
    ext_track_result = await db_session.execute(
        select(ExternalTrack)
        .where(ExternalTrack.external_library_id == library_id)
        .order_by(ExternalTrack.provider_key)
    )
    ext_tracks = list(ext_track_result.scalars().all())
    assert len(ext_tracks) == 2
    first_ext_track = ext_tracks[0]
    first_track = await db_session.get(Track, first_ext_track.track_id)
    assert first_track is not None

    # Stream the first track through the Tornado endpoint.
    stream_response = await tornado_client.get(
        f"/api/v1/stream/{first_track.id}",
        headers=headers,
    )
    assert stream_response.status_code == 200
    assert stream_response.content == b"X" * 32000

    listen_result = await db_session.execute(
        select(ListeningHistory).where(ListeningHistory.track_id == str(first_track.id))
    )
    assert listen_result.scalar_one_or_none() is not None

    # Download the first track and assert bytes + sanitized filename.
    download_response = client.get(
        f"/api/v1/tracks/{first_track.id}/download",
        headers=headers,
    )
    assert download_response.status_code == status.HTTP_200_OK
    assert download_response.content == b"X" * 32000
    disposition = download_response.headers.get("Content-Disposition", "")
    assert 'filename="Download One"' in disposition

    # Edit the track title; write-back should be enqueued.
    patch_response = client.patch(
        f"/api/v1/tracks/{first_track.id}",
        json={"title": "Updated Title"},
        headers=headers,
    )
    assert patch_response.status_code == status.HTTP_200_OK
    assert patch_response.json()["title"] == "Updated Title"
    assert write_back_mock.called
    assert write_back_mock.call_args[0][0] == str(first_ext_track.id)

    # Tombstone the track via the admin delete endpoint and confirm it disappears.
    delete_response = client.request(
        "DELETE",
        f"/api/v1/admin/external-libraries/{library_id}/tracks/{first_ext_track.id}",
        json={"delete_source": False},
        headers=headers,
    )
    assert delete_response.status_code == status.HTTP_204_NO_CONTENT

    tracks_response = client.get("/api/v1/tracks", headers=headers)
    assert tracks_response.status_code == status.HTTP_200_OK
    assert len(tracks_response.json()) == 1

    # Restore the track and confirm it reappears.
    restore_response = client.post(
        f"/api/v1/admin/external-libraries/{library_id}/tracks/{first_ext_track.id}/restore",
        headers=headers,
    )
    assert restore_response.status_code == status.HTTP_200_OK
    assert restore_response.json()["state"] == "active"

    tracks_response = client.get("/api/v1/tracks", headers=headers)
    assert tracks_response.status_code == status.HTTP_200_OK
    assert len(tracks_response.json()) == 2

    # Upload a file whose audio hash matches the second external item.
    upload_hash = "a" * 64
    monkeypatch.setattr(
        "songhive.services.import_.audio_hash",
        AsyncMock(return_value=upload_hash),
    )
    monkeypatch.setattr(
        "songhive.services.import_.extract_metadata",
        lambda _path: AudioMetadata(
            title="Uploaded Track",
            artist="Uploaded Artist",
            album="Uploaded Album",
            mimetype="audio/mpeg",
            duration=123.0,
        ),
    )

    upload_response = client.post(
        "/api/v1/files/upload",
        files={"file": ("match.mp3", b"does-not-matter", "audio/mpeg")},
        headers=headers,
    )
    assert upload_response.status_code == status.HTTP_409_CONFLICT
    warning = upload_response.json()
    assert warning["sha256"] == upload_hash
    assert warning["provider_type"] == "fake"
    assert warning["token"]
    assert len(warning["display_info"]) == 1
    assert warning["display_info"][0]["external_library_id"] == library_id

    # Resolve with keep_local and assert the local track is preferred.
    resolve_response = client.post(
        "/api/v1/files/upload/resolve-duplicate",
        json={"token": warning["token"], "action": "keep_local"},
        headers=headers,
    )
    assert resolve_response.status_code == status.HTTP_200_OK
    new_track_id = resolve_response.headers["X-Track-Id"]
    assert new_track_id != str(first_track.id)

    new_track = await db_session.get(Track, new_track_id)
    assert new_track is not None
    assert new_track.audio_file_id is not None
    assert new_track.source != "external"

    local_track_response = client.get(f"/api/v1/tracks/{new_track_id}", headers=headers)
    assert local_track_response.status_code == status.HTTP_200_OK
    assert local_track_response.json()["is_external"] is False

    # Audit log captured the sync action.
    audit_result = await db_session.execute(select(AuditLog).where(AuditLog.action == "external_library.admin_sync"))
    assert audit_result.scalar_one_or_none() is not None
