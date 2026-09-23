"""Tests for the Web Push subscription service and API."""

from unittest.mock import MagicMock

import pytest

from songhive.services import push

SAMPLE_ENDPOINT = "https://push.example.com/subscription/abc123"
SAMPLE_P256DH = "BLDxq7ybZJrZvQ-BnVqfdpG5nOdLT7oEyo9b1GcvU7WWQ"
SAMPLE_AUTH = "RndzZ2dPZVdRb1VXeUx6Yg"


@pytest.fixture
def push_keys():
    """Return a freshly generated VAPID key pair."""
    return push.generate_vapid_keys()


@pytest.fixture
def configured_config(config, push_keys):
    """Return a config with VAPID keys and push enabled."""
    public_key, private_key = push_keys
    new_config = config.model_copy()
    new_config.notifications = config.notifications.model_copy(
        update={
            "vapid_public_key": public_key,
            "vapid_private_key": private_key,
            "vapid_subscriber": "mailto:admin@example.com",
            "push_enabled": True,
        }
    )
    return new_config


@pytest.mark.asyncio
async def test_store_and_update_subscription(db_session, regular_user):
    """Storing a subscription creates a row; re-storing updates the keys."""
    sub = await push.store_subscription(
        db_session,
        regular_user.id,
        SAMPLE_ENDPOINT,
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )
    assert sub.user_id == regular_user.id
    assert sub.endpoint == SAMPLE_ENDPOINT
    assert sub.p256dh == SAMPLE_P256DH

    updated = await push.store_subscription(
        db_session,
        regular_user.id,
        SAMPLE_ENDPOINT,
        "new-p256dh",
        "new-auth",
    )
    assert updated.id == sub.id
    assert updated.p256dh == "new-p256dh"
    assert updated.auth == "new-auth"


@pytest.mark.asyncio
async def test_remove_subscription_scoped_to_user(db_session, regular_user, other_user):
    """Removal only deletes the current user's matching endpoint."""
    await push.store_subscription(
        db_session,
        regular_user.id,
        SAMPLE_ENDPOINT,
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )
    await push.store_subscription(
        db_session,
        other_user.id,
        SAMPLE_ENDPOINT,
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )

    removed = await push.remove_subscription(db_session, regular_user.id, SAMPLE_ENDPOINT)
    assert removed == 1

    remaining = await push.list_subscriptions(db_session, other_user.id)
    assert len(remaining) == 1
    assert await push.list_subscriptions(db_session, regular_user.id) == []


@pytest.mark.asyncio
async def test_list_subscriptions(db_session, regular_user, other_user):
    """Subscriptions are scoped to the user and returned newest-first."""
    first = await push.store_subscription(
        db_session,
        regular_user.id,
        f"{SAMPLE_ENDPOINT}-1",
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )
    second = await push.store_subscription(
        db_session,
        regular_user.id,
        f"{SAMPLE_ENDPOINT}-2",
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )
    await push.store_subscription(
        db_session,
        other_user.id,
        f"{SAMPLE_ENDPOINT}-3",
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )

    subs = await push.list_subscriptions(db_session, regular_user.id)
    assert [s.id for s in subs] == [second.id, first.id]


@pytest.mark.asyncio
async def test_is_push_available(config, configured_config):
    """Push availability depends on all VAPID fields and the enabled flag."""
    assert push.is_push_available(config) is False
    assert push.is_push_available(configured_config) is True

    configured_config.notifications.push_enabled = False
    assert push.is_push_available(configured_config) is False


@pytest.mark.asyncio
async def test_get_public_key(config, configured_config):
    """The public key is only exposed when push is fully configured."""
    assert push.get_public_key(config) is None
    assert push.get_public_key(configured_config) == configured_config.notifications.vapid_public_key


def test_generate_vapid_keys():
    """Generated VAPID keys are base64url and have the expected lengths."""
    public, private = push.generate_vapid_keys()
    raw_public = push._base64url_to_bytes(public)
    raw_private = push._base64url_to_bytes(private)
    assert len(raw_public) == 65
    assert len(raw_private) == 32
    assert public == public.rstrip("=")
    assert private == private.rstrip("=")


@pytest.mark.asyncio
async def test_send_skips_when_not_configured(db_session, regular_user, config):
    """With no VAPID config, send_notification_to_user returns 0."""
    await push.store_subscription(
        db_session,
        regular_user.id,
        SAMPLE_ENDPOINT,
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )
    sent = await push.send_notification_to_user(db_session, regular_user.id, {"id": "n1"}, config)
    assert sent == 0


@pytest.mark.asyncio
async def test_send_success(db_session, regular_user, configured_config, monkeypatch):
    """A successful webpush call increments the sent count."""
    webpush_mock = MagicMock()
    monkeypatch.setattr("songhive.services.push.webpush", webpush_mock)
    await push.store_subscription(
        db_session,
        regular_user.id,
        SAMPLE_ENDPOINT,
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )

    sent = await push.send_notification_to_user(
        db_session,
        regular_user.id,
        {"id": "n1", "type": "like", "payload": {"actor_name": "bob"}},
        configured_config,
    )
    assert sent == 1
    assert webpush_mock.call_count == 1


@pytest.mark.asyncio
async def test_send_removes_invalid_subscriptions(db_session, regular_user, configured_config, monkeypatch):
    """Expired/invalid subscriptions (410/404/403) are removed after failure."""

    class FakeWebPushException(Exception):
        status_code = 410

    def _raise(*args, **kwargs):
        raise FakeWebPushException("Gone")

    monkeypatch.setattr("songhive.services.push.webpush", _raise)
    monkeypatch.setattr("songhive.services.push.WebPushException", FakeWebPushException)

    await push.store_subscription(
        db_session,
        regular_user.id,
        SAMPLE_ENDPOINT,
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )

    sent = await push.send_notification_to_user(
        db_session,
        regular_user.id,
        {"id": "n1"},
        configured_config,
    )
    assert sent == 0
    assert await push.list_subscriptions(db_session, regular_user.id) == []


@pytest.mark.asyncio
async def test_send_swallows_unrelated_errors(db_session, regular_user, configured_config, monkeypatch):
    """Non-4xx/5xx-ish push failures leave the subscription in place."""

    def _raise(*args, **kwargs):
        raise ValueError("network hiccup")

    monkeypatch.setattr("songhive.services.push.webpush", _raise)

    await push.store_subscription(
        db_session,
        regular_user.id,
        SAMPLE_ENDPOINT,
        SAMPLE_P256DH,
        SAMPLE_AUTH,
    )

    sent = await push.send_notification_to_user(
        db_session,
        regular_user.id,
        {"id": "n1"},
        configured_config,
    )
    assert sent == 0
    assert await push.list_subscriptions(db_session, regular_user.id)


def test_push_config_unconfigured(client, regular_user, auth_headers):
    """The push config endpoint reports disabled and no key when not configured."""
    response = client.get("/api/v1/notifications/push-config")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["public_key"] is None


def test_push_config_configured(client, configured_config, regular_user, auth_headers):
    """The push config endpoint exposes the public key when push is configured."""
    client.app.state.config = configured_config
    response = client.get("/api/v1/notifications/push-config")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["public_key"] == configured_config.notifications.vapid_public_key


def test_push_subscription_requires_auth(client):
    """Subscription registration and removal require authentication."""
    assert client.post("/api/v1/notifications/push-subscription", json={}).status_code == 401
    assert client.delete("/api/v1/notifications/push-subscription").status_code == 401


def test_push_subscription_lifecycle(client, db_session, regular_user, auth_headers):
    """Authenticated users can register and remove push subscriptions."""
    headers = auth_headers(regular_user)
    body = {
        "endpoint": SAMPLE_ENDPOINT,
        "p256dh": SAMPLE_P256DH,
        "auth": SAMPLE_AUTH,
    }

    response = client.post("/api/v1/notifications/push-subscription", json=body, headers=headers)
    assert response.status_code == 204

    response = client.delete(
        "/api/v1/notifications/push-subscription",
        params={"endpoint": SAMPLE_ENDPOINT},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["removed"] == 1
