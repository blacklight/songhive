"""
Activity-subscription tests — the profile "bell": subscription CRUD, the
``activity`` notifications fanned out to subscribers on authored activity,
and the ``/api/v1/users/{username}/activity-subscription`` and
``/api/v1/remote/actors/{handle}/activity-subscription`` endpoints.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from songhive.api.app import create_app
from songhive.api.deps import get_db
from songhive.models._enums import Visibility
from songhive.models.activity import Activity
from songhive.models.artist import Artist
from songhive.models.base import init_db
from songhive.models.follow import FOLLOW_STATE_ACCEPTED, FOLLOW_STATE_PENDING, Follow
from songhive.models.notification import ActivitySubscription, Notification, NotificationPreference
from songhive.models.remote_object import RemoteObject
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import federation as federation_service
from songhive.services import remote_content
from songhive.services.activities import (
    create_status,
    like_activity,
    notify_remote_activity_subscribers,
    reply_to_activity,
)
from songhive.services.notifications import (
    is_subscribed_to_actor_activity,
    is_subscribed_to_user_activity,
    list_activity_subscriber_ids,
    list_actor_activity_subscriber_ids,
    subscribe_to_actor_activity,
    subscribe_to_user_activity,
    unsubscribe_from_actor_activity,
    unsubscribe_from_user_activity,
)
from songhive.services.remote_content import RemoteActorResult

BOB_ACTOR = "https://remote.example/users/bob"
BOB_INBOX = "https://remote.example/users/bob/inbox"


async def _make_artist(session, name: str = "Test Artist") -> Artist:
    """Create and persist a test artist."""
    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _make_track(session, owner: User | None, visibility: str = Visibility.PUBLIC.value) -> Track:
    """Create and persist a test track."""
    artist = await _make_artist(session)
    track = Track(
        title="Test Track",
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(track)
    await session.flush()
    return track


def _make_activity(entity_type: str, entity_id: str, **overrides) -> Activity:
    """Build a minimally valid Activity, allowing per-field overrides."""
    params = {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "activity_type": "create",
        "source_type": "local",
        "source_actor": "https://local.example/users/alice",
        "source_id": "https://local.example/users/alice/objects/1",
        "visibility": Visibility.PUBLIC.value,
    }
    params.update(overrides)
    return Activity(**params)


async def _notifications(db_session, user_id, type: str | None = None) -> list[Notification]:
    query = sa.select(Notification).where(Notification.user_id == user_id)
    if type is not None:
        query = query.where(Notification.type == type)
    return list((await db_session.execute(query)).scalars().all())


async def _make_remote_object(db_session, **kwargs) -> RemoteObject:
    """Create and persist a cached remote object."""
    row = RemoteObject(
        canonical_url=kwargs.get("canonical_url", "https://remote.example/objects/1"),
        domain="remote.example",
        object_type="Note",
        actor_url=kwargs.get("actor_url", BOB_ACTOR),
        visibility=kwargs.get("visibility", "public"),
        content=kwargs.get("content"),
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _make_remote_activity(remote_object: RemoteObject, **overrides) -> Activity:
    """Build a materialized remote activity for ``remote_object``."""
    params = {
        "entity_type": "remote",
        "entity_id": str(remote_object.id),
        "activity_type": "create",
        "source_type": "remote",
        "source_actor": BOB_ACTOR,
        "source_id": remote_object.canonical_url,
        "visibility": Visibility.PUBLIC.value,
        "payload": {
            "type": "Create",
            "actor": BOB_ACTOR,
            "object": {
                "id": remote_object.canonical_url,
                "type": "Note",
                "content": "<p>hello from bob</p>",
            },
        },
    }
    params.update(overrides)
    return Activity(**params)


# --- federation-enabled app fixtures (mirrors tests/test_follows.py) -------


@pytest.fixture
def fed_config(config):
    """Return a federation-enabled copy of the test config."""
    fed = config.model_copy(deep=True)
    fed.federation.enabled = True
    fed.federation.instance_domain = "music.example.com"
    fed.federation.private_key_path = Path(fed.storage.local_path).parent / "actor.pem"
    return fed


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
    """Provision actor keys for ``users`` and commit (pubby storage is sync)."""
    for user in users:
        federation_service.ensure_user_actor(user, fed_config)
    await db_session.commit()
    for user in users:
        await db_session.execute(sa.select(User).where(User.id == user.id).execution_options(populate_existing=True))


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


# ---------------------------------------------------------------------------
# subscription service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_creates_row(db_session, regular_user, other_user):
    """Subscribing persists one row and reports the state."""
    row = await subscribe_to_user_activity(db_session, regular_user.id, other_user.id)
    assert row.user_id == regular_user.id
    assert row.target_user_id == other_user.id
    assert await is_subscribed_to_user_activity(db_session, regular_user.id, other_user.id)
    assert await list_activity_subscriber_ids(db_session, other_user.id) == [regular_user.id]
    assert await list_activity_subscriber_ids(db_session, regular_user.id) == []


@pytest.mark.asyncio
async def test_subscribe_idempotent(db_session, regular_user, other_user):
    """A second subscribe returns the existing row rather than duplicating."""
    first = await subscribe_to_user_activity(db_session, regular_user.id, other_user.id)
    second = await subscribe_to_user_activity(db_session, regular_user.id, other_user.id)
    assert second.id == first.id
    count = await db_session.execute(sa.select(sa.func.count(ActivitySubscription.id)))
    assert count.scalar() == 1


@pytest.mark.asyncio
async def test_subscribe_self_rejected(db_session, regular_user):
    """Users cannot subscribe to their own activity."""
    with pytest.raises(ValueError):
        await subscribe_to_user_activity(db_session, regular_user.id, regular_user.id)
    assert not await is_subscribed_to_user_activity(db_session, regular_user.id, regular_user.id)


@pytest.mark.asyncio
async def test_unsubscribe_removes_row(db_session, regular_user, other_user):
    """Unsubscribing deletes the row and reports the removed count."""
    await subscribe_to_user_activity(db_session, regular_user.id, other_user.id)
    assert await unsubscribe_from_user_activity(db_session, regular_user.id, other_user.id) == 1
    assert not await is_subscribed_to_user_activity(db_session, regular_user.id, other_user.id)
    # Removing twice is a no-op.
    assert await unsubscribe_from_user_activity(db_session, regular_user.id, other_user.id) == 0


# ---------------------------------------------------------------------------
# activity notification fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_status_notifies_subscriber(db_session, config, regular_user, other_user):
    """A subscriber gets an ``activity`` notification for a new status."""
    await subscribe_to_user_activity(db_session, other_user.id, regular_user.id)

    status = await create_status(db_session, author=regular_user, config=config, status_text="hello world")

    rows = await _notifications(db_session, other_user.id, "activity")
    assert len(rows) == 1
    notification = rows[0]
    assert notification.actor_url == status.source_actor
    assert notification.source_url == status.source_id
    payload = notification.payload or {}
    assert payload["activity_id"] == status.source_id
    assert payload["activity_type"] == "create"
    assert payload["actor_name"] == regular_user.username
    assert payload["object_activity_id"] == str(status.id)
    assert payload["object_page_url"] == f"/activities/{status.id}"


@pytest.mark.asyncio
async def test_create_status_no_notification_without_subscription(db_session, config, regular_user, other_user):
    """Non-subscribed users get nothing."""
    await create_status(db_session, author=regular_user, config=config, status_text="hello world")
    assert await _notifications(db_session, other_user.id) == []


@pytest.mark.asyncio
async def test_create_status_author_never_notified(db_session, config, regular_user):
    """The author never gets an ``activity`` notification for their own post."""
    await create_status(db_session, author=regular_user, config=config, status_text="hello world")
    assert await _notifications(db_session, regular_user.id) == []


@pytest.mark.asyncio
async def test_private_status_not_fanned_out(db_session, config, regular_user, other_user):
    """A subscriber who cannot view the activity gets no notification."""
    await subscribe_to_user_activity(db_session, other_user.id, regular_user.id)

    await create_status(
        db_session,
        author=regular_user,
        config=config,
        status_text="secret",
        visibility=Visibility.PRIVATE.value,
    )

    assert await _notifications(db_session, other_user.id) == []


@pytest.mark.asyncio
async def test_mentioned_subscriber_gets_mention_only(db_session, config, regular_user, other_user):
    """A mentioned subscriber gets the direct mention, not a second activity row."""
    await subscribe_to_user_activity(db_session, other_user.id, regular_user.id)

    await create_status(db_session, author=regular_user, config=config, status_text="hey @other hi")

    assert len(await _notifications(db_session, other_user.id, "mention")) == 1
    assert await _notifications(db_session, other_user.id, "activity") == []


@pytest.mark.asyncio
async def test_like_notifies_subscriber_and_owner_once(db_session, config, regular_user, other_user, make_user):
    """A like fans out to the liker's subscribers; the liked owner keeps ``like``."""
    third = await make_user("third", email_verified=True)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    await subscribe_to_user_activity(db_session, third.id, other_user.id)
    # The liked owner also subscribes — they must get ``like``, not ``activity``.
    await subscribe_to_user_activity(db_session, regular_user.id, other_user.id)

    like = await like_activity(db_session, activity=activity, author=other_user)

    third_rows = await _notifications(db_session, third.id, "activity")
    assert len(third_rows) == 1
    assert third_rows[0].source_url == like.source_id
    payload = third_rows[0].payload or {}
    assert payload["activity_type"] == "like"
    assert payload["object_activity_id"] == str(activity.id)
    assert payload["target_url"] == activity.source_id

    owner_types = {n.type for n in await _notifications(db_session, regular_user.id)}
    assert owner_types == {"like"}


@pytest.mark.asyncio
async def test_reply_notifies_subscriber(db_session, config, regular_user, other_user, make_user):
    """A reply fans out to the replier's subscribers with the target context."""
    third = await make_user("third", email_verified=True)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    await subscribe_to_user_activity(db_session, third.id, other_user.id)

    reply = await reply_to_activity(
        db_session,
        activity=activity,
        author=other_user,
        config=config,
        status_text="nice track!",
    )

    third_rows = await _notifications(db_session, third.id, "activity")
    assert len(third_rows) == 1
    assert third_rows[0].source_url == reply.source_id
    payload = third_rows[0].payload or {}
    assert payload["activity_type"] == "reply"
    assert payload["object_activity_id"] == str(reply.id)
    assert payload["target_url"] == activity.source_id
    assert payload["target_object_activity_id"] == str(activity.id)

    # The replied-to owner gets the direct ``reply`` notification only.
    owner_types = {n.type for n in await _notifications(db_session, regular_user.id)}
    assert owner_types == {"reply"}


@pytest.mark.asyncio
async def test_activity_notification_respects_preferences(db_session, config, regular_user, other_user):
    """A disabled ``activity`` preference suppresses the notification row."""
    db_session.add(
        NotificationPreference(
            user_id=other_user.id,
            type="activity",
            in_app=False,
            email=False,
            email_digest=False,
        )
    )
    await db_session.flush()
    await subscribe_to_user_activity(db_session, other_user.id, regular_user.id)

    await create_status(db_session, author=regular_user, config=config, status_text="hello")

    assert await _notifications(db_session, other_user.id) == []


@pytest.mark.asyncio
async def test_unsubscribe_stops_notifications(db_session, config, regular_user, other_user):
    """After unsubscribing no further ``activity`` notifications arrive."""
    await subscribe_to_user_activity(db_session, other_user.id, regular_user.id)
    await unsubscribe_from_user_activity(db_session, other_user.id, regular_user.id)

    await create_status(db_session, author=regular_user, config=config, status_text="hello")

    assert await _notifications(db_session, other_user.id) == []


@pytest.mark.asyncio
async def test_inactive_subscriber_skipped(db_session, config, regular_user, other_user):
    """A subscriber deactivated after subscribing gets no notification."""
    await subscribe_to_user_activity(db_session, other_user.id, regular_user.id)
    other_user.is_active = False
    await db_session.flush()

    await create_status(db_session, author=regular_user, config=config, status_text="hello")

    assert await _notifications(db_session, other_user.id) == []


@pytest.mark.asyncio
async def test_retracting_activity_removes_notification(db_session, config, regular_user, other_user):
    """Retracting the authored activity removes the fanned-out notification."""
    from songhive.services.activities import retract_activity

    await subscribe_to_user_activity(db_session, other_user.id, regular_user.id)
    status = await create_status(db_session, author=regular_user, config=config, status_text="hello")
    assert len(await _notifications(db_session, other_user.id, "activity")) == 1

    await retract_activity(db_session, status)

    assert await _notifications(db_session, other_user.id) == []


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_endpoint(client, regular_user, other_user, auth_headers):
    """POST subscribes the authenticated user and reports the state."""
    response = client.post(
        f"/api/v1/users/{other_user.username}/activity-subscription",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["activity_subscribed"] is True
    # Federation is disabled in the default test config — no follow exists.
    assert body["follow_state"] is None

    profile = client.get(
        f"/api/v1/users/{other_user.username}",
        headers=auth_headers(regular_user),
    )
    assert profile.status_code == 200
    assert profile.json()["activity_subscribed"] is True


@pytest.mark.asyncio
async def test_subscribe_endpoint_idempotent(client, regular_user, other_user, auth_headers):
    """Subscribing twice returns the same state without error."""
    for _ in range(2):
        response = client.post(
            f"/api/v1/users/{other_user.username}/activity-subscription",
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_unsubscribe_endpoint(client, regular_user, other_user, auth_headers):
    """DELETE removes the subscription; a second delete stays 204."""
    client.post(
        f"/api/v1/users/{other_user.username}/activity-subscription",
        headers=auth_headers(regular_user),
    )
    for _ in range(2):
        response = client.delete(
            f"/api/v1/users/{other_user.username}/activity-subscription",
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 204

    profile = client.get(
        f"/api/v1/users/{other_user.username}",
        headers=auth_headers(regular_user),
    )
    assert profile.json()["activity_subscribed"] is False


@pytest.mark.asyncio
async def test_subscribe_endpoint_unauthenticated(client, other_user):
    """Anonymous callers cannot subscribe."""
    response = client.post(f"/api/v1/users/{other_user.username}/activity-subscription")
    assert response.status_code == 401
    response = client.delete(f"/api/v1/users/{other_user.username}/activity-subscription")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_subscribe_endpoint_self_rejected(client, regular_user, auth_headers):
    """Subscribing to oneself returns 400."""
    response = client.post(
        f"/api/v1/users/{regular_user.username}/activity-subscription",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_subscribe_endpoint_unknown_user(client, regular_user, auth_headers):
    """A missing target returns 404."""
    response = client.post(
        "/api/v1/users/nobody/activity-subscription",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_profile_activity_subscribed_defaults(client, db_session, regular_user, other_user, auth_headers):
    """Anonymous viewers and the profile owner always see ``false``."""
    await subscribe_to_user_activity(db_session, regular_user.id, other_user.id)

    response = client.get(f"/api/v1/users/{other_user.username}")
    assert response.status_code == 200
    assert response.json()["activity_subscribed"] is False

    response = client.get(
        f"/api/v1/users/{other_user.username}",
        headers=auth_headers(other_user),
    )
    assert response.status_code == 200
    assert response.json()["activity_subscribed"] is False


@pytest.mark.asyncio
async def test_delete_user_removes_subscriptions(db_session, regular_user, other_user, make_user):
    """Account deletion drops subscriptions in both directions."""
    from songhive.users import manager as user_manager

    third = await make_user("third", email_verified=True)
    await subscribe_to_user_activity(db_session, regular_user.id, other_user.id)
    await subscribe_to_user_activity(db_session, third.id, regular_user.id)

    await user_manager.delete_user(db_session, str(regular_user.id))

    remaining = await db_session.execute(sa.select(sa.func.count(ActivitySubscription.id)))
    assert remaining.scalar() == 0


# ---------------------------------------------------------------------------
# remote-actor subscription service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_remote_actor_creates_row(db_session, regular_user):
    """A remote subscription keys on the actor URL, not a users row."""
    row = await subscribe_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    assert row.user_id == regular_user.id
    assert row.target_actor_url == BOB_ACTOR
    assert row.target_user_id is None
    assert await is_subscribed_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    assert await list_actor_activity_subscriber_ids(db_session, BOB_ACTOR) == [regular_user.id]
    assert await list_actor_activity_subscriber_ids(db_session, "https://remote.example/users/carol") == []


@pytest.mark.asyncio
async def test_subscribe_remote_actor_idempotent(db_session, regular_user):
    """A second subscribe returns the existing row rather than duplicating."""
    first = await subscribe_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    second = await subscribe_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    assert second.id == first.id
    count = await db_session.execute(sa.select(sa.func.count(ActivitySubscription.id)))
    assert count.scalar() == 1


@pytest.mark.asyncio
async def test_subscribe_remote_actor_self_rejected(db_session, regular_user):
    """Users cannot subscribe to their own actor URL."""
    regular_user.actor_url = BOB_ACTOR
    await db_session.flush()
    with pytest.raises(ValueError):
        await subscribe_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    assert not await is_subscribed_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)


@pytest.mark.asyncio
async def test_unsubscribe_remote_actor(db_session, regular_user):
    """Unsubscribing deletes the row and reports the removed count."""
    await subscribe_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    assert await unsubscribe_from_actor_activity(db_session, regular_user.id, BOB_ACTOR) == 1
    assert not await is_subscribed_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    assert await unsubscribe_from_actor_activity(db_session, regular_user.id, BOB_ACTOR) == 0


@pytest.mark.asyncio
async def test_remote_actor_delete_removes_subscriptions(db_session, regular_user, other_user):
    """A remote actor deleting themselves drops every subscription on them."""
    from songhive.federation.incoming import retract_remote_object

    await subscribe_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    await subscribe_to_actor_activity(db_session, other_user.id, BOB_ACTOR)
    await subscribe_to_actor_activity(db_session, other_user.id, "https://remote.example/users/carol")

    await retract_remote_object(
        db_session,
        activity={"type": "Delete", "actor": BOB_ACTOR, "object": BOB_ACTOR},
    )

    assert not await is_subscribed_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    assert not await is_subscribed_to_actor_activity(db_session, other_user.id, BOB_ACTOR)
    # Subscriptions on other actors survive.
    assert await is_subscribed_to_actor_activity(db_session, other_user.id, "https://remote.example/users/carol")


# ---------------------------------------------------------------------------
# remote activity notification fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_activity_notifies_subscriber(db_session, config, regular_user, other_user):
    """A materialized remote post fans out an ``activity`` notification."""
    remote_object = await _make_remote_object(db_session, content="<p>hi</p>")
    row = _make_remote_activity(remote_object)
    db_session.add(row)
    await db_session.flush()
    await subscribe_to_actor_activity(db_session, other_user.id, BOB_ACTOR)

    await notify_remote_activity_subscribers(db_session, activity=row, config=config)

    rows = await _notifications(db_session, other_user.id, "activity")
    assert len(rows) == 1
    notification = rows[0]
    assert notification.actor_url == BOB_ACTOR
    assert notification.source_url == remote_object.canonical_url
    payload = notification.payload or {}
    assert payload["activity_id"] == remote_object.canonical_url
    assert payload["activity_type"] == "create"
    # No actor cache entry — the handle derived from the actor URL stands in.
    assert payload["actor_name"] == "bob@remote.example"
    assert payload["object_activity_id"] == str(row.id)
    assert payload["object_page_url"] == f"/activities/{row.id}"


@pytest.mark.asyncio
async def test_remote_activity_no_notification_without_subscription(db_session, config, regular_user, other_user):
    """Non-subscribed users get nothing for a remote activity."""
    remote_object = await _make_remote_object(db_session)
    row = _make_remote_activity(remote_object)
    db_session.add(row)
    await db_session.flush()

    await notify_remote_activity_subscribers(db_session, activity=row, config=config)

    assert await _notifications(db_session, other_user.id) == []


@pytest.mark.asyncio
async def test_remote_mentioned_activity_not_fanned_out(db_session, config, regular_user, other_user):
    """A mentioned-only remote post does not reach unrelated subscribers."""
    remote_object = await _make_remote_object(db_session)
    row = _make_remote_activity(remote_object, visibility=Visibility.MENTIONED.value)
    db_session.add(row)
    await db_session.flush()
    await subscribe_to_actor_activity(db_session, other_user.id, BOB_ACTOR)

    await notify_remote_activity_subscribers(db_session, activity=row, config=config)

    assert await _notifications(db_session, other_user.id) == []


@pytest.mark.asyncio
async def test_remote_reply_notifies_and_skips_owner(db_session, config, regular_user, other_user):
    """A remote reply carries the target context; the local owner is skipped."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    remote_object = await _make_remote_object(db_session)
    reply = _make_remote_activity(
        remote_object,
        activity_type="reply",
        entity_type="track",
        entity_id=str(track.id),
        in_reply_to_activity_id=str(parent.id),
    )
    db_session.add(reply)
    await db_session.flush()

    await subscribe_to_actor_activity(db_session, other_user.id, BOB_ACTOR)
    # The replied-to owner also subscribes — the direct reply notification
    # (issued by the inbox path) already covers them.
    await subscribe_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)

    await notify_remote_activity_subscribers(db_session, activity=reply, config=config)

    rows = await _notifications(db_session, other_user.id, "activity")
    assert len(rows) == 1
    payload = rows[0].payload or {}
    assert payload["activity_type"] == "reply"
    assert payload["object_activity_id"] == str(reply.id)
    assert payload["target_url"] == parent.source_id
    assert payload["target_object_activity_id"] == str(parent.id)
    assert await _notifications(db_session, regular_user.id) == []


@pytest.mark.asyncio
async def test_remote_announce_notifies_with_target(db_session, config, regular_user, other_user):
    """A remote boost links and describes the boosted activity."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    remote_object = await _make_remote_object(db_session, canonical_url="https://remote.example/announces/1")
    announce = _make_remote_activity(
        remote_object,
        activity_type="announce",
        entity_type="track",
        entity_id=str(track.id),
        in_reply_to_activity_id=str(parent.id),
        payload={
            "type": "Announce",
            "actor": BOB_ACTOR,
            "id": "https://remote.example/announces/1",
            "object": parent.source_id,
        },
    )
    db_session.add(announce)
    await db_session.flush()
    await subscribe_to_actor_activity(db_session, other_user.id, BOB_ACTOR)

    await notify_remote_activity_subscribers(db_session, activity=announce, config=config)

    rows = await _notifications(db_session, other_user.id, "activity")
    assert len(rows) == 1
    payload = rows[0].payload or {}
    assert payload["activity_type"] == "announce"
    assert payload["activity_id"] == "https://remote.example/announces/1"
    # The card describes the boosted activity, not the boost wrapper.
    assert payload["object_activity_id"] == str(parent.id)
    assert payload["target_url"] == parent.source_id


@pytest.mark.asyncio
async def test_remote_unsubscribe_stops_notifications(db_session, config, regular_user, other_user):
    """After unsubscribing no further remote ``activity`` notifications arrive."""
    remote_object = await _make_remote_object(db_session)
    row = _make_remote_activity(remote_object)
    db_session.add(row)
    await db_session.flush()
    await subscribe_to_actor_activity(db_session, other_user.id, BOB_ACTOR)
    await unsubscribe_from_actor_activity(db_session, other_user.id, BOB_ACTOR)

    await notify_remote_activity_subscribers(db_session, activity=row, config=config)

    assert await _notifications(db_session, other_user.id) == []


# ---------------------------------------------------------------------------
# remote subscription + follow coupling (federated endpoints)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_subscribe_endpoint(
    fed_client, db_session, fed_config, regular_user, auth_headers, deliver_mock, monkeypatch
):
    """POST subscribes, follows the actor (pending), and delivers a Follow."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)

    response = fed_client.post(
        "/api/v1/remote/actors/bob@remote.example/activity-subscription",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["activity_subscribed"] is True
    assert body["follow_state"] == FOLLOW_STATE_PENDING

    row = await db_session.scalar(
        sa.select(Follow).where(Follow.user_id == regular_user.id, Follow.target_actor_url == BOB_ACTOR)
    )
    assert row is not None
    assert row.state == FOLLOW_STATE_PENDING
    assert await is_subscribed_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    deliver_mock.delay.assert_called_once()

    actor_response = fed_client.get(
        "/api/v1/remote/actors/bob@remote.example",
        headers=auth_headers(regular_user),
    )
    assert actor_response.status_code == 200
    assert actor_response.json()["activity_subscribed"] is True
    assert actor_response.json()["follow_state"] == FOLLOW_STATE_PENDING


@pytest.mark.asyncio
async def test_remote_subscribe_endpoint_idempotent(
    fed_client, db_session, fed_config, regular_user, auth_headers, deliver_mock, monkeypatch
):
    """Subscribing twice returns the same state and delivers one Follow."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)

    for _ in range(2):
        response = fed_client.post(
            "/api/v1/remote/actors/bob@remote.example/activity-subscription",
            headers=auth_headers(regular_user),
        )
        assert response.status_code == 200
        assert response.json()["activity_subscribed"] is True

    count = await db_session.execute(sa.select(sa.func.count(ActivitySubscription.id)))
    assert count.scalar() == 1
    # The follow is idempotent too — no second Follow activity is enqueued.
    deliver_mock.delay.assert_called_once()


@pytest.mark.asyncio
async def test_remote_unsubscribe_endpoint_keeps_follow(
    fed_client, db_session, fed_config, regular_user, auth_headers, deliver_mock, monkeypatch
):
    """DELETE removes the subscription but keeps the follow relationship."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    await _provision(db_session, fed_config, regular_user)

    fed_client.post(
        "/api/v1/remote/actors/bob@remote.example/activity-subscription",
        headers=auth_headers(regular_user),
    )
    response = fed_client.delete(
        "/api/v1/remote/actors/bob@remote.example/activity-subscription",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 204

    assert not await is_subscribed_to_actor_activity(db_session, regular_user.id, BOB_ACTOR)
    row = await db_session.scalar(
        sa.select(Follow).where(Follow.user_id == regular_user.id, Follow.target_actor_url == BOB_ACTOR)
    )
    assert row is not None

    actor_response = fed_client.get(
        "/api/v1/remote/actors/bob@remote.example",
        headers=auth_headers(regular_user),
    )
    assert actor_response.json()["activity_subscribed"] is False
    assert actor_response.json()["follow_state"] == FOLLOW_STATE_PENDING


@pytest.mark.asyncio
async def test_remote_subscribe_endpoint_unauthenticated(fed_client, monkeypatch):
    """Anonymous callers cannot subscribe to a remote actor."""
    _patch_remote_lookup(monkeypatch, _remote_actor())
    response = fed_client.post("/api/v1/remote/actors/bob@remote.example/activity-subscription")
    assert response.status_code == 401
    response = fed_client.delete("/api/v1/remote/actors/bob@remote.example/activity-subscription")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_local_subscribe_endpoint_also_follows(
    fed_client, db_session, fed_config, regular_user, other_user, auth_headers
):
    """The local bell also creates the follow and reports its state."""
    await _provision(db_session, fed_config, regular_user, other_user)

    response = fed_client.post(
        f"/api/v1/users/{other_user.username}/activity-subscription",
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["activity_subscribed"] is True
    assert body["follow_state"] == FOLLOW_STATE_ACCEPTED

    row = await db_session.scalar(
        sa.select(Follow).where(Follow.user_id == regular_user.id, Follow.target_user_id == other_user.id)
    )
    assert row is not None
    assert row.state == FOLLOW_STATE_ACCEPTED
