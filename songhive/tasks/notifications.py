"""
Notification Celery tasks: daily digest emails and the nightly retention purge.

``send_notification_digests`` emails each eligible user one plain-text digest
covering their unsent, digest-enabled notifications and stamps
``digest_sent_at`` on the covered rows.  When SMTP is not configured
(``EmailNotConfiguredError``) nothing is stamped, so the rows are retried on
the next run once email is configured.  When ``send_email`` returns ``False``
(SMTP accepted-failed) the rows are likewise left unstamped so the next run
retries them.

``purge_old_notifications`` deletes seen notifications older than
``notifications.retention_days``; unseen notifications are never deleted.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import load_config
from ..models.base import dispose_and_reset, get_session, init_db
from ..models.notification import Notification, NotificationPreference
from ..models.user import User
from ..services import email as email_service
from ..services import notifications as notifications_service
from ..services.email import EmailNotConfiguredError
from .celery import celery_app

logger = logging.getLogger(__name__)


async def _load_digest_recipients(session: AsyncSession) -> List[User]:
    """Return verified users that have at least one digest-enabled type."""
    result = await session.execute(
        select(User)
        .join(NotificationPreference, NotificationPreference.user_id == User.id)
        .where(
            NotificationPreference.email_digest.is_(True),
            User.email_verified.is_(True),
        )
        .distinct()
    )
    return list(result.scalars().all())


async def _send_user_digest(session: AsyncSession, config, user: User) -> bool:
    """Send one user's digest and stamp the covered rows. Returns True on send."""
    prefs = await session.execute(
        select(NotificationPreference.type).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.email_digest.is_(True),
        )
    )
    digest_types = [row[0] for row in prefs.all()]
    if not digest_types:
        return False

    result = await session.execute(
        select(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.type.in_(digest_types),
            Notification.digest_sent_at.is_(None),
        )
        .order_by(Notification.created_at)
    )
    rows = list(result.scalars().all())
    if not rows:
        return False

    sent = email_service.send_notification_digest_email(
        config,
        user.email,
        user.username,
        [notifications_service.notification_to_dict(row) for row in rows],
    )
    if not sent:
        # SMTP accepted-failed: leave digest_sent_at NULL so the next run retries.
        return False

    now = datetime.now(timezone.utc)
    for row in rows:
        row.digest_sent_at = now
    await session.commit()
    return True


async def _send_notification_digests(config) -> int:
    """Send one digest email per eligible user; returns the number sent."""
    sent_count = 0
    try:
        async with get_session() as session:
            users = await _load_digest_recipients(session)
            for user in users:
                if not user.email:
                    continue
                try:
                    if await _send_user_digest(session, config, user):
                        sent_count += 1
                except EmailNotConfiguredError:
                    logger.warning("SMTP is not configured; skipping notification digests this run")
                    break
                except Exception as exc:  # one user's failure must not roll back others
                    logger.exception("Failed to send notification digest to %s: %s", user.id, exc)
                    await session.rollback()
    finally:
        await dispose_and_reset()
    return sent_count


@celery_app.task(name="songhive.tasks.notifications.send_notification_digests")
def send_notification_digests() -> int:
    """Send the daily notification digest email to each eligible user."""
    config = load_config([])
    init_db(config.database.url)
    return asyncio.run(_send_notification_digests(config))


async def _purge_old_notifications(config) -> int:
    """Run the seen-notification retention purge."""
    try:
        async with get_session() as session:
            return await notifications_service.purge_seen_notifications(
                session,
                older_than_days=config.notifications.retention_days,
            )
    finally:
        await dispose_and_reset()


@celery_app.task(name="songhive.tasks.notifications.purge_old_notifications")
def purge_old_notifications() -> int:
    """Delete seen notifications older than the configured retention window."""
    config = load_config([])
    init_db(config.database.url)
    return asyncio.run(_purge_old_notifications(config))
