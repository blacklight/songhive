"""
Email sending support for Songhive.

Provides synchronous helpers to send plain-text emails over SMTP, plus
convenience functions for verification and password-reset messages.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate
from typing import Optional

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
    msg["Date"] = formatdate(localtime=True)
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


def send_verification_email(
    config: SonghiveConfig,
    to_address: str,
    username: str,
    verification_url: str,
) -> bool:
    """Send an email containing a link to verify the email address."""
    subject = "Verify your Songhive account"
    body = (
        f"Hi {username},\n\n"
        "Please verify your Songhive account by opening the following link:\n\n"
        f"{verification_url}\n\n"
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


def send_purchase_redeem_email(
    config: SonghiveConfig,
    to_address: str,
    redeem_url: str,
    title: str,
    expires_days: int,
) -> bool:
    """Send a guest buyer their download link for a completed purchase."""
    subject = "Your Songhive purchase is ready"
    body = (
        "Hi,\n\n"
        f"Thank you for your purchase of {title}. "
        "Your download link is ready:\n\n"
        f"{redeem_url}\n\n"
        f"This link stays valid for {expires_days} days and can be used a "
        "limited number of times. If you did not make this purchase, you can "
        "ignore this email."
    )
    return send_email(config, to_address, subject, body)


def _absolute_url(config: SonghiveConfig, url: str) -> str:
    """Resolve a relative SPA path (e.g. ``/settings/billing``) to a public URL."""
    if not url or url.startswith(("http://", "https://")):
        return url
    from .payments import public_base_url

    base = public_base_url(config)
    return f"{base}{url}" if base else url


_MEMBERSHIP_EVENT_TEXT = {
    "membership_paid": ("Membership payment confirmed", "Your membership payment was confirmed — thank you."),
    "membership_payment_failed": (
        "Membership payment failed",
        "A payment for your membership failed. Please update your payment " "method to keep your account active.",
    ),
    "membership_cancel_scheduled": (
        "Membership cancellation scheduled",
        "Your membership cancellation is scheduled — you keep access until " "the end of the paid period.",
    ),
    "membership_canceled": ("Membership ended", "Your membership has ended."),
}


def _system_notification_text(notification: dict) -> Optional[tuple[str, str]]:
    """
    Subject suffix and body line for actor-less system notifications
    (``purchase``, ``membership``), or ``None`` for regular actor notifications.
    """
    notification_type = notification.get("type")
    payload = notification.get("payload") or {}
    if notification_type == "purchase":
        count = payload.get("track_count")
        noun = "track" if count == 1 else "tracks"
        detail = f" — {count} {noun} added" if count else ""
        return "Purchase confirmed", f"Thank you for your purchase{detail}; it's now in your library."
    if notification_type == "membership":
        return _MEMBERSHIP_EVENT_TEXT.get(
            str(payload.get("event") or ""),
            ("Membership update", "Your membership status changed."),
        )
    return None


def send_notification_email(
    config: SonghiveConfig,
    to_address: str,
    username: str,
    notification: dict,
) -> bool:
    """Send a plain-text email for a single notification."""
    source_url = _absolute_url(config, notification.get("source_url") or "")
    system = _system_notification_text(notification)
    if system is not None:
        subject_suffix, body_line = system
        subject = f"[Songhive] {subject_suffix}"
        lines = [f"Hi {username},", "", body_line]
    else:
        payload = notification.get("payload") or {}
        actor_name = payload.get("actor_name") or notification.get("actor_url") or "someone"
        notification_type = notification.get("type") or "notification"
        subject = f"[Songhive] New {notification_type} from {actor_name}"
        lines = [
            f"Hi {username},",
            "",
            f"You have a new {notification_type} notification from {actor_name}.",
        ]
    if source_url:
        lines += ["", f"View it here: {source_url}"]
    lines += ["", "You can manage your notification preferences in your profile settings."]
    return send_email(config, to_address, subject, "\n".join(lines))


def send_notification_digest_email(
    config: SonghiveConfig,
    to_address: str,
    username: str,
    notifications: list,
) -> bool:
    """Send a daily digest email listing the given notifications, grouped by type."""
    subject = "[Songhive] Your daily notifications summary"
    lines = [
        f"Hi {username},",
        "",
        "Here is a summary of your recent Songhive notifications:",
        "",
    ]

    grouped: dict = {}
    for notification in notifications:
        grouped.setdefault(notification.get("type") or "notification", []).append(notification)

    for notification_type in sorted(grouped):
        lines.append(f"{notification_type}:")
        for notification in grouped[notification_type]:
            system = _system_notification_text(notification)
            if system is not None:
                entry = system[1]
            else:
                payload = notification.get("payload") or {}
                actor_name = payload.get("actor_name") or notification.get("actor_url") or "someone"
                entry = actor_name
            source_url = _absolute_url(config, notification.get("source_url") or "")
            line = f"  - {entry}"
            if source_url:
                line += f" — {source_url}"
            lines.append(line)
        lines.append("")

    lines.append("You can manage your notification preferences in your profile settings.")
    return send_email(config, to_address, subject, "\n".join(lines))
