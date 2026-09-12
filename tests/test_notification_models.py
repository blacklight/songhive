"""
Notification model tests - instantiation, persistence, and constraints.
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from songhive.models.notification import (
    NOTIFICATION_TYPES,
    Notification,
    NotificationPreference,
    NotificationType,
)


def test_notification_type_enum_values():
    """NotificationType exposes the seven expected string values."""
    assert [t.value for t in NotificationType] == [
        "follow",
        "like",
        "boost",
        "quote",
        "reply",
        "mention",
        "share",
    ]
    assert NOTIFICATION_TYPES == tuple(t.value for t in NotificationType)


@pytest.mark.asyncio
async def test_notification_round_trip(db_session, regular_user):
    """A valid Notification row persists and reads back."""
    notification = Notification(
        user_id=regular_user.id,
        type="like",
        actor_url="https://remote.example/users/bob",
        source_url="https://remote.example/objects/1",
        payload={"actor_name": "bob"},
        delivered_targets=["in_app"],
    )
    db_session.add(notification)
    await db_session.commit()

    row = await db_session.get(Notification, notification.id)
    assert row is not None
    assert row.user_id == regular_user.id
    assert row.type == "like"
    assert row.actor_url == "https://remote.example/users/bob"
    assert row.source_url == "https://remote.example/objects/1"
    assert row.payload == {"actor_name": "bob"}
    assert row.delivered_targets == ["in_app"]
    assert row.seen_at is None
    assert row.digest_sent_at is None


@pytest.mark.asyncio
async def test_notification_rejects_unknown_type(db_session, regular_user):
    """Assigning an unknown type raises ValueError via @validates."""
    with pytest.raises(ValueError):
        Notification(user_id=regular_user.id, type="poke")


@pytest.mark.asyncio
async def test_notification_preference_defaults(db_session, regular_user):
    """Preference rows default to in-app only."""
    pref = NotificationPreference(user_id=regular_user.id, type="follow")
    db_session.add(pref)
    await db_session.commit()

    row = await db_session.get(NotificationPreference, pref.id)
    assert row is not None
    assert row.in_app is True
    assert row.email is False
    assert row.email_digest is False


@pytest.mark.asyncio
async def test_notification_preference_unique_user_type(db_session, regular_user):
    """Only one preference row may exist per (user_id, type)."""
    db_session.add(NotificationPreference(user_id=regular_user.id, type="like"))
    db_session.add(NotificationPreference(user_id=regular_user.id, type="like"))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_notifications_removed_with_user(db_session, regular_user):
    """Deleting a user removes their notifications and preferences."""
    from songhive.users import manager as user_manager

    db_session.add(Notification(user_id=regular_user.id, type="follow"))
    db_session.add(NotificationPreference(user_id=regular_user.id, type="follow"))
    await db_session.commit()

    await user_manager.delete_user(db_session, regular_user.id)
    await db_session.commit()

    remaining = await db_session.execute(sa.select(Notification).where(Notification.user_id == regular_user.id))
    assert remaining.scalars().all() == []
    prefs = await db_session.execute(
        sa.select(NotificationPreference).where(NotificationPreference.user_id == regular_user.id)
    )
    assert prefs.scalars().all() == []
