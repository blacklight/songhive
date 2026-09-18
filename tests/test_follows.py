"""
Tests for outbound follows — ``songhive.services.follows`` and the
``/api/v1/users/me/follows`` endpoints.

Pubby stores the inbound side (followers of our actors); the ``follows``
table stores the outbound side — who our local users follow, whether the
actor lives on this instance or a remote one.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.federation.storage import create_activitypub_storage
from songhive.models.activity import Activity
from songhive.models.base import init_db
from songhive.models.follow import FOLLOW_STATE_ACCEPTED, FOLLOW_STATE_PENDING
from songhive.models.remote_object import RemoteObject
from songhive.models.user import FollowersApproval, User
from songhive.services import federation as federation_service
from songhive.services import follows as follows_service
from songhive.services import remote_content
from songhive.services.remote_content import RemoteActorResult

BOB_ACTOR = "https://remote.example/users/bob"
BOB_INBOX = "https://remote.example/users/bob/inbox"


def _fed_config(config, tmp_path):
    """Return a federation-enabled copy of the test config."""
    fed = config.model_copy(deep=True)
    fed.federation.enabled = True
    fed.federation.instance_domain = "music.example.com"
    fed.federation.private_key_path = Path(fed.storage.local_path).parent / "actor.pem"
    return fed


@pytest.fixture
def fed_config(config, tmp_path):
    return _fed_config(config, tmp_path)


@pytest.fixture
def fed_app(fed_config, engine):
    init_db(engine=engine, force=True)
    return create_app(fed_config)


@pytest.fixture
def fed_client(fed_app, db_session, fake_redis_server, monkeypatch):
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
def deliver_mock(monkeypatch):
    """Capture activities enqueued on ``deliver_activity``."""
    mock = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", mock)
    return mock


async def _provision(db_session, fed_config, *users):
    """
    Provision actor keys for ``users`` and commit.

    Mirrors production, where ``create_user`` provisions federation
    credentials at signup — and keeps pubby's sync storage writes from
    colliding with an open write transaction on sqlite.
    """
    for user in users:
        federation_service.ensure_user_actor(user, fed_config)
    await db_session.commit()
    for user in users:
        # Force a reload so attributes that were never set (``links`` and
        # friends) are populated instead of triggering a lazy load mid-call.
        await db_session.execute(select(User).where(User.id == user.id).execution_options(populate_existing=True))


def _remote_actor(actor_url: str = BOB_ACTOR, inbox: str = BOB_INBOX) -> RemoteActorResult:
    return RemoteActorResult(
        actor_url=actor_url,
        username="bob",
        domain="remote.example",
        display_name="Bob Remote",
        inbox_url=inbox,
    )


def _patch_remote_lookup(monkeypatch, actor: RemoteActorResult):
    async def _lookup(session, config, target, **kwargs):
        return actor

    monkeypatch.setattr(remote_content, "lookup_remote_actor", _lookup)


async def test_follow_local_user_accepted(db_session, fed_config, regular_user, other_user):
    """A local follow against an auto-accepting user is stored as accepted."""
    await _provision(db_session, fed_config, regular_user, other_user)

    row = await follows_service.follow_user(db_session, fed_config, regular_user, "other")

    assert row.state == FOLLOW_STATE_ACCEPTED
    assert row.target_user_id == str(other_user.id)
    assert row.target_actor_url == other_user.actor_url
    assert row.activity_id
    assert row.accepted_at is not None

    # The inbound side is recorded in pubby's follower table.
    storage = create_activitypub_storage(fed_config.database.url)
    followers = storage.get_followers(other_user.actor_url)
    assert [f.actor_id for f in followers] == [regular_user.actor_url]


async def test_follow_local_user_manual_pending(db_session, fed_config, regular_user, other_user):
    """A local follow against a manual-approval user stays pending."""
    other_user.followers_approval = FollowersApproval.MANUAL.value
    await _provision(db_session, fed_config, regular_user, other_user)

    row = await follows_service.follow_user(db_session, fed_config, regular_user, "other")

    assert row.state == FOLLOW_STATE_PENDING
    assert row.accepted_at is None

    storage = create_activitypub_storage(fed_config.database.url)
    requests = storage.get_follow_requests(other_user.actor_url)
    assert [r.actor_id for r in requests] == [regular_user.actor_url]


async def test_follow_local_user_reject_policy(db_session, fed_config, regular_user, other_user):
    """A local follow against a rejecting user fails with 403."""
    other_user.followers_approval = FollowersApproval.REJECT.value
    await _provision(db_session, fed_config, regular_user, other_user)

    with pytest.raises(follows_service.FollowError) as exc:
        await follows_service.follow_user(db_session, fed_config, regular_user, "other")
    assert exc.value.status_code == 403


async def test_follow_self_rejected(db_session, fed_config, regular_user):
    await _provision(db_session, fed_config, regular_user)
    with pytest.raises(follows_service.FollowError) as exc:
        await follows_service.follow_user(db_session, fed_config, regular_user, "regular")
    assert exc.value.status_code == 400


async def test_follow_unknown_user(db_session, fed_config, regular_user):
    await _provision(db_session, fed_config, regular_user)
    with pytest.raises(follows_service.FollowError) as exc:
        await follows_service.follow_user(db_session, fed_config, regular_user, "nobody")
    assert exc.value.status_code == 404


async def test_follow_remote_pending_and_delivers(db_session, fed_config, regular_user, deliver_mock, monkeypatch):
    """A remote follow stores a pending row and enqueues the Follow activity."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)

    row = await follows_service.follow_user(db_session, fed_config, regular_user, BOB_ACTOR)

    assert row.state == FOLLOW_STATE_PENDING
    assert row.target_user_id is None
    assert row.target_actor_url == BOB_ACTOR
    assert row.inbox_url == BOB_INBOX
    assert row.activity_id

    deliver_mock.delay.assert_called_once()
    activity, inbox_url, key_id, _key = deliver_mock.delay.call_args[0]
    assert activity["type"] == "Follow"
    assert activity["actor"] == regular_user.actor_url
    assert activity["object"] == BOB_ACTOR
    assert activity["id"] == row.activity_id
    assert inbox_url == BOB_INBOX
    assert key_id == f"{regular_user.actor_url}#main-key"


async def test_follow_remote_handle_resolves(db_session, fed_config, regular_user, deliver_mock, monkeypatch):
    """A ``@user@domain`` handle resolves through the remote actor lookup."""
    seen = {}

    async def _lookup(session, config, target, **kwargs):
        seen["target"] = target
        return _remote_actor()

    monkeypatch.setattr(remote_content, "lookup_remote_actor", _lookup)
    await _provision(db_session, fed_config, regular_user)

    row = await follows_service.follow_user(db_session, fed_config, regular_user, "@bob@remote.example")
    assert row.target_actor_url == BOB_ACTOR
    assert seen["target"] == "@bob@remote.example"


async def test_follow_duplicate_returns_existing(db_session, fed_config, regular_user, other_user, deliver_mock):
    """Re-following an already followed actor returns the stored row."""
    await _provision(db_session, fed_config, regular_user, other_user)
    first = await follows_service.follow_user(db_session, fed_config, regular_user, "other")
    second = await follows_service.follow_user(db_session, fed_config, regular_user, "other")
    assert second.id == first.id


async def test_unfollow_local(db_session, fed_config, regular_user, other_user):
    """Unfollowing a local user removes the row and the pubby follower."""
    await _provision(db_session, fed_config, regular_user, other_user)
    await follows_service.follow_user(db_session, fed_config, regular_user, "other")
    await db_session.commit()

    await follows_service.unfollow_user(db_session, fed_config, regular_user, other_user.actor_url)

    assert await follows_service.get_follow(db_session, regular_user.id, other_user.actor_url) is None
    storage = create_activitypub_storage(fed_config.database.url)
    assert storage.get_followers(other_user.actor_url) == []


async def test_unfollow_remote_delivers_undo(db_session, fed_config, regular_user, deliver_mock, monkeypatch):
    """Unfollowing a remote actor delivers ``Undo(Follow)`` to its inbox."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)
    row = await follows_service.follow_user(db_session, fed_config, regular_user, BOB_ACTOR)
    await db_session.commit()
    deliver_mock.reset_mock()

    await follows_service.unfollow_user(db_session, fed_config, regular_user, BOB_ACTOR)

    assert await follows_service.get_follow(db_session, regular_user.id, BOB_ACTOR) is None
    deliver_mock.delay.assert_called_once()
    activity, inbox_url, _key_id, _key = deliver_mock.delay.call_args[0]
    assert activity["type"] == "Undo"
    assert inbox_url == BOB_INBOX
    undo_obj = activity["object"]
    assert undo_obj["type"] == "Follow"
    assert undo_obj["id"] == row.activity_id
    assert undo_obj["actor"] == regular_user.actor_url
    assert undo_obj["object"] == BOB_ACTOR


async def test_unfollow_not_following(db_session, fed_config, regular_user):
    await _provision(db_session, fed_config, regular_user)
    with pytest.raises(follows_service.FollowError) as exc:
        await follows_service.unfollow_user(db_session, fed_config, regular_user, BOB_ACTOR)
    assert exc.value.status_code == 404


async def test_apply_follow_decision_accept(db_session, fed_config, regular_user, deliver_mock, monkeypatch):
    """An inbound ``Accept`` marks the pending remote follow accepted."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)
    row = await follows_service.follow_user(db_session, fed_config, regular_user, BOB_ACTOR)
    follow_activity = {
        "id": row.activity_id,
        "type": "Follow",
        "actor": regular_user.actor_url,
        "object": BOB_ACTOR,
    }

    matched = await follows_service.apply_follow_decision(
        db_session,
        activity={"type": "Accept", "actor": BOB_ACTOR, "object": follow_activity},
    )

    assert matched is True
    await db_session.refresh(row)
    assert row.state == FOLLOW_STATE_ACCEPTED
    assert row.accepted_at is not None


async def test_apply_follow_decision_reject_removes_row(
    db_session, fed_config, regular_user, deliver_mock, monkeypatch
):
    """An inbound ``Reject`` drops the pending remote follow."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)
    row = await follows_service.follow_user(db_session, fed_config, regular_user, BOB_ACTOR)

    matched = await follows_service.apply_follow_decision(
        db_session,
        activity={
            "type": "Reject",
            "actor": BOB_ACTOR,
            "object": {
                "id": row.activity_id,
                "type": "Follow",
                "actor": regular_user.actor_url,
                "object": BOB_ACTOR,
            },
        },
    )

    assert matched is True
    assert await follows_service.get_follow(db_session, regular_user.id, BOB_ACTOR) is None


async def test_apply_follow_decision_wrong_actor_ignored(
    db_session, fed_config, regular_user, deliver_mock, monkeypatch
):
    """An ``Accept`` from an unrelated actor does not match the follow."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)
    row = await follows_service.follow_user(db_session, fed_config, regular_user, BOB_ACTOR)

    matched = await follows_service.apply_follow_decision(
        db_session,
        activity={
            "type": "Accept",
            "actor": "https://evil.example/users/mallory",
            "object": {
                "id": row.activity_id,
                "type": "Follow",
                "actor": regular_user.actor_url,
                "object": BOB_ACTOR,
            },
        },
    )

    assert matched is False
    await db_session.refresh(row)
    assert row.state == FOLLOW_STATE_PENDING


async def test_apply_follow_decision_bare_id(db_session, fed_config, regular_user, deliver_mock, monkeypatch):
    """An ``Accept`` whose object is the bare activity id still matches."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)
    row = await follows_service.follow_user(db_session, fed_config, regular_user, BOB_ACTOR)

    matched = await follows_service.apply_follow_decision(
        db_session,
        activity={"type": "Accept", "actor": BOB_ACTOR, "object": row.activity_id},
    )
    assert matched is True
    await db_session.refresh(row)
    assert row.state == FOLLOW_STATE_ACCEPTED


async def test_apply_local_follow_decision(db_session, fed_config, regular_user, other_user):
    """A local owner's request decision folds back into the requester's row."""
    other_user.followers_approval = FollowersApproval.MANUAL.value
    await _provision(db_session, fed_config, regular_user, other_user)
    row = await follows_service.follow_user(db_session, fed_config, regular_user, "other")

    matched = await follows_service.apply_local_follow_decision(
        db_session,
        actor_url=regular_user.actor_url,
        target_actor_url=other_user.actor_url,
        accept=True,
    )
    assert matched is True
    await db_session.refresh(row)
    assert row.state == FOLLOW_STATE_ACCEPTED


async def test_actor_is_followed(db_session, fed_config, regular_user, deliver_mock, monkeypatch):
    """``actor_is_followed`` gates remote-activity materialization."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)
    assert await follows_service.actor_is_followed(db_session, BOB_ACTOR) is False

    await follows_service.follow_user(db_session, fed_config, regular_user, BOB_ACTOR)
    assert await follows_service.actor_is_followed(db_session, BOB_ACTOR) is True


async def test_api_follow_and_unfollow_local(
    fed_client, db_session, fed_config, regular_user, other_user, auth_headers
):
    """POST/DELETE /me/follows drive the local follow lifecycle."""
    await _provision(db_session, fed_config, regular_user, other_user)

    response = fed_client.post(
        "/api/v1/users/me/follows",
        json={"actor_url": "other"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["state"] == FOLLOW_STATE_ACCEPTED
    assert data["actor_url"] == other_user.actor_url

    response = fed_client.get("/api/v1/users/me/follows", headers=auth_headers(regular_user))
    assert response.status_code == 200
    assert [f["actor_url"] for f in response.json()] == [other_user.actor_url]

    response = fed_client.request(
        "DELETE",
        "/api/v1/users/me/follows",
        json={"actor_url": "other"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 204
    response = fed_client.get("/api/v1/users/me/follows", headers=auth_headers(regular_user))
    assert response.json() == []


async def test_api_follow_requires_auth(fed_client):
    response = fed_client.post("/api/v1/users/me/follows", json={"actor_url": "someone"})
    assert response.status_code == 401


async def test_api_follow_remote_pending(
    fed_client, db_session, fed_config, regular_user, auth_headers, deliver_mock, monkeypatch
):
    """The API reports remote follows as pending until the remote answers."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)

    response = fed_client.post(
        "/api/v1/users/me/follows",
        json={"actor_url": "@bob@remote.example"},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["state"] == FOLLOW_STATE_PENDING
    assert data["actor_url"] == BOB_ACTOR
    assert data["handle"] == "bob@remote.example"
    deliver_mock.delay.assert_called_once()


async def test_api_public_follows_lists_accepted_only(
    fed_client, db_session, fed_config, regular_user, other_user, auth_headers, deliver_mock, monkeypatch
):
    """``/{username}/follows`` hides pending rows except for the owner."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user, other_user)
    await follows_service.follow_user(db_session, fed_config, regular_user, "other")
    await follows_service.follow_user(db_session, fed_config, regular_user, BOB_ACTOR)
    await db_session.commit()

    # Anonymous viewer: only the accepted local follow is listed.
    response = fed_client.get("/api/v1/users/regular/follows")
    assert response.status_code == 200
    assert [f["actor_url"] for f in response.json()] == [other_user.actor_url]

    # Owner: the pending remote follow is included.
    response = fed_client.get("/api/v1/users/regular/follows", headers=auth_headers(regular_user))
    assert response.status_code == 200
    urls = {f["actor_url"] for f in response.json()}
    assert urls == {other_user.actor_url, BOB_ACTOR}


async def test_api_profile_reports_follow_state(
    fed_client, db_session, fed_config, regular_user, other_user, auth_headers
):
    """The viewer's follow relationship surfaces on the target's profile."""
    await _provision(db_session, fed_config, regular_user, other_user)
    await follows_service.follow_user(db_session, fed_config, regular_user, "other")
    await db_session.commit()

    response = fed_client.get("/api/v1/users/other", headers=auth_headers(regular_user))
    assert response.status_code == 200
    data = response.json()
    assert data["follow_state"] == FOLLOW_STATE_ACCEPTED
    assert data["follows_count"] >= 0

    # The followed user's profile shows their outbound count.
    response = fed_client.get("/api/v1/users/regular")
    assert response.json()["follows_count"] == 1


async def test_api_follow_request_flow_updates_row(
    fed_client, db_session, fed_config, regular_user, other_user, auth_headers, deliver_mock
):
    """Accepting a local follow request flips the requester's row to accepted."""
    other_user.followers_approval = FollowersApproval.MANUAL.value
    await _provision(db_session, fed_config, regular_user, other_user)
    await follows_service.follow_user(db_session, fed_config, regular_user, "other")
    await db_session.commit()

    response = fed_client.post(
        "/api/v1/users/me/follow-requests/accept",
        json={"actor_url": regular_user.actor_url},
        headers=auth_headers(other_user),
    )
    assert response.status_code == 204

    row = await follows_service.get_follow(db_session, regular_user.id, other_user.actor_url)
    assert row is not None
    assert row.state == FOLLOW_STATE_ACCEPTED


# ---------------------------------------------------------------------------
# Stale remote-activity pruning
# ---------------------------------------------------------------------------


def _remote_activity(source_id: str, days_old: int, **overrides) -> Activity:
    params = {
        "entity_type": "remote",
        "entity_id": source_id.rsplit("/", 1)[-1][:64],
        "activity_type": "create",
        "source_type": "remote",
        "source_actor": "https://remote.example/users/bob",
        "source_id": source_id,
        "visibility": "public",
        "published_at": datetime.now(timezone.utc) - timedelta(days=days_old),
    }
    params.update(overrides)
    return Activity(**params)


def _remote_object(canonical_url: str) -> RemoteObject:
    return RemoteObject(
        canonical_url=canonical_url,
        domain="remote.example",
        object_type="Note",
        actor_url="https://remote.example/users/bob",
        visibility="public",
        payload={"id": canonical_url},
    )


async def _activity_ids(db_session) -> set:
    rows = (await db_session.execute(select(Activity.id))).scalars().all()
    return set(rows)


async def test_prune_removes_stale_remote_activities(db_session):
    """Old remote activities with no interactions are pruned with their cache."""
    stale = _remote_activity("https://remote.example/notes/old", days_old=30)
    fresh = _remote_activity("https://remote.example/notes/new", days_old=1)
    db_session.add(stale)
    db_session.add(fresh)
    db_session.add(_remote_object(stale.source_id))
    db_session.add(_remote_object(fresh.source_id))
    db_session.add(_remote_object("https://remote.example/objects/bare"))
    await db_session.commit()

    result = await remote_content.prune_stale_remote_activities(db_session, older_than_days=7)

    assert result["pruned_activities"] == 1
    assert result["pruned_remote_objects"] == 1
    assert await _activity_ids(db_session) == {fresh.id}

    remaining = (await db_session.execute(select(RemoteObject.canonical_url))).scalars().all()
    assert set(remaining) == {fresh.source_id, "https://remote.example/objects/bare"}


async def test_prune_keeps_interacted_threads(db_session):
    """A remote activity with a live local reply is preserved."""
    parent = _remote_activity("https://remote.example/notes/thread", days_old=30)
    db_session.add(parent)
    await db_session.flush()
    reply = _remote_activity(
        "https://music.example.com/users/regular/objects/reply",
        days_old=30,
        source_type="local",
        source_actor="https://music.example.com/users/regular",
        activity_type="reply",
        in_reply_to_activity_id=str(parent.id),
    )
    db_session.add(reply)
    await db_session.commit()

    result = await remote_content.prune_stale_remote_activities(db_session, older_than_days=7)

    assert result["pruned_activities"] == 0
    assert await _activity_ids(db_session) == {parent.id, reply.id}


async def test_prune_dry_run_reports_without_deleting(db_session):
    """Dry run reports the candidates but deletes nothing."""
    stale = _remote_activity("https://remote.example/notes/old", days_old=30)
    db_session.add(stale)
    await db_session.commit()

    result = await remote_content.prune_stale_remote_activities(db_session, older_than_days=7, dry_run=True)

    assert result["candidates"] == 1
    assert result["pruned_activities"] == 0
    assert await _activity_ids(db_session) == {stale.id}
