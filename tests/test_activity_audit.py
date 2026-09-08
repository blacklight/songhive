"""
Audit-log tests for mutating activity routes.
"""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.activity import Activity
from songhive.models.artist import Artist
from songhive.models.audit_log import AuditLog
from songhive.models.track import Track
from songhive.models.user import User


async def _make_artist(session, name: str = "Test Artist") -> Artist:
    """Create and persist a test artist."""
    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _make_track(session, owner: User | None, visibility: str = Visibility.PUBLIC.value) -> Track:
    """Create and persist a test track."""
    artist = await _make_artist(session)
    track = Track(
        title="Test Track",
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(track)
    await session.flush()
    return track


def _make_activity(**overrides) -> Activity:
    """Build a minimally valid local activity."""
    params = {
        "entity_type": "track",
        "activity_type": "create",
        "source_type": "local",
        "source_actor": "https://local.example/users/alice",
        "source_id": "https://local.example/users/alice/objects/1",
        "visibility": Visibility.PUBLIC.value,
    }
    params.update(overrides)
    return Activity(**params)


@pytest.mark.asyncio
async def test_update_activity_logs_audit(client, db_session, regular_user, other_user, auth_headers):
    """PATCH /activities/{id} writes an activity.update audit row."""
    client.app.state.config.federation.instance_domain = "local.example"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        entity_id=str(track.id),
        owner_user_id=regular_user.id,
    )
    db_session.add(activity)
    await db_session.flush()

    resp = client.patch(
        f"/api/v1/activities/{activity.id}",
        json={"content": "edited", "visibility": "local"},
        headers=auth_headers(regular_user),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["visibility"] == Visibility.LOCAL.value

    log = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "activity.update",
            AuditLog.target_id == str(activity.id),
        )
    )
    assert log is not None
    assert str(log.actor_id) == str(regular_user.id)
    assert log.target_type == "activity"
    assert log.details["entity_type"] == "track"
    assert log.details["entity_id"] == str(track.id)
    assert log.details["visibility"] == {"old": Visibility.PUBLIC.value, "new": Visibility.LOCAL.value}
    assert log.details["content_changed"] is True


@pytest.mark.asyncio
async def test_delete_activity_logs_audit(client, db_session, regular_user, auth_headers, monkeypatch):
    """DELETE /activities/{id} writes an activity.delete audit row."""
    track = await _make_track(db_session, regular_user)
    regular_user.private_key_pem = "private"
    regular_user.actor_url = "https://local.example/users/regular"
    activity = _make_activity(
        entity_id=str(track.id),
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        source_id=f"{regular_user.actor_url}/objects/pub-1",
    )
    db_session.add(activity)
    await db_session.flush()

    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", MagicMock())

    resp = client.delete(f"/api/v1/activities/{activity.id}", headers=auth_headers(regular_user))

    assert resp.status_code == 200

    log = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "activity.delete",
            AuditLog.target_id == str(activity.id),
        )
    )
    assert log is not None
    assert str(log.actor_id) == str(regular_user.id)
    assert log.target_type == "activity"
    assert log.details["entity_type"] == "track"
    assert log.details["entity_id"] == str(track.id)
    assert log.details["visibility"] == Visibility.PUBLIC.value


@pytest.mark.asyncio
async def test_like_activity_logs_audit(client, db_session, regular_user, other_user, auth_headers):
    """POST /activities/{id}/like writes an activity.like audit row."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        entity_id=str(track.id),
        owner_user_id=other_user.id,
    )
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(
        f"/api/v1/activities/{activity.id}/like",
        headers=auth_headers(regular_user),
    )

    assert resp.status_code == 201

    log = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "activity.like",
            AuditLog.target_id == str(activity.id),
        )
    )
    assert log is not None
    assert str(log.actor_id) == str(regular_user.id)
    assert log.target_type == "activity"
    assert log.details["entity_type"] == "track"
    assert log.details["entity_id"] == str(track.id)
    assert log.details["visibility"] == Visibility.PUBLIC.value
