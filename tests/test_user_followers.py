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
from pubby import Follower

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.config.schema import SonghiveConfig
from songhive.federation.actors import get_federation_storage
from songhive.federation.storage import create_activitypub_storage
from songhive.models.base import get_session, init_db
from songhive.services.auth import create_user
from songhive.services.federation import count_followers_by_actor, get_actor_followers
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


def _seed_user(engine, config, username="alice"):
    """Create a local user with provisioned actor keys on the test engine."""
    init_db(engine=engine, force=True)

    async def _create():
        async with get_session() as session:
            return await create_user(
                session,
                username=username,
                email=f"{username}@example.com",
                password="secret",
                config=config,
            )

    return asyncio.run(_create())


def _run_process_incoming(engine, config, activity, username="alice"):
    """Run the incoming-activity task against the test engine."""
    with (
        patch(
            "songhive.tasks.federation.init_db",
            lambda *a, **k: init_db(engine=engine, force=True),
        ),
        patch("songhive.tasks.federation.load_config", lambda *_, **__: config),
        patch(
            "pubby.handlers._inbox.InboxProcessor._deliver_to_inbox",
            lambda *a, **k: True,
        ),
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
    storage = get_federation_storage(fed_config.database.url)
    storage.cache_remote_actor(BOB_ACTOR, BOB_DOC, datetime.now(timezone.utc))

    def _init(*a, **k):
        init_db(engine=engine, force=True)

    with (
        patch("songhive.tasks.federation.init_db", _init),
        patch("songhive.tasks.federation.load_config", lambda *_, **__: fed_config),
        patch("pubby.handlers._inbox.InboxProcessor._deliver_to_inbox", lambda *a, **k: True),
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
