"""
Notification service tests: delivery targets, dedup, seen state, and purge.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from songhive.models.notification import Notification, NotificationPreference
from songhive.services import notifications


@pytest.fixture
def ws_send(monkeypatch):
    """Capture EventWebSocket.send_to_user calls."""
    mock = MagicMock()
    monkeypatch.setattr(
        "songhive.services.notifications.EventWebSocket.send_to_user",
        mock,
    )
    return mock


@pytest.fixture
def email_delay(monkeypatch):
    """Capture send_notification_email.delay calls."""
    task = MagicMock()
    monkeypatch.setattr("songhive.tasks.email.send_notification_email", task)
    return task


async def _set_pref(db_session, user_id, type, **targets):
    pref = NotificationPreference(
        user_id=user_id,
        type=type,
        in_app=targets.get("in_app", True),
        email=targets.get("email", False),
        email_digest=targets.get("email_digest", False),
    )
    db_session.add(pref)
    await db_session.flush()


async def _count_notifications(db_session, user_id) -> int:
    result = await db_session.execute(sa.select(sa.func.count(Notification.id)).where(Notification.user_id == user_id))
    return result.scalar() or 0


@pytest.mark.asyncio
async def test_create_default_targets_push_ws_only(db_session, regular_user, ws_send, email_delay):
    """With no preference row, only the in-app target is enabled."""
    notification = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="like",
        actor_url="https://remote.example/users/bob",
        source_url="https://remote.example/objects/1",
        payload={"actor_name": "bob"},
    )
    assert notification is not None
    assert notification.delivered_targets == ["in_app"]
    ws_send.assert_called_once()
    args = ws_send.call_args[0]
    assert args[0] == regular_user.id
    assert args[1] == "notification"
    assert args[2]["id"] == notification.id
    assert args[2]["type"] == "like"
    email_delay.delay.assert_not_called()


@pytest.mark.asyncio
async def test_create_all_targets_disabled_creates_nothing(db_session, regular_user, ws_send, email_delay):
    """No row is inserted when every delivery target is disabled."""
    await _set_pref(db_session, regular_user.id, "like", in_app=False)
    notification = await notifications.create_notification(db_session, user_id=regular_user.id, type="like")
    assert notification is None
    assert await _count_notifications(db_session, regular_user.id) == 0
    ws_send.assert_not_called()
    email_delay.delay.assert_not_called()


@pytest.mark.asyncio
async def test_create_email_target_verified_user_enqueues(db_session, regular_user, ws_send, email_delay):
    """A verified recipient with email enabled gets the email task queued."""
    await _set_pref(db_session, regular_user.id, "follow", in_app=False, email=True)
    notification = await notifications.create_notification(db_session, user_id=regular_user.id, type="follow")
    assert notification is not None
    assert notification.delivered_targets == ["email"]
    ws_send.assert_not_called()
    email_delay.delay.assert_called_once_with(str(notification.id))


@pytest.mark.asyncio
async def test_create_email_target_unverified_user_skips_queue(db_session, unverified_user, ws_send, email_delay):
    """An unverified recipient never gets the individual email queued."""
    await _set_pref(db_session, unverified_user.id, "follow", in_app=False, email=True)
    notification = await notifications.create_notification(db_session, user_id=unverified_user.id, type="follow")
    assert notification is not None
    assert notification.delivered_targets == ["email"]
    email_delay.delay.assert_not_called()


@pytest.mark.asyncio
async def test_create_digest_only_creates_row_without_push(db_session, regular_user, ws_send, email_delay):
    """Digest-only delivery still records the row for the nightly task."""
    await _set_pref(db_session, regular_user.id, "boost", in_app=False, email_digest=True)
    notification = await notifications.create_notification(db_session, user_id=regular_user.id, type="boost")
    assert notification is not None
    assert notification.delivered_targets == ["email_digest"]
    ws_send.assert_not_called()
    email_delay.delay.assert_not_called()


@pytest.mark.asyncio
async def test_create_dedup_unseen_notification(db_session, regular_user, ws_send):
    """An identical unseen notification is not duplicated."""
    first = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="like",
        actor_url="https://remote.example/users/bob",
        source_url="https://remote.example/objects/1",
    )
    second = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="like",
        actor_url="https://remote.example/users/bob",
        source_url="https://remote.example/objects/1",
    )
    assert first is second
    assert await _count_notifications(db_session, regular_user.id) == 1


@pytest.mark.asyncio
async def test_create_dedup_nullable_fields(db_session, regular_user):
    """Dedup treats NULL actor/source values as equal."""
    await notifications.create_notification(db_session, user_id=regular_user.id, type="share")
    await notifications.create_notification(db_session, user_id=regular_user.id, type="share")
    assert await _count_notifications(db_session, regular_user.id) == 1


@pytest.mark.asyncio
async def test_create_after_seen_creates_new_row(db_session, regular_user):
    """A seen notification does not block a new identical one."""
    first = await notifications.create_notification(db_session, user_id=regular_user.id, type="like")
    await notifications.mark_seen(db_session, regular_user.id, [first.id])
    second = await notifications.create_notification(db_session, user_id=regular_user.id, type="like")
    assert second is not None
    assert second.id != first.id
    assert await _count_notifications(db_session, regular_user.id) == 2


@pytest.mark.asyncio
async def test_list_and_unread_count(db_session, regular_user, other_user):
    """list_notifications paginates newest-first and honors the seen filter."""
    n1 = await notifications.create_notification(db_session, user_id=regular_user.id, type="like", source_url="/1")
    n2 = await notifications.create_notification(db_session, user_id=regular_user.id, type="follow", source_url="/2")
    await notifications.create_notification(db_session, user_id=other_user.id, type="like")

    items, total = await notifications.list_notifications(db_session, regular_user.id)
    assert total == 2
    assert {n.id for n in items} == {n1.id, n2.id}

    items, total = await notifications.list_notifications(db_session, regular_user.id, limit=1, offset=0)
    assert total == 2
    assert len(items) == 1

    assert await notifications.get_unread_count(db_session, regular_user.id) == 2

    await notifications.mark_seen(db_session, regular_user.id, [n1.id])
    unseen, total = await notifications.list_notifications(db_session, regular_user.id, seen=False)
    assert total == 1
    assert unseen[0].id == n2.id
    seen_items, total = await notifications.list_notifications(db_session, regular_user.id, seen=True)
    assert total == 1
    assert seen_items[0].id == n1.id


@pytest.mark.asyncio
async def test_list_type_filter(db_session, regular_user):
    """The types allowlist restricts listing and the total count."""
    like = await notifications.create_notification(db_session, user_id=regular_user.id, type="like")
    follow = await notifications.create_notification(db_session, user_id=regular_user.id, type="follow")
    await notifications.create_notification(db_session, user_id=regular_user.id, type="mention")

    items, total = await notifications.list_notifications(db_session, regular_user.id, types=["like", "follow"])
    assert total == 2
    assert {n.id for n in items} == {like.id, follow.id}

    items, total = await notifications.list_notifications(db_session, regular_user.id, types=["follow"])
    assert total == 1
    assert items[0].id == follow.id

    items, total = await notifications.list_notifications(db_session, regular_user.id, types=[])
    assert total == 0
    assert items == []


@pytest.mark.asyncio
async def test_mark_seen_unseen_scoped_to_user(db_session, regular_user, other_user):
    """Seen/unseen updates never touch another user's rows."""
    mine = await notifications.create_notification(db_session, user_id=regular_user.id, type="like")
    theirs = await notifications.create_notification(db_session, user_id=other_user.id, type="like")

    # Attempting to mark another user's id is a no-op.
    assert await notifications.mark_seen(db_session, regular_user.id, [theirs.id]) == 0

    assert await notifications.mark_seen(db_session, regular_user.id, [mine.id]) == 1
    # Marking an already-seen row reports 0 (no change).
    assert await notifications.mark_seen(db_session, regular_user.id, [mine.id]) == 0
    assert await notifications.mark_unseen(db_session, regular_user.id, [mine.id]) == 1
    assert await notifications.mark_unseen(db_session, regular_user.id, [mine.id]) == 0

    await db_session.refresh(theirs)
    assert theirs.seen_at is None


@pytest.mark.asyncio
async def test_mark_all_seen(db_session, regular_user, other_user):
    """mark_all_seen updates every unseen row of the user only."""
    await notifications.create_notification(db_session, user_id=regular_user.id, type="like")
    await notifications.create_notification(db_session, user_id=regular_user.id, type="follow")
    theirs = await notifications.create_notification(db_session, user_id=other_user.id, type="like")

    assert await notifications.mark_all_seen(db_session, regular_user.id) == 2
    assert await notifications.get_unread_count(db_session, regular_user.id) == 0
    assert await notifications.get_unread_count(db_session, other_user.id) == 1
    await db_session.refresh(theirs)
    assert theirs.seen_at is None


@pytest.mark.asyncio
async def test_get_and_set_preferences(db_session, regular_user):
    """Preferences merge stored rows with defaults for the remaining types."""
    prefs = await notifications.get_preferences(db_session, regular_user.id)
    assert len(prefs) == 7
    assert all(p["in_app"] is True and p["email"] is False and p["email_digest"] is False for p in prefs)

    await notifications.set_preference(db_session, regular_user.id, "like", in_app=False, email=True, email_digest=True)
    prefs = await notifications.get_preferences(db_session, regular_user.id)
    like = next(p for p in prefs if p["type"] == "like")
    assert like == {"type": "like", "in_app": False, "email": True, "email_digest": True}
    follow = next(p for p in prefs if p["type"] == "follow")
    assert follow["in_app"] is True

    # Updating the same type reuses the stored row.
    await notifications.set_preference(
        db_session, regular_user.id, "like", in_app=True, email=False, email_digest=False
    )
    prefs = await notifications.get_preferences(db_session, regular_user.id)
    like = next(p for p in prefs if p["type"] == "like")
    assert like["in_app"] is True
    result = await db_session.execute(
        sa.select(sa.func.count(NotificationPreference.id)).where(NotificationPreference.user_id == regular_user.id)
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_purge_seen_notifications(db_session, regular_user):
    """Only seen rows older than the retention window are deleted."""
    old_seen = await notifications.create_notification(
        db_session, user_id=regular_user.id, type="like", source_url="/old"
    )
    new_seen = await notifications.create_notification(
        db_session, user_id=regular_user.id, type="like", source_url="/new"
    )
    unseen = await notifications.create_notification(
        db_session, user_id=regular_user.id, type="follow", source_url="/unseen"
    )
    await notifications.mark_seen(db_session, regular_user.id, [old_seen.id, new_seen.id])

    now = datetime.now(timezone.utc)
    await db_session.execute(
        sa.update(Notification).where(Notification.id == old_seen.id).values(seen_at=now - timedelta(days=100))
    )
    await db_session.execute(
        sa.update(Notification).where(Notification.id == unseen.id).values(created_at=now - timedelta(days=100))
    )

    deleted = await notifications.purge_seen_notifications(db_session, older_than_days=90)
    assert deleted == 1

    remaining = await db_session.execute(sa.select(Notification.id))
    remaining_ids = {row[0] for row in remaining.all()}
    assert remaining_ids == {new_seen.id, unseen.id}


@pytest.mark.asyncio
async def test_delete_notifications_scoped_to_user(db_session, regular_user, other_user, ws_send):
    """delete_notifications removes the given ids of the user only."""
    mine = await notifications.create_notification(db_session, user_id=regular_user.id, type="like")
    mine2 = await notifications.create_notification(db_session, user_id=regular_user.id, type="follow")
    theirs = await notifications.create_notification(db_session, user_id=other_user.id, type="like")

    # Ids belonging to another user are ignored.
    assert await notifications.delete_notifications(db_session, regular_user.id, [theirs.id]) == 0
    assert await notifications.delete_notifications(db_session, regular_user.id, [mine.id, "missing"]) == 1

    remaining = await db_session.execute(sa.select(Notification.id))
    assert {row[0] for row in remaining.all()} == {mine2.id, theirs.id}
    ws_send.assert_called_with(regular_user.id, "notification_deleted", {"ids": [str(mine.id)]})


@pytest.mark.asyncio
async def test_clear_notifications_removes_only_own(db_session, regular_user, other_user):
    """clear_notifications deletes every row of the user and no one else's."""
    await notifications.create_notification(db_session, user_id=regular_user.id, type="like")
    await notifications.create_notification(db_session, user_id=regular_user.id, type="follow")
    theirs = await notifications.create_notification(db_session, user_id=other_user.id, type="like")

    assert await notifications.clear_notifications(db_session, regular_user.id) == 2
    assert await _count_notifications(db_session, regular_user.id) == 0
    await db_session.refresh(theirs)
    assert theirs.id


@pytest.mark.asyncio
async def test_retract_notifications_unfollow(db_session, regular_user, other_user, ws_send):
    """An unfollow retracts that actor's follow notifications only."""
    actor = "https://remote.example/users/bob"
    follow = await notifications.create_notification(
        db_session, user_id=regular_user.id, type="follow", actor_url=actor, source_url=actor
    )
    # A like notification from the same actor must survive the unfollow.
    like = await notifications.create_notification(
        db_session, user_id=regular_user.id, type="like", actor_url=actor, source_url="/x"
    )
    # Another user's follow notification from the same actor survives.
    theirs = await notifications.create_notification(
        db_session, user_id=other_user.id, type="follow", actor_url=actor, source_url=actor
    )
    # A follow from a different actor survives.
    other_actor = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="follow",
        actor_url="https://remote.example/users/carol",
    )

    removed = await notifications.retract_notifications(
        db_session, user_id=regular_user.id, type="follow", actor_urls=[actor]
    )
    assert removed == 1

    remaining = await db_session.execute(sa.select(Notification.id))
    assert {row[0] for row in remaining.all()} == {like.id, theirs.id, other_actor.id}
    ws_send.assert_called_with(regular_user.id, "notification_deleted", {"ids": [str(follow.id)]})


@pytest.mark.asyncio
async def test_retract_notifications_seen_rows(db_session, regular_user):
    """Retraction removes notifications regardless of their seen state."""
    seen = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="share",
        actor_url="/users/bob",
        source_url="/tracks/1",
    )
    unseen = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="share",
        actor_url="/users/bob",
        source_url="/tracks/2",
    )
    await notifications.mark_seen(db_session, regular_user.id, [seen.id])

    removed = await notifications.retract_notifications(
        db_session,
        user_id=regular_user.id,
        type="share",
        actor_urls=["/users/bob"],
        source_url="/tracks/1",
    )
    assert removed == 1

    remaining = await db_session.execute(sa.select(Notification.id))
    assert [row[0] for row in remaining.all()] == [unseen.id]


@pytest.mark.asyncio
async def test_retract_notifications_referencing(db_session, regular_user, other_user):
    """Referencing URLs match source_url and the payload's target/activity id."""
    object_url = "https://instance.example/objects/abc"
    # Like on the object: matched via source_url.
    await notifications.create_notification(db_session, user_id=regular_user.id, type="like", source_url=object_url)
    # Reply to the object: matched via payload.target_url.
    await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="reply",
        source_url="https://remote.example/notes/1",
        payload={"target_url": object_url},
    )
    # Notification produced by a now-undone activity: payload.activity_id.
    await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="mention",
        source_url="https://remote.example/notes/2",
        payload={"activity_id": "https://remote.example/activities/9"},
    )
    # Unrelated notification survives.
    keep = await notifications.create_notification(
        db_session, user_id=regular_user.id, type="boost", source_url="/other"
    )
    # Another user's notification referencing the same URL survives when
    # the retraction is scoped to a recipient.
    theirs = await notifications.create_notification(
        db_session, user_id=other_user.id, type="like", source_url=object_url
    )

    removed = await notifications.retract_notifications_referencing(
        db_session,
        [object_url, "https://remote.example/activities/9"],
        user_id=regular_user.id,
    )
    assert removed == 3

    remaining = await db_session.execute(sa.select(Notification.id))
    assert {row[0] for row in remaining.all()} == {keep.id, theirs.id}


@pytest.mark.asyncio
async def test_retract_notifications_referencing_actor_scope(db_session, regular_user):
    """The optional actor filter limits retraction to that actor's rows."""
    url = "https://remote.example/notes/1"
    bob = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="reply",
        actor_url="https://remote.example/users/bob",
        source_url=url,
    )
    carol = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="reply",
        actor_url="https://remote.example/users/carol",
        source_url=url,
    )

    removed = await notifications.retract_notifications_referencing(
        db_session, [url], actor_url="https://remote.example/users/bob"
    )
    assert removed == 1

    remaining = await db_session.execute(sa.select(Notification.id))
    assert [row[0] for row in remaining.all()] == [carol.id]
    result = await db_session.execute(sa.select(Notification.id).where(Notification.id == bob.id))
    assert result.first() is None


@pytest.mark.asyncio
async def test_update_notifications_referencing_patches_payload(db_session, regular_user, ws_send):
    """Matching rows get payload fields merged; ``None`` values remove keys."""
    note_url = "https://remote.example/notes/1"
    mention = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="mention",
        actor_url="https://remote.example/users/bob",
        source_url=note_url,
        payload={"object_content": "old", "object_summary": "spoiler"},
    )
    keep = await notifications.create_notification(
        db_session, user_id=regular_user.id, type="like", source_url="/other"
    )

    changed = await notifications.update_notifications_referencing(
        db_session,
        [note_url],
        fields_for=lambda _n: {"object_content": "new", "object_summary": None},
    )
    assert changed == 1

    await db_session.refresh(mention)
    assert mention.payload["object_content"] == "new"
    assert "object_summary" not in mention.payload
    await db_session.refresh(keep)
    assert not keep.payload

    ws_send.assert_called_with(
        regular_user.id,
        "notification_updated",
        {"notifications": [notifications.notification_to_dict(mention)]},
    )


@pytest.mark.asyncio
async def test_update_notifications_referencing_scoped(db_session, regular_user, other_user, ws_send):
    """The user_id/actor_url filters restrict which rows are patched."""
    url = "https://remote.example/notes/2"
    bob = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="reply",
        actor_url="https://remote.example/users/bob",
        source_url=url,
        payload={"object_content": "old"},
    )
    carol = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="reply",
        actor_url="https://remote.example/users/carol",
        source_url=url,
        payload={"object_content": "old"},
    )
    theirs = await notifications.create_notification(
        db_session,
        user_id=other_user.id,
        type="reply",
        actor_url="https://remote.example/users/bob",
        source_url=url,
        payload={"object_content": "old"},
    )

    changed = await notifications.update_notifications_referencing(
        db_session,
        [url],
        fields_for=lambda _n: {"object_content": "new"},
        user_id=regular_user.id,
        actor_url="https://remote.example/users/bob",
    )
    assert changed == 1

    await db_session.refresh(bob)
    assert bob.payload["object_content"] == "new"
    await db_session.refresh(carol)
    assert carol.payload["object_content"] == "old"
    await db_session.refresh(theirs)
    assert theirs.payload["object_content"] == "old"


@pytest.mark.asyncio
async def test_update_notifications_referencing_skips_unchanged(db_session, regular_user, ws_send):
    """Rows left untouched by ``fields_for`` push no event and don't count."""
    url = "https://remote.example/notes/3"
    await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="mention",
        source_url=url,
        payload={"object_content": "same"},
    )
    ws_send.reset_mock()

    skipped = await notifications.update_notifications_referencing(db_session, [url], fields_for=lambda _n: None)
    assert skipped == 0
    unchanged = await notifications.update_notifications_referencing(
        db_session, [url], fields_for=lambda _n: {"object_content": "same"}
    )
    assert unchanged == 0
    ws_send.assert_not_called()


@pytest.mark.asyncio
async def test_update_notifications_from_actor(db_session, regular_user, other_user, ws_send):
    """Actor profile edits refresh the actor_* snapshot on all their rows."""
    actor = "https://remote.example/users/bob"
    follow = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="follow",
        actor_url=actor,
        source_url=actor,
        payload={"actor_name": "bob", "actor_display_name": "Bob"},
    )
    like = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="like",
        actor_url=actor,
        source_url="/x",
        payload={"actor_name": "bob"},
    )
    other_actor = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="follow",
        actor_url="https://remote.example/users/carol",
        payload={"actor_name": "carol"},
    )
    theirs = await notifications.create_notification(
        db_session,
        user_id=other_user.id,
        type="follow",
        actor_url=actor,
        payload={"actor_name": "bob"},
    )

    changed = await notifications.update_notifications_from_actor(
        db_session,
        actor,
        fields={"actor_display_name": "Robert", "actor_avatar_url": "https://remote.example/a.png"},
        user_id=regular_user.id,
    )
    assert changed == 2

    await db_session.refresh(follow)
    assert follow.payload["actor_display_name"] == "Robert"
    assert follow.payload["actor_avatar_url"] == "https://remote.example/a.png"
    await db_session.refresh(like)
    assert like.payload["actor_display_name"] == "Robert"
    await db_session.refresh(other_actor)
    assert other_actor.payload.get("actor_display_name") is None
    await db_session.refresh(theirs)
    assert theirs.payload.get("actor_display_name") is None


@pytest.mark.asyncio
async def test_refresh_notifications_for_item(db_session, regular_user, ws_send):
    """A rename rewrites item_title/target_item_title (and legacy track_title)."""
    like = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="like",
        source_url="/tracks/t1",
        payload={"item_type": "track", "item_id": "t1", "track_title": "Old"},
    )
    share = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="share",
        source_url="/tracks/t1",
        payload={"item_type": "track", "item_id": "t1", "item_title": "Old"},
    )
    reply = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="reply",
        source_url="https://remote.example/notes/1",
        payload={"target_item_type": "track", "target_item_id": "t1", "target_item_title": "Old"},
    )
    unrelated = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="share",
        source_url="/albums/a1",
        payload={"item_type": "album", "item_id": "a1", "item_title": "Old"},
    )
    # Same id but a different item_type must not be patched.
    mistyped = await notifications.create_notification(
        db_session,
        user_id=regular_user.id,
        type="share",
        source_url="/albums/t1",
        payload={"item_type": "album", "item_id": "t1", "item_title": "Old"},
    )

    changed = await notifications.refresh_notifications_for_item(
        db_session, item_type="track", item_id="t1", title="New"
    )
    assert changed == 3

    await db_session.refresh(like)
    assert like.payload["track_title"] == "New"
    assert like.payload["item_title"] == "New"
    await db_session.refresh(share)
    assert share.payload["item_title"] == "New"
    assert "track_title" not in share.payload
    await db_session.refresh(reply)
    assert reply.payload["target_item_title"] == "New"
    await db_session.refresh(unrelated)
    assert unrelated.payload["item_title"] == "Old"
    await db_session.refresh(mistyped)
    assert mistyped.payload["item_title"] == "Old"
