"""Audit-log coverage for external-library admin actions."""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import status
from sqlalchemy import select

from songhive.api.routes.external_libraries import _redact_audit_details
from songhive.models.audit_log import AuditLog
from songhive.models.external_track import ExternalTrack


def test_redact_audit_details_redacts_secrets_and_preserves_provider_key():
    """_redact_audit_details redacts secret-like keys and keeps provider_key."""
    details = {
        "external_library_id": "lib-1",
        "delete_source": False,
        "secret_key": "super-secret",
    }
    redacted = _redact_audit_details(details, "track1.flac")
    assert redacted["secret_key"] == "<redacted>"
    assert redacted["provider_key"] == "track1.flac"
    assert redacted["external_library_id"] == "lib-1"
    assert redacted["delete_source"] is False


@pytest.mark.asyncio
async def test_external_library_audit_log_rows(
    client,
    admin_user,
    auth_headers,
    db_session,
    fake_redis,
    synced_external_library,
    monkeypatch,
):
    """Admin create, sync, update, tombstone, restore, and delete all emit audit rows."""
    library, tracks = synced_external_library
    headers = auth_headers(admin_user)
    library_id = str(library.id)

    monkeypatch.setattr(
        "songhive.api.routes.admin_external_libraries.sync_external_library_task.delay",
        MagicMock(),
    )

    # Manual sync through the API (real sync already ran via the fixture).
    sync_response = client.post(
        f"/api/v1/admin/external-libraries/{library_id}/sync",
        json={"include_tombstones": False},
        headers=headers,
    )
    assert sync_response.status_code == status.HTTP_202_ACCEPTED

    # Update the library config with a new secret.
    new_config = {
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
        },
        "secret_key": "updated-secret",
    }
    patch_response = client.patch(
        f"/api/v1/admin/external-libraries/{library_id}",
        json={
            "config": new_config,
            "sync_interval_seconds": 120,
        },
        headers=headers,
    )
    assert patch_response.status_code == status.HTTP_200_OK
    assert patch_response.json()["config"].get("secret_key") == "<redacted>"

    # Locate the external track for the first Songhive track.
    ext_track_result = await db_session.execute(
        select(ExternalTrack).where(ExternalTrack.track_id == str(tracks[0].id))
    )
    ext_track = ext_track_result.scalar_one()
    ext_track_id = str(ext_track.id)

    # Tombstone and then restore the track.
    tombstone_response = client.request(
        "DELETE",
        f"/api/v1/admin/external-libraries/{library_id}/tracks/{ext_track_id}",
        json={"delete_source": False},
        headers=headers,
    )
    assert tombstone_response.status_code == status.HTTP_204_NO_CONTENT

    restore_response = client.post(
        f"/api/v1/admin/external-libraries/{library_id}/tracks/{ext_track_id}/restore",
        headers=headers,
    )
    assert restore_response.status_code == status.HTTP_200_OK

    # Delete the library.
    delete_response = client.delete(
        f"/api/v1/admin/external-libraries/{library_id}",
        headers=headers,
    )
    assert delete_response.status_code == status.HTTP_204_NO_CONTENT

    # Verify all expected library-level audit rows.
    library_logs = list(
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.target_id == library_id).order_by(AuditLog.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    library_actions = [log.action for log in library_logs]
    assert "external_library.admin_create" in library_actions
    assert "external_library.admin_sync" in library_actions
    assert "external_library.admin_update" in library_actions
    assert "external_library.admin_delete" in library_actions
    for log in library_logs:
        assert log.actor_id == str(admin_user.id)
        assert log.target_type == "external_library"
        assert "secret_key" not in json.dumps(log.details or {})

    update_log = next(log for log in library_logs if log.action == "external_library.admin_update")
    assert update_log.details.get("config_changed") is True
    assert update_log.details.get("sync_interval_seconds") == 120

    sync_log = next(log for log in library_logs if log.action == "external_library.admin_sync")
    assert sync_log.details.get("include_tombstones") is False

    # Verify track-level audit rows.
    track_logs = list(
        (await db_session.execute(select(AuditLog).where(AuditLog.target_id == ext_track_id))).scalars().all()
    )
    track_actions = [log.action for log in track_logs]
    assert "external_track.admin_tombstone" in track_actions
    assert "external_track.admin_restore" in track_actions
    for log in track_logs:
        assert log.actor_id == str(admin_user.id)
        assert log.target_type == "external_track"
        assert "secret_key" not in json.dumps(log.details or {})
