"""
Tests for the email sending infrastructure.
"""

import logging
import smtplib

import pytest

from songhive.config.schema import SonghiveConfig
from songhive.services.email import (
    EmailNotConfiguredError,
    send_email,
    send_password_reset_email,
    send_verification_email,
)
from songhive.tasks.email import send_password_reset_email as send_password_reset_email_task
from songhive.tasks.email import send_verification_email as send_verification_email_task


@pytest.fixture
def fake_smtp(monkeypatch):
    """Replace smtplib.SMTP/SMTP_SSL with a recorder that captures sent messages."""

    class _FakeSMTP:
        instances = []

        def __init__(self, host, port):
            self.host = host
            self.port = port
            self.starttls_called = False
            self.starttls_context = None
            self.login_called = None
            self.sent = []
            _FakeSMTP.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context=None):
            self.starttls_called = True
            self.starttls_context = context

        def login(self, user, password):
            self.login_called = (user, password)

        def send_message(self, msg):
            self.sent.append(msg)

    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


@pytest.fixture
def email_config():
    """Return a configuration with a working email section."""
    return SonghiveConfig(
        auth={"secret_key": "a" * 32},
        federation={"enabled": False},
        email={
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_username": "user",
            "smtp_password": "secret",
            "smtp_tls": True,
            "from_address": "songhive@example.com",
        },
    )


@pytest.fixture
def email_config_no_auth():
    """Return an email configuration without credentials and with TLS disabled."""
    return SonghiveConfig(
        auth={"secret_key": "a" * 32},
        federation={"enabled": False},
        email={
            "smtp_host": "smtp.example.com",
            "smtp_port": 25,
            "smtp_username": None,
            "smtp_password": None,
            "smtp_tls": False,
            "from_address": "songhive@example.com",
        },
    )


def test_send_email_raises_when_not_configured():
    """A missing smtp_host or from_address raises a clear configuration error."""
    config = SonghiveConfig(auth={"secret_key": "a" * 32}, federation={"enabled": False})
    with pytest.raises(EmailNotConfiguredError):
        send_email(config, "user@example.com", "Subject", "Body")


def test_send_email_success_with_auth(fake_smtp, email_config):
    """A configured SMTP relay is opened, TLS applied, credentials used, and sent."""
    result = send_email(email_config, "user@example.com", "Hello", "Test body")
    assert result is True

    assert len(fake_smtp.instances) == 1
    smtp = fake_smtp.instances[0]
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.starttls_called is True
    assert smtp.login_called == ("user", "secret")
    assert len(smtp.sent) == 1

    msg = smtp.sent[0]
    assert msg["From"] == "songhive@example.com"
    assert msg["To"] == "user@example.com"
    assert msg["Subject"] == "Hello"
    assert msg.get_content().strip() == "Test body"


def test_send_email_without_credentials_or_tls(fake_smtp, email_config_no_auth):
    """No login or starttls is attempted when credentials and TLS are disabled."""
    result = send_email(email_config_no_auth, "user@example.com", "Hi", "Body")
    assert result is True

    smtp = fake_smtp.instances[0]
    assert smtp.starttls_called is False
    assert smtp.login_called is None
    assert smtp.sent[0]["From"] == "songhive@example.com"


def test_send_email_returns_false_on_smtp_failure(fake_smtp, email_config, caplog):
    """SMTP failures are logged and do not propagate."""

    class FailingSMTP(fake_smtp):
        def __init__(self, host, port):
            super().__init__(host, port)
            FailingSMTP.instances.append(self)

        def send_message(self, _):
            raise smtplib.SMTPException("boom")

    # Replace the fixture's fake with the failing variant for this test only.
    fake_smtp.instances.clear()
    # Monkeypatching the classes directly, not using the fixture's return, because
    # the failing SMTP needs to be installed after the fixture.
    import smtplib as smtplib_module

    smtplib_module.SMTP = FailingSMTP
    smtplib_module.SMTP_SSL = FailingSMTP

    caplog.set_level(logging.ERROR, logger="songhive.services.email")
    result = send_email(email_config, "user@example.com", "Subject", "Body")
    assert result is False
    assert "Failed to send email" in caplog.text
    assert "user@example.com" in caplog.text


def test_send_verification_email_content(fake_smtp, email_config):
    """The verification helper sends a message with the raw token and endpoint."""
    token = "verification-token-123"
    result = send_verification_email(email_config, "user@example.com", "alice", token)
    assert result is True

    msg = fake_smtp.instances[0].sent[0]
    assert msg["Subject"] == "Verify your Songhive account"
    assert token in msg.get_content()
    assert "/api/v1/auth/verify-email" in msg.get_content()
    assert "alice" in msg.get_content()


def test_send_password_reset_email_content(fake_smtp, email_config):
    """The password reset helper includes the raw token and expiry."""
    token = "reset-token-456"
    result = send_password_reset_email(email_config, "user@example.com", "bob", token)
    assert result is True

    msg = fake_smtp.instances[0].sent[0]
    assert msg["Subject"] == "Reset your Songhive password"
    assert token in msg.get_content()
    assert "/api/v1/auth/password-reset/confirm" in msg.get_content()
    assert "bob" in msg.get_content()
    assert f"expires in {email_config.auth.password_reset_token_expiry_minutes} minutes" in msg.get_content()


def test_send_email_does_not_log_tokens(fake_smtp, email_config, caplog):
    """Tokens and SMTP credentials are not written to logs."""
    token = "super-secret-token"
    caplog.set_level(logging.INFO, logger="songhive.services.email")
    send_email(email_config, "user@example.com", "Subject", f"Token: {token}")
    assert token not in caplog.text
    assert "secret" not in caplog.text  # smtp_password


def test_verification_task_sends_email(fake_smtp, email_config, monkeypatch):
    """The Celery verification task loads config and delegates to the service."""
    monkeypatch.setattr("songhive.tasks.email.load_config", lambda _: email_config)

    token = "task-verify-token"
    result = send_verification_email_task.run("user@example.com", "alice", token)
    assert result is True

    msg = fake_smtp.instances[0].sent[0]
    assert msg["To"] == "user@example.com"
    assert token in msg.get_content()


def test_password_reset_task_sends_email(fake_smtp, email_config, monkeypatch):
    """The Celery password-reset task loads config and delegates to the service."""
    monkeypatch.setattr("songhive.tasks.email.load_config", lambda _: email_config)

    token = "task-reset-token"
    result = send_password_reset_email_task.run("user@example.com", "bob", token)
    assert result is True

    msg = fake_smtp.instances[0].sent[0]
    assert msg["To"] == "user@example.com"
    assert token in msg.get_content()


def test_verification_task_noops_when_not_configured(caplog, monkeypatch):
    """The task returns False and warns when email is not configured."""
    config = SonghiveConfig(
        auth={"secret_key": "a" * 32},
        federation={"enabled": False},
        email={"smtp_host": None, "from_address": None},
    )
    monkeypatch.setattr("songhive.tasks.email.load_config", lambda _: config)
    caplog.set_level(logging.WARNING, logger="songhive.tasks.email")

    result = send_verification_email_task.run("user@example.com", "alice", "token")
    assert result is False
    assert "Email not queued" in caplog.text


def test_password_reset_task_noops_when_not_configured(caplog, monkeypatch):
    """The task returns False and warns when email is not configured."""
    config = SonghiveConfig(
        auth={"secret_key": "a" * 32},
        federation={"enabled": False},
        email={"smtp_host": None, "from_address": None},
    )
    monkeypatch.setattr("songhive.tasks.email.load_config", lambda _: config)
    caplog.set_level(logging.WARNING, logger="songhive.tasks.email")

    result = send_password_reset_email_task.run("user@example.com", "bob", "token")
    assert result is False
    assert "Email not queued" in caplog.text
