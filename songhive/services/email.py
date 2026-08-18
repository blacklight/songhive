"""
Email sending support for Songhive.

Provides synchronous helpers to send plain-text emails over SMTP, plus
convenience functions for verification and password-reset messages.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from ..config.schema import SonghiveConfig

logger = logging.getLogger(__name__)


class EmailNotConfiguredError(RuntimeError):
    """Raised when email cannot be sent because SMTP is not configured."""


def send_email(config: SonghiveConfig, to_address: str, subject: str, body: str) -> bool:
    """
    Send a plain-text email using the configured SMTP relay.

    :returns: ``True`` if the message was accepted by the SMTP server,
        ``False`` if sending failed.
    :raises EmailNotConfiguredError: if ``smtp_host`` or ``from_address`` is missing.
    """
    smtp_host = config.email.smtp_host
    smtp_port = config.email.smtp_port
    smtp_username = config.email.smtp_username
    smtp_password = config.email.smtp_password
    smtp_tls = config.email.smtp_tls
    from_address = config.email.from_address

    if not smtp_host or not from_address:
        raise EmailNotConfiguredError("email.smtp_host and email.from_address must be configured")

    msg = EmailMessage()
    msg["From"] = from_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(body)

    use_ssl = smtp_tls and smtp_port == 465
    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP

    try:
        with smtp_class(smtp_host, smtp_port) as smtp:
            if smtp_tls and not use_ssl:
                context = ssl.create_default_context()
                smtp.starttls(context=context)
            if smtp_username and smtp_password:
                smtp.login(smtp_username, smtp_password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError):
        logger.exception("Failed to send email to %s", to_address)
        return False

    logger.info("Email sent to %s", to_address)
    return True


def send_verification_email(config: SonghiveConfig, to_address: str, username: str, token: str) -> bool:
    """Send an email containing a raw email-verification token."""
    subject = "Verify your Songhive account"
    body = (
        f"Hi {username},\n\n"
        "Please verify your Songhive account by using the following token "
        "with the /api/v1/auth/verify-email endpoint:\n\n"
        f"{token}\n\n"
        "If you did not sign up for Songhive, you can ignore this email."
    )
    return send_email(config, to_address, subject, body)


def send_password_reset_email(config: SonghiveConfig, to_address: str, username: str, token: str) -> bool:
    """Send an email containing a raw password-reset token."""
    subject = "Reset your Songhive password"
    expiry_minutes = config.auth.password_reset_token_expiry_minutes
    body = (
        f"Hi {username},\n\n"
        "A password reset was requested for your Songhive account. "
        "Use the following token with the /api/v1/auth/password-reset/confirm "
        "endpoint to set a new password:\n\n"
        f"{token}\n\n"
        f"This token expires in {expiry_minutes} minutes.\n\n"
        "If you did not request this reset, you can ignore this email."
    )
    return send_email(config, to_address, subject, body)
