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


def _remote_actor_storage(tmp_path):
    """A pubby storage backed by a fresh SQLite database."""
    from songhive.federation.storage import create_activitypub_storage

    return create_activitypub_storage(f"sqlite+aiosqlite:///{tmp_path / 'fed.db'}")


def _store_remote_actors(storage):
    """Seed one follower and one cache-only remote actor."""
    from datetime import datetime, timezone

    from pubby import Follower

    storage.store_follower(
        Follower(
            actor_id="https://remote.example/users/bob",
            inbox="https://remote.example/users/bob/inbox",
            followed_at=datetime.now(timezone.utc),
            actor_data={"preferredUsername": "bob", "name": "Bob Remote"},
            target_actor_id="https://music.example.com/users/alice",
        )
    )
    storage.cache_remote_actor(
        "https://other.example/@carol",
        {"preferredUsername": "carol", "name": "Carol"},
        datetime.now(timezone.utc),
    )
    # The same actor in both tables must produce a single follower match.
    storage.cache_remote_actor(
        "https://remote.example/users/bob",
        {"preferredUsername": "bob", "name": "Bob Remote"},
        datetime.now(timezone.utc),
    )


def test_search_remote_actors_matches_followers_and_cache(config, tmp_path):
    """Followers and cached actors both surface, deduplicated by actor URL."""
    from songhive.services.federation import search_remote_actors

    storage = _remote_actor_storage(tmp_path)
    _store_remote_actors(storage)

    matches = search_remote_actors(storage, "o", config)
    handles = {m.handle: m for m in matches}
    assert set(handles) == {"bob@remote.example", "carol@other.example"}
    assert handles["bob@remote.example"].follower is True
    assert handles["bob@remote.example"].display_name == "Bob Remote"
    assert handles["carol@other.example"].follower is False


def test_search_remote_actors_matches_display_name_and_url(config, tmp_path):
    """The query matches ``name`` and the actor URL, not just the username."""
    from songhive.services.federation import search_remote_actors

    storage = _remote_actor_storage(tmp_path)
    _store_remote_actors(storage)

    assert [m.handle for m in search_remote_actors(storage, "remote", config)] == ["bob@remote.example"]
    assert [m.handle for m in search_remote_actors(storage, "carol", config)] == ["carol@other.example"]


def test_search_remote_actors_user_at_domain_narrows(config, tmp_path):
    """``user@domain`` queries constrain the match to that domain."""
    from songhive.services.federation import search_remote_actors

    storage = _remote_actor_storage(tmp_path)
    _store_remote_actors(storage)

    assert [m.handle for m in search_remote_actors(storage, "o@other", config)] == ["carol@other.example"]
    assert search_remote_actors(storage, "carol@remote", config) == []


def test_search_remote_actors_skips_local_and_blocked(config, tmp_path):
    """Instance-domain actors and blocked domains never match."""
    from songhive.services.federation import search_remote_actors

    storage = _remote_actor_storage(tmp_path)
    _store_remote_actors(storage)
    # The name contains the query letter so the row does match the SQL
    # predicate — the local-domain check is what keeps it out.
    storage.cache_remote_actor(
        "https://music.example.com/users/alice",
        {"preferredUsername": "alice", "name": "Alice Local"},
    )
    config.federation.blocked_instances = ["other.example"]

    matches = search_remote_actors(storage, "o", config)
    handles = [m.handle for m in matches]
    assert "bob@remote.example" in handles
    assert "alice@music.example.com" not in handles
    assert "carol@other.example" not in handles


def test_search_remote_actors_derives_username_from_url(config, tmp_path):
    """Actors without ``preferredUsername`` fall back to the URL's last segment."""
    from songhive.services.federation import search_remote_actors

    storage = _remote_actor_storage(tmp_path)
    storage.cache_remote_actor("https://remote.example/@dave", {})

    matches = search_remote_actors(storage, "dave", config)
    assert [m.handle for m in matches] == ["dave@remote.example"]


def test_search_remote_actors_disabled(config, tmp_path):
    """Federation disabled short-circuits to no matches."""
    from songhive.services.federation import search_remote_actors

    config.federation.enabled = False
    storage = _remote_actor_storage(tmp_path)
    _store_remote_actors(storage)
    assert search_remote_actors(storage, "bob", config) == []


def test_actor_doc_handle_prefers_preferred_username():
    """Opaque actor ids (e.g. Mastodon ``/ap/users/<id>``) resolve to the doc username."""
    from songhive.services.federation import actor_doc_handle

    assert (
        actor_doc_handle(
            {"preferredUsername": "amber"},
            "https://hear-me.social/ap/users/117220292797596489",
        )
        == "amber@hear-me.social"
    )


def test_actor_doc_handle_falls_back_to_url_tail():
    """Without ``preferredUsername`` the URL tail names the actor, as before."""
    from songhive.services.federation import actor_doc_handle

    assert actor_doc_handle(None, "https://remote.example/users/bob") == "bob@remote.example"
    assert actor_doc_handle({}, "https://remote.example/@carol") == "carol@remote.example"
    assert actor_doc_handle(None, "") is None


def test_cached_actor_handles_reads_all_pubby_tables(tmp_path):
    """Actor cache, follower and follow-request rows all yield handles."""
    from datetime import datetime, timezone

    from pubby import Follower, FollowRequest

    from songhive.services.federation import cached_actor_handle, cached_actor_handles

    opaque_follower = "https://hear-me.social/ap/users/117220292797596489"
    opaque_request = "https://spore.social/ap/users/117308811748217043"
    cached = "https://other.example/users/zed"

    storage = _remote_actor_storage(tmp_path)
    storage.store_follower(
        Follower(
            actor_id=opaque_follower,
            inbox="https://hear-me.social/inbox",
            followed_at=datetime.now(timezone.utc),
            actor_data={"preferredUsername": "amber"},
            target_actor_id="https://music.example.com/users/alice",
        )
    )
    storage.store_follow_request(
        FollowRequest(
            actor_id=opaque_request,
            target_actor_id="https://music.example.com/users/alice",
            inbox="https://spore.social/inbox",
            actor_data={"preferredUsername": "disisdeguey2"},
        )
    )
    storage.cache_remote_actor(
        cached,
        {"preferredUsername": "zed"},
        datetime.now(timezone.utc),
    )

    urls = [opaque_follower, opaque_request, cached, "https://unknown.example/users/nobody"]
    assert cached_actor_handles(storage, urls) == {
        opaque_follower: "amber@hear-me.social",
        opaque_request: "disisdeguey2@spore.social",
        cached: "zed@other.example",
    }
    assert cached_actor_handle(storage, "https://unknown.example/users/nobody") is None


def test_cached_actor_handles_falls_back_to_url_tail(tmp_path):
    """A cached doc without ``preferredUsername`` still yields a tail handle."""
    from datetime import datetime, timezone

    from songhive.services.federation import cached_actor_handles

    storage = _remote_actor_storage(tmp_path)
    storage.cache_remote_actor("https://remote.example/users/dave", {}, datetime.now(timezone.utc))
    assert cached_actor_handles(storage, ["https://remote.example/users/dave"]) == {
        "https://remote.example/users/dave": "dave@remote.example"
    }
