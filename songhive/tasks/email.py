"""
Email Celery tasks: queue verification and password-reset messages.
"""

import logging

from ..config import load_config
from ..services import email as email_service
from ..services.email import EmailNotConfiguredError
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
