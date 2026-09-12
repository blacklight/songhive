"""
Email Celery tasks: queue verification, password-reset, and notification messages.
"""

import asyncio
import logging

from sqlalchemy import select

from ..config import load_config
from ..models.base import dispose_and_reset, get_session, init_db
from ..models.notification import Notification
from ..models.user import User
from ..services import email as email_service
from ..services.email import EmailNotConfiguredError
from ..services.notifications import notification_to_dict
from .celery import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="songhive.tasks.email.send_verification_email")
def send_verification_email(to_address: str, username: str, verification_url: str) -> bool:
    """Send a verification email for a newly registered account."""
    config = load_config([])
    try:
        return email_service.send_verification_email(config, to_address, username, verification_url)
    except EmailNotConfiguredError as exc:
        logger.warning("Email not queued for verification: %s", exc)
        return False


@celery_app.task(name="songhive.tasks.email.send_password_reset_email")
def send_password_reset_email(to_address: str, username: str, token: str) -> bool:
    """Send a password-reset email containing a single-use token."""
    config = load_config([])
    try:
        return email_service.send_password_reset_email(config, to_address, username, token)
    except EmailNotConfiguredError as exc:
        logger.warning("Email not queued for password reset: %s", exc)
        return False


async def _send_notification_email(config, notification_id: str) -> bool:
    """Fetch the notification and recipient, then send the individual email."""
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Notification, User)
                .join(User, Notification.user_id == User.id)
                .where(Notification.id == notification_id)
            )
            row = result.first()
            if row is None:
                logger.warning("Notification %s no longer exists; skipping email", notification_id)
                return False
            notification, user = row
            if not user.email:
                logger.warning("User %s has no email address; skipping notification email", user.id)
                return False
            return email_service.send_notification_email(
                config,
                user.email,
                user.username,
                notification_to_dict(notification),
            )
    finally:
        await dispose_and_reset()


@celery_app.task(name="songhive.tasks.email.send_notification_email")
def send_notification_email(notification_id: str) -> bool:
    """Send the individual email for a single notification."""
    config = load_config([])
    init_db(config.database.url)
    try:
        return asyncio.run(_send_notification_email(config, notification_id))
    except EmailNotConfiguredError as exc:
        logger.warning("Email not queued for notification: %s", exc)
        return False
