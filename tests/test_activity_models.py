"""
Activity model tests - verify instantiation, persistence, constraints, and
visibility helpers.
"""

from itertools import product

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from songhive.models._enums import Visibility
from songhive.models.activity import (
    ACTIVITY_ENTITY_TYPES,
    ACTIVITY_TARGET_STATES,
    ACTIVITY_TYPES,
    Activity,
    ActivityMention,
    ActivityTarget,
)


def _make_activity(**overrides) -> Activity:
    """Build a minimally valid Activity, allowing per-field overrides."""
    params = {
        "entity_type": "track",
        "entity_id": "track-1",
        "activity_type": "create",
        "source_type": "local",
        "source_actor": "https://example.com/users/alice",
        "source_id": "https://example.com/users/alice/objects/1",
        "visibility": Visibility.PUBLIC.value,
    }
    params.update(overrides)
    return Activity(**params)


def test_visibility_enum_values():
    """Visibility exposes the five expected string values."""
    assert [v.value for v in Visibility] == [
        "private",
        "mentioned",
        "local",
        "followers",
        "public",
    ]


def test_visibility_rank_is_strictly_increasing():
    """Rank orders visibility from most to least restrictive."""
    ranks = [Visibility.rank(v) for v in Visibility]
    assert ranks == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("child,parent", list(product(Visibility, Visibility)))
def test_visibility_can_contain_all_combinations(child, parent):
    """A parent can contain a child activity iff the child is not less restrictive."""
    assert Visibility.can_contain(child, parent) == (Visibility.rank(child) <= Visibility.rank(parent))


def test_activity_model():
    """Activity stores the federation source fields and content metadata."""
    activity = _make_activity(
        local_object_id="local-1",
        content="<p>hello</p>",
        content_source="hello",
        payload={"custom": True},
    )
    assert activity.entity_type == "track"
    assert activity.activity_type == "create"
    assert activity.local_object_id == "local-1"
    assert activity.payload == {"custom": True}


def test_activity_invalid_entity_type():
    """Activity rejects entity types outside ACTIVITY_ENTITY_TYPES."""
    with pytest.raises(ValueError, match="Invalid entity_type"):
        _make_activity(entity_type="station")


def test_activity_invalid_activity_type():
    """Activity rejects activity types outside ACTIVITY_TYPES."""
    with pytest.raises(ValueError, match="Invalid activity_type"):
        _make_activity(activity_type="boost-really")


def test_activity_entity_types_contract():
    """The allowed entity and activity types match the contract."""
    assert ACTIVITY_ENTITY_TYPES == ("track", "album", "artist", "playlist", "library")
    assert ACTIVITY_TYPES == (
        "create",
        "announce",
        "like",
        "reply",
        "quote",
        "mention",
        "update",
        "delete",
        "webmention",
    )
    assert ACTIVITY_TARGET_STATES == ("pending", "sent", "failed", "skipped")


@pytest.mark.asyncio
async def test_activity_persistence_defaults(db_session, regular_user):
    """A flushed activity receives ids, timestamps, and column defaults."""
    activity = _make_activity(owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    assert activity.id is not None
    assert activity.published_at is not None
    assert activity.created_at is not None
    assert activity.updated_at is not None
    assert activity.content_type == "text/plain"
    assert activity.retracted is False
    assert activity.deleted_at is None


@pytest.mark.asyncio
async def test_activity_entity_type_check_constraint(db_session):
    """The entity_type CHECK constraint rejects bad values below the ORM."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            sa.text(
                "INSERT INTO activities (id, entity_type, entity_id, activity_type, "
                "source_type, source_actor, source_id, visibility, published_at, "
                "created_at, updated_at) VALUES ('a1', 'station', 'x', 'create', "
                "'local', 'actor', 'src', 'public', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_activity_activity_type_check_constraint(db_session):
    """The activity_type CHECK constraint rejects bad values below the ORM."""
    with pytest.raises(IntegrityError):
        await db_session.execute(
            sa.text(
                "INSERT INTO activities (id, entity_type, entity_id, activity_type, "
                "source_type, source_actor, source_id, visibility, published_at, "
                "created_at, updated_at) VALUES ('a1', 'track', 'x', 'bogus', "
                "'local', 'actor', 'src', 'public', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_activity_source_type_source_id_unique(db_session):
    """(source_type, source_id) must be unique across activities."""
    db_session.add(_make_activity())
    await db_session.flush()

    db_session.add(_make_activity(entity_id="track-2"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_activity_local_object_id_unique(db_session):
    """local_object_id must be unique when set."""
    db_session.add(_make_activity(local_object_id="obj-1"))
    await db_session.flush()

    db_session.add(
        _make_activity(
            source_id="https://example.com/users/alice/objects/2",
            local_object_id="obj-1",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_activity_reply_self_reference(db_session):
    """An activity can reference another activity as its reply parent."""
    parent = _make_activity()
    db_session.add(parent)
    await db_session.flush()

    reply = _make_activity(
        activity_type="reply",
        source_id="https://example.com/users/alice/objects/2",
        in_reply_to_activity_id=parent.id,
    )
    db_session.add(reply)
    await db_session.flush()

    result = await db_session.execute(select(Activity).where(Activity.id == reply.id))
    loaded = result.scalar_one()
    assert loaded.in_reply_to_activity is not None
    assert loaded.in_reply_to_activity.id == parent.id


@pytest.mark.asyncio
async def test_activity_mentions_persist(db_session, regular_user):
    """Mentions are persisted through the activity relationship."""
    activity = _make_activity()
    activity.mentions.append(
        ActivityMention(
            handle="@bob@remote.example",
            actor_url="https://remote.example/users/bob",
        )
    )
    activity.mentions.append(ActivityMention(handle="alice", user_id=regular_user.id))
    db_session.add(activity)
    await db_session.flush()

    result = await db_session.execute(select(ActivityMention).where(ActivityMention.activity_id == activity.id))
    handles = {m.handle for m in result.scalars().all()}
    assert handles == {"@bob@remote.example", "alice"}


@pytest.mark.parametrize(
    "handle",
    ["alice", "@alice", "alice_9", "@alice@example.com", "alice@remote-instance.example.org"],
)
def test_activity_mention_valid_handles(handle):
    """Valid mention handles are accepted."""
    mention = ActivityMention(activity_id="a1", handle=handle)
    assert mention.handle == handle


@pytest.mark.parametrize(
    "handle",
    ["", "@", "alice bob", "alice@@x", "a@b@c", "@alice@", "alice!"],
)
def test_activity_mention_invalid_handles(handle):
    """Handles that do not match the mention regex are rejected."""
    with pytest.raises(ValueError, match="Invalid mention handle"):
        ActivityMention(activity_id="a1", handle=handle)


@pytest.mark.asyncio
async def test_activity_mention_activity_handle_unique(db_session):
    """(activity_id, handle) must be unique within activity_mentions."""
    activity = _make_activity()
    activity.mentions.append(ActivityMention(handle="@alice"))
    db_session.add(activity)
    await db_session.flush()

    db_session.add(ActivityMention(activity_id=activity.id, handle="@alice"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_activity_target_defaults(db_session):
    """Delivery targets default to pending with zero attempts."""
    activity = _make_activity()
    activity.targets.append(ActivityTarget(inbox_url="https://remote.example/inbox"))
    db_session.add(activity)
    await db_session.flush()

    target = activity.targets[0]
    assert target.state == "pending"
    assert target.attempts == 0
    assert target.last_error is None
    assert target.last_attempt_at is None


def test_activity_target_invalid_state():
    """Delivery targets reject states outside ACTIVITY_TARGET_STATES."""
    with pytest.raises(ValueError, match="Invalid activity target state"):
        ActivityTarget(activity_id="a1", inbox_url="https://x.example/inbox", state="delivered")


@pytest.mark.asyncio
async def test_activity_target_state_check_constraint(db_session):
    """The state CHECK constraint rejects bad values below the ORM."""
    activity = _make_activity()
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(IntegrityError):
        await db_session.execute(
            sa.text(
                "INSERT INTO activity_targets (id, activity_id, inbox_url, state, "
                "attempts, created_at, updated_at) VALUES ('t1', :activity_id, "
                "'https://x.example/inbox', 'delivered', 0, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            ),
            {"activity_id": activity.id},
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_activity_target_activity_inbox_unique(db_session):
    """(activity_id, inbox_url) must be unique within activity_targets."""
    activity = _make_activity()
    activity.targets.append(ActivityTarget(inbox_url="https://remote.example/inbox"))
    db_session.add(activity)
    await db_session.flush()

    db_session.add(ActivityTarget(activity_id=activity.id, inbox_url="https://remote.example/inbox"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_activity_delete_cascades_to_mentions_and_targets(db_session):
    """Deleting an activity removes its mentions and delivery targets."""
    activity = _make_activity()
    activity.mentions.append(ActivityMention(handle="@alice"))
    activity.targets.append(ActivityTarget(inbox_url="https://remote.example/inbox"))
    db_session.add(activity)
    await db_session.flush()
    activity_id = activity.id

    await db_session.delete(activity)
    await db_session.flush()

    remaining_mentions = (
        (await db_session.execute(select(ActivityMention).where(ActivityMention.activity_id == activity_id)))
        .scalars()
        .all()
    )
    remaining_targets = (
        (await db_session.execute(select(ActivityTarget).where(ActivityTarget.activity_id == activity_id)))
        .scalars()
        .all()
    )
    assert remaining_mentions == []
    assert remaining_targets == []
