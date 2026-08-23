"""
Federation service helper tests.
"""

from unittest.mock import MagicMock, patch

import pytest

from songhive.config.schema import SonghiveConfig
from songhive.models.user import User
from songhive.services.federation import (
    ensure_user_actor,
    get_follower_inboxes,
    provision_federation_keys,
)


@pytest.fixture
def config():
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        federation={"enabled": True, "instance_domain": "music.example.com"},
    )


def test_provision_federation_keys_populates_all_fields():
    user = User(username="alice", email="alice@example.com", password_hash="x")
    assert provision_federation_keys(user, "music.example.com") is True
    assert user.actor_url == "https://music.example.com/users/alice"
    assert user.private_key_pem
    assert user.public_key_pem


def test_provision_federation_keys_noop_when_complete():
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        actor_url="https://music.example.com/users/alice",
        private_key_pem="private",
        public_key_pem="public",
    )
    assert provision_federation_keys(user, "music.example.com") is False
    assert user.private_key_pem == "private"
    assert user.public_key_pem == "public"


def test_provision_federation_keys_rotates_mismatched_key():
    user = User(
        username="alice",
        email="alice@example.com",
        password_hash="x",
        actor_url="https://music.example.com/users/alice",
        public_key_pem="public-only",
    )
    assert provision_federation_keys(user, "music.example.com") is True
    assert user.private_key_pem
    assert user.public_key_pem
    assert user.public_key_pem != "public-only"


def test_ensure_user_actor_disabled(config):
    user = User(username="alice", email="alice@example.com", password_hash="x")
    config.federation.enabled = False
    assert ensure_user_actor(user, config) is False
    assert user.actor_url is None


def test_ensure_user_actor_no_domain(config):
    user = User(username="alice", email="alice@example.com", password_hash="x")
    config.federation.instance_domain = ""
    assert ensure_user_actor(user, config) is False
    assert user.actor_url is None


def test_ensure_user_actor_provisions(config):
    user = User(username="alice", email="alice@example.com", password_hash="x")
    assert ensure_user_actor(user, config) is True
    assert user.actor_url == "https://music.example.com/users/alice"
    assert user.private_key_pem
    assert user.public_key_pem


def test_get_follower_inboxes_returns_unique_inboxes_for_actor():
    """Inboxes are filtered to the requested actor and deduplicated."""
    actor_url = "https://music.example.com/users/alice"
    follower_a = MagicMock(
        inbox="https://a.example/inbox",
        shared_inbox="",
        actor_data={"followed_actor": actor_url},
    )
    follower_b = MagicMock(
        inbox="https://b.example/inbox",
        shared_inbox="https://b.example/shared",
        actor_data={"followed_actor": actor_url},
    )

    storage = MagicMock()
    storage.get_followers.return_value = [follower_a, follower_b]

    with patch(
        "songhive.services.federation.create_activitypub_storage",
        return_value=storage,
    ):
        inboxes = get_follower_inboxes(actor_url, "sqlite:///:memory:")

    assert inboxes == ["https://a.example/inbox", "https://b.example/shared"]
    storage.get_followers.assert_called_once_with(actor_id=actor_url)
