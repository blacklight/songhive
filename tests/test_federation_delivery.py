"""
Tests for the signed outgoing ActivityPub delivery task.

The actual signing and POST are delegated to ``pubby.deliver_activity``;
these tests patch ``pubby.handlers._outbox.requests.post`` and cover the
task's retry policy (5xx/429 retry, other 4xx give up) and the federation
gates that run before delivery.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from celery.exceptions import Retry
from pubby.crypto import export_private_key_pem, generate_rsa_keypair

from songhive.config.schema import SonghiveConfig
from songhive.tasks.federation import deliver_activity

PUBBY_POST = "pubby.handlers._outbox.requests.post"


def _make_config(**federation_overrides):
    """Build a test configuration with federation enabled."""
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": "sqlite+aiosqlite:///:memory:"},
        federation={
            "enabled": True,
            "instance_domain": "music.example.com",
            **federation_overrides,
        },
    )


def _make_private_key_pem():
    """Generate a PEM-encoded RSA private key for tests."""
    private_key, _ = generate_rsa_keypair()
    return export_private_key_pem(private_key)


def _patch_retry(monkeypatch):
    """Make the bound task's retry method record calls and return a Retry exception."""
    monkeypatch.setattr(
        deliver_activity,
        "retry",
        MagicMock(return_value=Retry("delivery failed")),
    )


def test_deliver_activity_sends_signed_post(monkeypatch):
    """A successful delivery signs the request and POSTs ActivityPub JSON."""
    config = _make_config()
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    private_key_pem = _make_private_key_pem()
    activity = {"type": "Create", "object": {"type": "Audio"}}
    inbox_url = "https://remote.example/inbox"
    actor_key_id = "https://music.example.com/users/alice#main-key"

    with patch(PUBBY_POST) as mock_post:
        mock_post.return_value.status_code = 202
        result = deliver_activity.run(activity, inbox_url, actor_key_id, private_key_pem)

    assert result == {"status_code": 202}
    mock_post.assert_called_once()
    call = mock_post.call_args
    assert call.args[0] == inbox_url
    assert call.kwargs["data"] == json.dumps(activity).encode("utf-8")
    assert call.kwargs["timeout"] == 15.0

    headers = call.kwargs["headers"]
    assert headers["Content-Type"] == "application/activity+json"
    assert "Signature" in headers
    assert headers["Content-Length"] == str(len(call.kwargs["data"]))
    assert headers["User-Agent"].startswith("pubby/")


def test_deliver_activity_skips_blocked_domain(monkeypatch):
    """The task makes no HTTP call when the inbox domain is blocked."""
    config = _make_config(blocked_instances=["evil.example"])
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    activity = {"type": "Create"}
    inbox_url = "https://evil.example/inbox"
    private_key_pem = _make_private_key_pem()
    actor_key_id = "https://music.example.com/ap/actor#main-key"

    with (
        patch(PUBBY_POST) as mock_post,
        patch("songhive.tasks.federation.load_private_key") as mock_load_key,
    ):
        result = deliver_activity.run(  # type: ignore
            activity,
            inbox_url,
            actor_key_id,
            private_key_pem,
        )

    assert result is None
    assert not mock_post.called
    assert not mock_load_key.called


def test_deliver_activity_retries_on_network_error(monkeypatch):
    """Network errors trigger a retry with the default countdown."""
    config = _make_config()
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)
    _patch_retry(monkeypatch)

    private_key_pem = _make_private_key_pem()
    activity = {"type": "Create"}
    inbox_url = "https://remote.example/inbox"
    actor_key_id = "https://music.example.com/users/alice#main-key"
    exc = requests.ConnectionError("boom")

    with patch(PUBBY_POST, side_effect=exc), pytest.raises(Retry):
        deliver_activity.run(activity, inbox_url, actor_key_id, private_key_pem)  # type: ignore

    assert deliver_activity.retry.call_args.kwargs["countdown"] == 30
    assert isinstance(deliver_activity.retry.call_args.kwargs["exc"], requests.RequestException)


def test_deliver_activity_retries_5xx_with_exponential_countdown(monkeypatch):
    """5xx responses are retried with an exponential countdown."""
    config = _make_config()
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)
    monkeypatch.setattr(deliver_activity.request, "retries", 2)
    _patch_retry(monkeypatch)

    private_key_pem = _make_private_key_pem()
    activity = {"type": "Create"}
    inbox_url = "https://remote.example/inbox"
    actor_key_id = "https://music.example.com/users/alice#main-key"

    mock_response = MagicMock(status_code=503)
    with patch(PUBBY_POST, return_value=mock_response), pytest.raises(Retry):
        deliver_activity.run(activity, inbox_url, actor_key_id, private_key_pem)  # type: ignore

    assert deliver_activity.retry.call_count == 1
    assert deliver_activity.retry.call_args.kwargs["countdown"] == 120  # 30 * 2 ** 2
    exc = deliver_activity.retry.call_args.kwargs["exc"]
    assert isinstance(exc, Exception)


def test_deliver_activity_retries_429(monkeypatch):
    """HTTP 429 responses are retried."""
    config = _make_config()
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)
    _patch_retry(monkeypatch)

    private_key_pem = _make_private_key_pem()
    activity = {"type": "Create"}
    inbox_url = "https://remote.example/inbox"
    actor_key_id = "https://music.example.com/users/alice#main-key"

    mock_response = MagicMock(status_code=429)
    with patch(PUBBY_POST, return_value=mock_response), pytest.raises(Retry):
        deliver_activity.run(activity, inbox_url, actor_key_id, private_key_pem)  # type: ignore

    assert deliver_activity.retry.call_args.kwargs["countdown"] == 30


def test_deliver_activity_does_not_retry_other_4xx(monkeypatch):
    """Permanent 4xx responses are logged and not retried."""
    config = _make_config()
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)
    _patch_retry(monkeypatch)

    private_key_pem = _make_private_key_pem()
    activity = {"type": "Create"}
    inbox_url = "https://remote.example/inbox"
    actor_key_id = "https://music.example.com/users/alice#main-key"

    mock_response = MagicMock(status_code=404)
    with patch(PUBBY_POST, return_value=mock_response):
        result = deliver_activity.run(activity, inbox_url, actor_key_id, private_key_pem)

    assert result is None
    assert not deliver_activity.retry.called
