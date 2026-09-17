"""
Tests for the user followers API (``GET /api/v1/users/{username}/followers``).

Followers are persisted in pubby's ``federation_followers`` storage: incoming
``Follow`` activities store the follower (including the fetched actor document)
and ``Undo(Follow)`` or an actor ``Delete`` removes it.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pubby import Follower, FollowRequest

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.api.middleware.auth import create_access_token
from songhive.config.schema import SonghiveConfig
from songhive.federation.actors import get_federation_storage
from songhive.federation.storage import create_activitypub_storage
from songhive.models.base import get_session, init_db
from songhive.services.auth import create_user
from songhive.services.federation import (
    count_followers_by_actor,
    get_actor_follow_requests,
    get_actor_followers,
)
from songhive.tasks.federation import process_incoming

REGULAR_ACTOR = "https://music.example.com/users/regular"
BOB_ACTOR = "https://remote.example/users/bob"
CAROL_ACTOR = "https://remote.example/users/carol"

BOB_DOC = {
    "id": BOB_ACTOR,
    "type": "Person",
    "preferredUsername": "bob",
    "name": "Bob Remote",
    "inbox": "https://remote.example/users/bob/inbox",
    "icon": {"type": "Image", "url": "https://remote.example/bob.png"},
}


def _fed_config(config, tmp_path):
    """Return a federation-enabled copy of the test config."""
    fed = config.model_copy(deep=True)
    fed.federation.enabled = True
    fed.federation.instance_domain = "music.example.com"
    fed.federation.private_key_path = Path(fed.storage.local_path).parent / "actor.pem"
    return fed


@pytest.fixture
def fed_client(fed_app, db_session, fake_redis_server, monkeypatch):
    """Create a test client for the federation-enabled app."""
    from fakeredis.aioredis import FakeRedis

    def _get_redis_client(_):
        return FakeRedis(server=fake_redis_server, decode_responses=True)

    monkeypatch.setattr("songhive.api.app.get_redis_client", _get_redis_client)

    async def _db():
        yield db_session

    with TestClient(fed_app) as client:
        client.app.dependency_overrides[get_db] = _db  # type: ignore
        yield client
        client.app.dependency_overrides.pop(get_db, None)  # type: ignore


@pytest.fixture
def fed_app(fed_config, engine):
    """Create a federation-enabled test application."""
    init_db(engine=engine, force=True)
    return create_app(fed_config)


@pytest.fixture
def fed_config(config, tmp_path):
    """Federation-enabled test config."""
    return _fed_config(config, tmp_path)


@pytest.fixture
async def fed_user(db_session, regular_user):
    """Give the regular user an actor URL and commit it for task sessions."""
    regular_user.actor_url = REGULAR_ACTOR
    await db_session.commit()
    return regular_user


def _follower(actor_id, followed_at, actor_data=None, target=REGULAR_ACTOR):
    return Follower(
        actor_id=actor_id,
        inbox=f"{actor_id}/inbox",
        followed_at=followed_at,
        actor_data=actor_data or {},
        target_actor_id=target,
    )


def _store(fed_config, *followers):
    storage = create_activitypub_storage(fed_config.database.url)
    for follower in followers:
        storage.store_follower(follower)


async def test_get_user_returns_followers_count(fed_client, fed_config, fed_user):
    """GET /users/{username} reports the stored follower count."""
    _store(
        fed_config,
        _follower(BOB_ACTOR, datetime.now(timezone.utc), BOB_DOC),
        _follower(CAROL_ACTOR, datetime.now(timezone.utc)),
        # A follower of a different actor must not be counted.
        _follower(
            "https://remote.example/users/dan",
            datetime.now(timezone.utc),
            target="https://music.example.com/users/other",
        ),
    )

    response = fed_client.get("/api/v1/users/regular")
    assert response.status_code == 200
    assert response.json()["followers_count"] == 2


async def test_get_user_followers_count_zero_when_federation_disabled(client, regular_user):
    """Without federation the profile reports zero followers."""
    response = client.get("/api/v1/users/regular")
    assert response.status_code == 200
    assert response.json()["followers_count"] == 0


async def test_list_users_reports_followers_count(fed_client, fed_config, fed_user):
    """The public user directory also carries the follower count."""
    _store(fed_config, _follower(BOB_ACTOR, datetime.now(timezone.utc), BOB_DOC))

    response = fed_client.get("/api/v1/users")
    assert response.status_code == 200
    counts = {u["username"]: u["followers_count"] for u in response.json()}
    assert counts["regular"] == 1


async def test_list_followers_sorted_by_followed_at_desc(fed_client, fed_config, fed_user):
    """Followers are returned newest-first with details from the actor doc."""
    now = datetime.now(timezone.utc)
    _store(
        fed_config,
        _follower(BOB_ACTOR, now - timedelta(days=2), BOB_DOC),
        _follower(
            CAROL_ACTOR,
            now,
            {"id": CAROL_ACTOR, "preferredUsername": "carol", "name": "Carol"},
        ),
    )

    response = fed_client.get("/api/v1/users/regular/followers")
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"

    data = response.json()
    assert [f["actor_url"] for f in data] == [CAROL_ACTOR, BOB_ACTOR]
    assert data[1]["display_name"] == "Bob Remote"
    assert data[1]["avatar_url"] == "https://remote.example/bob.png"
    assert data[1]["followed_at"]


async def test_list_followers_pagination(fed_client, fed_config, fed_user):
    """limit/offset paginate the followers list."""
    now = datetime.now(timezone.utc)
    _store(
        fed_config,
        *[_follower(f"https://remote.example/users/u{i}", now - timedelta(minutes=i)) for i in range(3)],
    )

    page1 = fed_client.get("/api/v1/users/regular/followers", params={"limit": 2, "offset": 0})
    page2 = fed_client.get("/api/v1/users/regular/followers", params={"limit": 2, "offset": 2})
    assert page1.status_code == 200 and page2.status_code == 200
    assert page1.headers["X-Total-Count"] == "3"
    assert [f["actor_url"] for f in page1.json()] == [
        "https://remote.example/users/u0",
        "https://remote.example/users/u1",
    ]
    assert [f["actor_url"] for f in page2.json()] == ["https://remote.example/users/u2"]


async def test_list_followers_empty(fed_client, fed_user):
    """A user without followers gets an empty list."""
    response = fed_client.get("/api/v1/users/regular/followers")
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


async def test_list_followers_unknown_user(fed_client):
    """Followers of a non-existent user return 404."""
    response = fed_client.get("/api/v1/users/nobody/followers")
    assert response.status_code == 404


async def test_list_followers_federation_disabled(client, regular_user):
    """Without federation the followers list is empty."""
    response = client.get("/api/v1/users/regular/followers")
    assert response.status_code == 200
    assert response.json() == []


def test_get_actor_followers_sorts_and_filters(fed_config):
    """get_actor_followers returns only the target actor's followers, newest first."""
    storage = create_activitypub_storage(fed_config.database.url)
    now = datetime.now(timezone.utc)
    storage.store_follower(_follower(BOB_ACTOR, now - timedelta(days=1)))
    storage.store_follower(_follower(CAROL_ACTOR, now))
    storage.store_follower(_follower("https://remote.example/users/dan", now, target="https://other.example/users/x"))

    followers = get_actor_followers(storage, REGULAR_ACTOR)
    assert [f.actor_id for f in followers] == [CAROL_ACTOR, BOB_ACTOR]

    counts = count_followers_by_actor(storage)
    assert counts[REGULAR_ACTOR] == 2
    assert counts["https://other.example/users/x"] == 1


def _task_config(tmp_path):
    """Federation-enabled config for ``process_incoming`` tests."""
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={
            "enabled": True,
            "instance_domain": "music.example.com",
            "private_key_path": tmp_path / "actor.pem",
        },
    )


def _seed_user(engine, config, username="alice", followers_approval=None):
    """Create a local user with provisioned actor keys on the test engine."""
    init_db(engine=engine, force=True)

    async def _create():
        async with get_session() as session:
            user = await create_user(
                session,
                username=username,
                email=f"{username}@example.com",
                password="secret",
                config=config,
            )
            if followers_approval is not None:
                user.followers_approval = followers_approval
                await session.commit()
            return user

    return asyncio.run(_create())


def _run_process_incoming(engine, config, activity, username="alice", deliveries=None):
    """Run the incoming-activity task against the test engine."""
    from pubby.handlers._inbox import InboxProcessor

    real_process = InboxProcessor.process

    def _process_unverified(self, *args, **kwargs):
        # These tests exercise follower bookkeeping, not request signing:
        # the real processor requires verified HTTP signature headers.
        kwargs["skip_verification"] = True
        return real_process(self, *args, **kwargs)

    def _deliver(*args, **kwargs):
        if deliveries is not None:
            deliveries.append((args, kwargs))
        return True

    with (
        patch(
            "songhive.tasks.federation.init_db",
            lambda *a, **k: init_db(engine=engine, force=True),
        ),
        patch("songhive.tasks.federation.load_config", lambda *_, **__: config),
        patch(
            "pubby.handlers._inbox.InboxProcessor._deliver_to_inbox",
            _deliver,
        ),
        patch.object(InboxProcessor, "process", _process_unverified),
    ):
        return process_incoming(activity, username=username)


def _followers_of(config, actor_url):
    storage = get_federation_storage(config.database.url)
    return storage.get_followers(actor_id=actor_url)


def test_incoming_follow_stores_follower(engine, tmp_path):
    """A Follow to a local user's inbox stores the follower with its actor data."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config)
    actor_url = user.actor_url

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    activity = {
        "type": "Follow",
        "id": "https://remote.example/activities/f1",
        "actor": BOB_ACTOR,
        "object": actor_url,
    }
    _run_process_incoming(engine, config, activity)

    followers = _followers_of(config, actor_url)
    assert [f.actor_id for f in followers] == [BOB_ACTOR]
    assert followers[0].actor_data["preferredUsername"] == "bob"
    assert followers[0].followed_at is not None


def test_incoming_undo_follow_removes_follower(engine, tmp_path):
    """An Undo(Follow) removes the stored follower."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config)
    actor_url = user.actor_url

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    _run_process_incoming(
        engine,
        config,
        {"type": "Follow", "id": "https://remote.example/activities/f1", "actor": BOB_ACTOR, "object": actor_url},
    )
    assert _followers_of(config, actor_url)

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Undo",
            "id": "https://remote.example/activities/u1",
            "actor": BOB_ACTOR,
            "object": {
                "type": "Follow",
                "id": "https://remote.example/activities/f1",
                "actor": BOB_ACTOR,
                "object": actor_url,
            },
        },
    )
    assert _followers_of(config, actor_url) == []


def test_incoming_actor_delete_removes_follower(engine, tmp_path):
    """A Delete of the follower's actor removes the stored follower."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config)
    actor_url = user.actor_url

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))
    storage.store_follower(_follower(BOB_ACTOR, datetime.now(timezone.utc), BOB_DOC, target=actor_url))

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Delete",
            "id": "https://remote.example/activities/d1",
            "actor": BOB_ACTOR,
            "object": {"id": BOB_ACTOR, "type": "Person"},
        },
    )
    assert _followers_of(config, actor_url) == []


def test_incoming_delete_of_other_object_keeps_follower(engine, tmp_path):
    """A Delete targeting an object (not the actor) keeps the follower."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config)
    actor_url = user.actor_url

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))
    storage.store_follower(_follower(BOB_ACTOR, datetime.now(timezone.utc), BOB_DOC, target=actor_url))

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Delete",
            "id": "https://remote.example/activities/d2",
            "actor": BOB_ACTOR,
            "object": "https://remote.example/notes/1",
        },
    )
    assert [f.actor_id for f in _followers_of(config, actor_url)] == [BOB_ACTOR]


def test_incoming_follow_visible_via_api(fed_client, fed_config, engine, fed_user):
    """End-to-end: an incoming Follow shows up on the followers API."""
    from pubby.handlers._inbox import InboxProcessor

    storage = get_federation_storage(fed_config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    def _init(*a, **k):
        init_db(engine=engine, force=True)

    real_process = InboxProcessor.process

    def _process_unverified(self, *args, **kwargs):
        kwargs["skip_verification"] = True
        return real_process(self, *args, **kwargs)

    with (
        patch("songhive.tasks.federation.init_db", _init),
        patch("songhive.tasks.federation.load_config", lambda *_, **__: fed_config),
        patch("pubby.handlers._inbox.InboxProcessor._deliver_to_inbox", lambda *a, **k: True),
        patch.object(InboxProcessor, "process", _process_unverified),
    ):
        process_incoming(
            {
                "type": "Follow",
                "id": "https://remote.example/activities/f1",
                "actor": BOB_ACTOR,
                "object": REGULAR_ACTOR,
            },
            username="regular",
        )

    response = fed_client.get("/api/v1/users/regular/followers")
    assert response.status_code == 200
    data = response.json()
    assert [f["actor_url"] for f in data] == [BOB_ACTOR]
    assert data[0]["display_name"] == "Bob Remote"

    profile = fed_client.get("/api/v1/users/regular")
    assert profile.json()["followers_count"] == 1


def test_incoming_object_follow_is_not_an_actor_follower(engine, tmp_path):
    """A Follow of a local object (thread subscription) is stored scoped to
    the object and never surfaces as an actor follower."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config)
    actor_url = user.actor_url
    object_url = f"{actor_url}/objects/thread-1"

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f2",
            "actor": BOB_ACTOR,
            "object": object_url,
        },
    )

    # Stored under the object's id — a thread subscription…
    assert [f.actor_id for f in storage.get_followers(actor_id=object_url)] == [BOB_ACTOR]
    # …but the actor's own followers (API + counts) exclude it. Unassigned
    # rows remain visible via ``get_followers(actor_id=...)``, so filter on
    # the recorded target to prove the actor count is untouched.
    actor_followers = [f for f in _followers_of(config, actor_url) if f.target_actor_id == actor_url]
    assert actor_followers == []
    assert count_followers_by_actor(storage).get(actor_url, 0) == 0


def test_incoming_follow_of_remote_object_dropped(engine, tmp_path):
    """A Follow targeting a remote object is ignored: nothing is stored."""
    config = _task_config(tmp_path)
    _seed_user(engine, config)

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    result = _run_process_incoming(
        engine,
        config,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f3",
            "actor": BOB_ACTOR,
            "object": "https://remote.example/notes/1",
        },
    )

    assert result is None
    assert storage.get_followers() == []


def test_incoming_undo_follow_removes_object_subscription(engine, tmp_path):
    """Undo(Follow) of an object follow removes only that subscription."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config)
    actor_url = user.actor_url
    object_url = f"{actor_url}/objects/thread-1"

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f4",
            "actor": BOB_ACTOR,
            "object": object_url,
        },
    )
    assert storage.get_followers(actor_id=object_url)

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Undo",
            "id": "https://remote.example/activities/u2",
            "actor": BOB_ACTOR,
            "object": {
                "type": "Follow",
                "id": "https://remote.example/activities/f4",
                "actor": BOB_ACTOR,
                "object": object_url,
            },
        },
    )
    assert storage.get_followers(actor_id=object_url) == []


def _follow_request(actor_id, target=REGULAR_ACTOR, requested_at=None, actor_data=None):
    return FollowRequest(
        actor_id=actor_id,
        target_actor_id=target,
        inbox=f"{actor_id}/inbox",
        actor_data=actor_data or {},
        activity={
            "type": "Follow",
            "id": f"{actor_id}#follow-1",
            "actor": actor_id,
            "object": target,
        },
        requested_at=requested_at or datetime.now(timezone.utc),
    )


def _store_request(fed_config, *requests):
    storage = create_activitypub_storage(fed_config.database.url)
    for request in requests:
        storage.store_follow_request(request)


def _requests_of(config, actor_url):
    storage = get_federation_storage(config.database.url)
    return storage.get_follow_requests(actor_url)


def _notifications_for(engine, user_id):
    """Fetch all Notification rows for a user from the test engine."""
    from sqlalchemy import select

    from songhive.models.base import reset_db
    from songhive.models.notification import Notification

    init_db(engine=engine, force=True)

    async def _load():
        async with get_session() as session:
            result = await session.execute(select(Notification).where(Notification.user_id == user_id))
            return list(result.scalars().all())

    rows = asyncio.run(_load())
    reset_db()
    return rows


def _auth(config, user):
    return {"Authorization": f"Bearer {create_access_token(user.id, config.auth.secret_key)}"}


def test_incoming_follow_manual_policy_stores_request(engine, tmp_path):
    """A Follow to a ``manual`` user's inbox stores a pending request, not a follower."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config, followers_approval="manual")
    actor_url = user.actor_url

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    deliveries = []
    _run_process_incoming(
        engine,
        config,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f-manual",
            "actor": BOB_ACTOR,
            "object": actor_url,
        },
        deliveries=deliveries,
    )

    requests = _requests_of(config, actor_url)
    assert [r.actor_id for r in requests] == [BOB_ACTOR]
    assert requests[0].target_actor_id == actor_url
    assert requests[0].actor_data["preferredUsername"] == "bob"
    assert requests[0].activity["id"] == "https://remote.example/activities/f-manual"
    # No follower is stored and no Accept is sent until approval.
    assert _followers_of(config, actor_url) == []
    assert deliveries == []


def test_incoming_follow_manual_policy_flags_notification(engine, tmp_path):
    """A held follow creates a notification carrying ``follow_request_pending``."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config, followers_approval="manual")
    actor_url = user.actor_url

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f-flag",
            "actor": BOB_ACTOR,
            "object": actor_url,
        },
    )

    rows = _notifications_for(engine, user.id)
    assert len(rows) == 1
    assert rows[0].type == "follow"
    assert rows[0].actor_url == BOB_ACTOR
    assert rows[0].payload["follow_request_pending"] is True


def test_incoming_follow_accept_policy_notification_not_flagged(engine, tmp_path):
    """An auto-accepted follow creates a plain follow notification."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config)
    actor_url = user.actor_url

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f-plain",
            "actor": BOB_ACTOR,
            "object": actor_url,
        },
    )

    rows = _notifications_for(engine, user.id)
    assert len(rows) == 1
    assert "follow_request_pending" not in (rows[0].payload or {})


def test_incoming_follow_reject_policy_replies_reject(engine, tmp_path):
    """A Follow to a ``reject`` user's inbox is answered with a Reject."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config, followers_approval="reject")
    actor_url = user.actor_url

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    deliveries = []
    _run_process_incoming(
        engine,
        config,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f-reject",
            "actor": BOB_ACTOR,
            "object": actor_url,
        },
        deliveries=deliveries,
    )

    assert _requests_of(config, actor_url) == []
    assert _followers_of(config, actor_url) == []

    assert len(deliveries) == 1
    args, _ = deliveries[0]
    inbox, activity = args[1], args[2]
    assert inbox == f"{BOB_ACTOR}/inbox"
    assert activity["type"] == "Reject"
    assert activity["actor"] == actor_url
    assert activity["object"]["id"] == "https://remote.example/activities/f-reject"

    # A rejected follow produces no notification.
    assert _notifications_for(engine, user.id) == []


def test_incoming_undo_follow_removes_pending_request(engine, tmp_path):
    """Undo(Follow) also clears a still-pending follow request."""
    config = _task_config(tmp_path)
    user = _seed_user(engine, config, followers_approval="manual")
    actor_url = user.actor_url

    storage = get_federation_storage(config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f-undo",
            "actor": BOB_ACTOR,
            "object": actor_url,
        },
    )
    assert _requests_of(config, actor_url)

    _run_process_incoming(
        engine,
        config,
        {
            "type": "Undo",
            "id": "https://remote.example/activities/u-undo",
            "actor": BOB_ACTOR,
            "object": {
                "type": "Follow",
                "id": "https://remote.example/activities/f-undo",
                "actor": BOB_ACTOR,
                "object": actor_url,
            },
        },
    )
    assert _requests_of(config, actor_url) == []


def test_get_actor_follow_requests_sorts_and_filters(fed_config):
    """get_actor_follow_requests returns only the target's requests, newest first."""
    storage = create_activitypub_storage(fed_config.database.url)
    now = datetime.now(timezone.utc)
    storage.store_follow_request(_follow_request(BOB_ACTOR, requested_at=now - timedelta(days=1)))
    storage.store_follow_request(_follow_request(CAROL_ACTOR, requested_at=now))
    storage.store_follow_request(
        _follow_request("https://remote.example/users/dan", target="https://other.example/users/x")
    )

    requests = get_actor_follow_requests(storage, REGULAR_ACTOR)
    assert [r.actor_id for r in requests] == [CAROL_ACTOR, BOB_ACTOR]


async def test_list_follow_requests_owner(fed_client, fed_config, fed_user):
    """GET /me/follow-requests returns the user's pending requests, newest first."""
    now = datetime.now(timezone.utc)
    _store_request(
        fed_config,
        _follow_request(BOB_ACTOR, requested_at=now - timedelta(days=1), actor_data=BOB_DOC),
        _follow_request(CAROL_ACTOR, requested_at=now),
        # A request targeting another actor must not be listed.
        _follow_request("https://remote.example/users/dan", target="https://music.example.com/users/other"),
    )

    response = fed_client.get("/api/v1/users/me/follow-requests", headers=_auth(fed_config, fed_user))
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"

    data = response.json()
    assert [r["actor_url"] for r in data] == [CAROL_ACTOR, BOB_ACTOR]
    assert data[1]["display_name"] == "Bob Remote"
    assert data[1]["avatar_url"] == "https://remote.example/bob.png"
    assert data[1]["requested_at"]


async def test_list_follow_requests_pagination(fed_client, fed_config, fed_user):
    """limit/offset paginate the pending requests list."""
    now = datetime.now(timezone.utc)
    _store_request(
        fed_config,
        *[
            _follow_request(f"https://remote.example/users/u{i}", requested_at=now - timedelta(minutes=i))
            for i in range(3)
        ],
    )

    page1 = fed_client.get(
        "/api/v1/users/me/follow-requests",
        params={"limit": 2, "offset": 0},
        headers=_auth(fed_config, fed_user),
    )
    page2 = fed_client.get(
        "/api/v1/users/me/follow-requests",
        params={"limit": 2, "offset": 2},
        headers=_auth(fed_config, fed_user),
    )
    assert page1.status_code == 200 and page2.status_code == 200
    assert page1.headers["X-Total-Count"] == "3"
    assert [r["actor_url"] for r in page1.json()] == [
        "https://remote.example/users/u0",
        "https://remote.example/users/u1",
    ]
    assert [r["actor_url"] for r in page2.json()] == ["https://remote.example/users/u2"]


async def test_list_follow_requests_unauthenticated(fed_client):
    """The follow-requests list requires authentication."""
    response = fed_client.get("/api/v1/users/me/follow-requests")
    assert response.status_code == 401


async def test_list_follow_requests_federation_disabled(client, regular_user, config):
    """Without federation the requests list is empty."""
    response = client.get("/api/v1/users/me/follow-requests", headers=_auth(config, regular_user))
    assert response.status_code == 200
    assert response.json() == []


async def test_accept_follow_request(fed_client, fed_config, fed_user, db_session):
    """Accepting promotes the request to a follower and enqueues the Accept."""
    fed_user.private_key_pem = "private-key"
    await db_session.commit()

    _store_request(fed_config, _follow_request(BOB_ACTOR, actor_data=BOB_DOC))

    with patch("songhive.tasks.federation.deliver_activity") as deliver_mock:
        response = fed_client.post(
            "/api/v1/users/me/follow-requests/accept",
            headers=_auth(fed_config, fed_user),
            json={"actor_url": BOB_ACTOR},
        )

    assert response.status_code == 204

    storage = get_federation_storage(fed_config.database.url)
    assert storage.get_follow_request(BOB_ACTOR, REGULAR_ACTOR) is None
    followers = [f for f in storage.get_followers(actor_id=REGULAR_ACTOR) if f.target_actor_id == REGULAR_ACTOR]
    assert [f.actor_id for f in followers] == [BOB_ACTOR]

    deliver_mock.delay.assert_called_once()
    activity, inbox, key_id, _ = deliver_mock.delay.call_args.args
    assert inbox == f"{BOB_ACTOR}/inbox"
    assert activity["type"] == "Accept"
    assert activity["actor"] == REGULAR_ACTOR
    assert activity["object"]["type"] == "Follow"
    assert activity["object"]["actor"] == BOB_ACTOR
    assert key_id == f"{REGULAR_ACTOR}#main-key"


async def test_reject_follow_request(fed_client, fed_config, fed_user, db_session):
    """Rejecting drops the request and enqueues the Reject; no follower is stored."""
    fed_user.private_key_pem = "private-key"
    await db_session.commit()

    _store_request(fed_config, _follow_request(BOB_ACTOR))

    with patch("songhive.tasks.federation.deliver_activity") as deliver_mock:
        response = fed_client.post(
            "/api/v1/users/me/follow-requests/reject",
            headers=_auth(fed_config, fed_user),
            json={"actor_url": BOB_ACTOR},
        )

    assert response.status_code == 204

    storage = get_federation_storage(fed_config.database.url)
    assert storage.get_follow_request(BOB_ACTOR, REGULAR_ACTOR) is None
    assert storage.get_followers(actor_id=REGULAR_ACTOR) == []

    deliver_mock.delay.assert_called_once()
    activity, inbox, _, _ = deliver_mock.delay.call_args.args
    assert inbox == f"{BOB_ACTOR}/inbox"
    assert activity["type"] == "Reject"
    assert activity["object"]["actor"] == BOB_ACTOR


async def test_decide_follow_request_resolves_notification(fed_client, fed_config, fed_user, db_session):
    """Deciding a request rewrites the pending flag on its notification."""
    from sqlalchemy import select

    from songhive.models.notification import Notification

    fed_user.private_key_pem = "private-key"
    db_session.add(
        Notification(
            user_id=fed_user.id,
            type="follow",
            actor_url=BOB_ACTOR,
            source_url=BOB_ACTOR,
            payload={"follow_request_pending": True},
        )
    )
    await db_session.commit()

    _store_request(fed_config, _follow_request(BOB_ACTOR))

    with patch("songhive.tasks.federation.deliver_activity"):
        response = fed_client.post(
            "/api/v1/users/me/follow-requests/accept",
            headers=_auth(fed_config, fed_user),
            json={"actor_url": BOB_ACTOR},
        )
    assert response.status_code == 204

    result = await db_session.execute(select(Notification).where(Notification.user_id == fed_user.id))
    notification = result.scalar_one()
    assert notification.payload == {"follow_request_status": "accepted"}


async def test_decide_follow_request_not_found(fed_client, fed_config, fed_user, db_session):
    """Deciding a request that does not exist returns 404."""
    fed_user.private_key_pem = "private-key"
    await db_session.commit()

    response = fed_client.post(
        "/api/v1/users/me/follow-requests/accept",
        headers=_auth(fed_config, fed_user),
        json={"actor_url": BOB_ACTOR},
    )
    assert response.status_code == 404


async def test_decide_follow_request_other_actor_not_found(fed_client, fed_config, fed_user, db_session):
    """A request targeting another actor cannot be resolved by this user."""
    fed_user.private_key_pem = "private-key"
    await db_session.commit()

    _store_request(
        fed_config,
        _follow_request(BOB_ACTOR, target="https://music.example.com/users/other"),
    )

    response = fed_client.post(
        "/api/v1/users/me/follow-requests/accept",
        headers=_auth(fed_config, fed_user),
        json={"actor_url": BOB_ACTOR},
    )
    assert response.status_code == 404

    storage = get_federation_storage(fed_config.database.url)
    assert storage.get_follow_request(BOB_ACTOR, "https://music.example.com/users/other") is not None


async def test_decide_follow_request_federation_disabled(client, regular_user, config):
    """Without federation the decision endpoints return 404."""
    response = client.post(
        "/api/v1/users/me/follow-requests/accept",
        headers=_auth(config, regular_user),
        json={"actor_url": BOB_ACTOR},
    )
    assert response.status_code == 404
