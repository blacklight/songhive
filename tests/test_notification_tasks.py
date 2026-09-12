"""
Tests for the notification Celery tasks (digest, purge) and beat schedule.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from songhive.models.notification import Notification, NotificationPreference
from songhive.tasks import notifications as notification_tasks
from songhive.tasks.celery import celery_app, make_celery


async def _seed_notification(db_session, user, type="like", source_url="/x"):
    notification = Notification(
        user_id=user.id,
        type=type,
        actor_url="https://remote.example/users/bob",
        source_url=source_url,
        payload={"actor_name": "bob"},
        delivered_targets=["email_digest"],
    )
    db_session.add(notification)
    await db_session.commit()
    return notification


@pytest.mark.asyncio
async def test_digest_sends_one_email_per_eligible_user(
    engine, db_session, regular_user, other_user, monkeypatch, config
):
    """Each user with digest-enabled prefs and unsent rows gets one email."""
    from songhive.models.base import init_db

    init_db(engine=engine, force=True)

    for user in (regular_user, other_user):
        db_session.add(NotificationPreference(user_id=user.id, type="like", email_digest=True, in_app=True))
    n1 = await _seed_notification(db_session, regular_user, "like", "/a")
    n2 = await _seed_notification(db_session, regular_user, "like", "/b")
    n3 = await _seed_notification(db_session, other_user, "like", "/c")
    # A type without digest enabled is not covered.
    n4 = await _seed_notification(db_session, regular_user, "follow", "/d")

    sent = MagicMock(return_value=True)
    monkeypatch.setattr(
        "songhive.tasks.notifications.email_service.send_notification_digest_email",
        sent,
    )

    count = await notification_tasks._send_notification_digests(config)

    assert count == 2
    assert sent.call_count == 2
    calls = {c.args[1]: c.args[3] for c in sent.call_args_list}
    regular_items = calls[regular_user.email]
    assert len(regular_items) == 2

    for row in (n1, n2, n3):
        await db_session.refresh(row)
        assert row.digest_sent_at is not None
    await db_session.refresh(n4)
    assert n4.digest_sent_at is None


@pytest.mark.asyncio
async def test_digest_skips_unverified_and_already_sent(
    engine, db_session, unverified_user, regular_user, monkeypatch, config
):
    """Unverified users are skipped; already-stamped rows are not re-sent."""
    from songhive.models.base import init_db

    init_db(engine=engine, force=True)

    db_session.add(NotificationPreference(user_id=unverified_user.id, type="like", email_digest=True))
    await _seed_notification(db_session, unverified_user, "like", "/uv")

    db_session.add(NotificationPreference(user_id=regular_user.id, type="like", email_digest=True))
    done = await _seed_notification(db_session, regular_user, "like", "/done")
    done.digest_sent_at = datetime.now(timezone.utc)
    pending = await _seed_notification(db_session, regular_user, "like", "/pending")
    await db_session.commit()

    sent = MagicMock(return_value=True)
    monkeypatch.setattr(
        "songhive.tasks.notifications.email_service.send_notification_digest_email",
        sent,
    )

    count = await notification_tasks._send_notification_digests(config)

    assert count == 1
    assert sent.call_count == 1
    call = sent.call_args_list[0]
    assert call.args[1] == regular_user.email
    assert [n["id"] for n in call.args[3]] == [pending.id]

    await db_session.refresh(pending)
    assert pending.digest_sent_at is not None


@pytest.mark.asyncio
async def test_digest_failed_send_leaves_rows_unstamped(engine, db_session, regular_user, monkeypatch, config):
    """A send_email False result leaves digest_sent_at NULL for retry."""
    from songhive.models.base import init_db

    init_db(engine=engine, force=True)

    db_session.add(NotificationPreference(user_id=regular_user.id, type="like", email_digest=True))
    row = await _seed_notification(db_session, regular_user, "like", "/x")

    sent = MagicMock(return_value=False)
    monkeypatch.setattr(
        "songhive.tasks.notifications.email_service.send_notification_digest_email",
        sent,
    )

    count = await notification_tasks._send_notification_digests(config)

    assert count == 0
    await db_session.refresh(row)
    assert row.digest_sent_at is None


@pytest.mark.asyncio
async def test_digest_skips_when_smtp_not_configured(engine, db_session, regular_user, monkeypatch, config):
    """EmailNotConfiguredError is logged and nothing is stamped."""
    from songhive.models.base import init_db
    from songhive.services.email import EmailNotConfiguredError

    init_db(engine=engine, force=True)

    db_session.add(NotificationPreference(user_id=regular_user.id, type="like", email_digest=True))
    row = await _seed_notification(db_session, regular_user, "like", "/x")

    sent = MagicMock(side_effect=EmailNotConfiguredError("no smtp"))
    monkeypatch.setattr(
        "songhive.tasks.notifications.email_service.send_notification_digest_email",
        sent,
    )

    count = await notification_tasks._send_notification_digests(config)

    assert count == 0
    await db_session.refresh(row)
    assert row.digest_sent_at is None


@pytest.mark.asyncio
async def test_purge_task_deletes_only_old_seen(engine, db_session, regular_user, config):
    """The purge task deletes seen rows past retention and keeps the rest."""
    from songhive.models.base import init_db

    init_db(engine=engine, force=True)

    old_seen = await _seed_notification(db_session, regular_user, "like", "/old")
    new_seen = await _seed_notification(db_session, regular_user, "like", "/new")
    unseen = await _seed_notification(db_session, regular_user, "follow", "/unseen")
    now = datetime.now(timezone.utc)
    old_seen.seen_at = now - timedelta(days=config.notifications.retention_days + 10)
    new_seen.seen_at = now - timedelta(days=1)
    unseen.created_at = now - timedelta(days=config.notifications.retention_days + 10)
    await db_session.commit()

    deleted = await notification_tasks._purge_old_notifications(config)

    assert deleted == 1
    remaining = await db_session.execute(sa.select(Notification.id))
    assert {r[0] for r in remaining.all()} == {new_seen.id, unseen.id}


def test_beat_schedule_includes_notification_entries():
    """The beat schedule registers the digest and purge tasks."""
    schedule = celery_app.conf.beat_schedule
    assert "send-notification-digests" in schedule
    assert "purge-old-notifications" in schedule
    assert schedule["send-notification-digests"]["task"] == "songhive.tasks.notifications.send_notification_digests"
    assert schedule["purge-old-notifications"]["task"] == "songhive.tasks.notifications.purge_old_notifications"
    assert schedule["purge-old-notifications"]["schedule"].hour == {3}
    assert schedule["send-notification-digests"]["schedule"].hour == {8}


def test_make_celery_custom_notification_hours():
    """make_celery honors configured digest/purge hours."""
    app = make_celery(notification_digest_hour=6, notification_purge_hour=22)
    digest = app.conf.beat_schedule["send-notification-digests"]["schedule"]
    purge = app.conf.beat_schedule["purge-old-notifications"]["schedule"]
    assert digest.hour == {6}
    assert purge.hour == {22}
    assert digest.minute == {0}
    assert purge.minute == {0}


@pytest.mark.asyncio
async def test_send_notification_email_task(engine, db_session, regular_user, monkeypatch, config):
    """The individual email task loads the notification and delegates."""
    from songhive.models.base import init_db
    from songhive.tasks import email as email_tasks

    init_db(engine=engine, force=True)
    notification = await _seed_notification(db_session, regular_user, "like", "/x")

    send = MagicMock(return_value=True)
    monkeypatch.setattr(email_tasks.email_service, "send_notification_email", send)

    result = await email_tasks._send_notification_email(config, notification.id)

    assert result is True
    send.assert_called_once()
    assert send.call_args.args[1] == regular_user.email
    assert send.call_args.args[3]["id"] == notification.id


@pytest.mark.asyncio
async def test_send_notification_email_task_missing_row(engine, db_session, regular_user, monkeypatch, config):
    """The individual email task returns False for a missing notification."""
    from songhive.models.base import init_db
    from songhive.tasks import email as email_tasks

    init_db(engine=engine, force=True)

    send = MagicMock(return_value=True)
    monkeypatch.setattr(email_tasks.email_service, "send_notification_email", send)

    result = await email_tasks._send_notification_email(config, "missing-id")

    assert result is False
    send.assert_not_called()


def test_notifications_config_defaults(config):
    """The notifications config section exposes the documented defaults."""
    assert config.notifications.retention_days == 90
    assert config.notifications.purge_hour == 3
    assert config.notifications.digest_hour == 8


def test_notifications_config_from_env(monkeypatch):
    """Notifications settings are overridable via SONGHIVE_NOTIFICATIONS__*."""
    from songhive.config.schema import SonghiveConfig

    monkeypatch.setenv("SONGHIVE_NOTIFICATIONS__RETENTION_DAYS", "30")
    monkeypatch.setenv("SONGHIVE_NOTIFICATIONS__PURGE_HOUR", "5")
    monkeypatch.setenv("SONGHIVE_NOTIFICATIONS__DIGEST_HOUR", "9")
    config = SonghiveConfig()
    assert config.notifications.retention_days == 30
    assert config.notifications.purge_hour == 5
    assert config.notifications.digest_hour == 9


@pytest.mark.asyncio
async def test_cli_purge_notifications(db_session, regular_user, monkeypatch, capsys, config):
    """`songhive admin purge-notifications` prints the deleted count."""
    from contextlib import asynccontextmanager

    from songhive.cli import admin as cli_admin

    @asynccontextmanager
    async def _fake_session():
        yield db_session

    monkeypatch.setattr(cli_admin, "get_session", lambda: _fake_session())
    monkeypatch.setattr(cli_admin, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cli_admin, "init_db", lambda *a, **k: None)

    old = await _seed_notification(db_session, regular_user, "like", "/old")
    old.seen_at = datetime.now(timezone.utc) - timedelta(days=365)
    await db_session.commit()

    await cli_admin._handle_purge_notifications(SimpleNamespace())

    captured = capsys.readouterr()
    assert "Deleted 1 notification(s)" in captured.out
    remaining = await db_session.execute(sa.select(Notification.id))
    assert remaining.all() == []
