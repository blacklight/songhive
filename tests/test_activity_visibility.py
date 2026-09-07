"""
Activity visibility rule tests - the containment matrix, enforcement, and
the federation cascade for visibility updates.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from songhive.federation.activities import (
    AS_PUBLIC,
    activity_audience,
    create_visibility_update_activity,
)
from songhive.models._enums import Visibility
from songhive.models.activity import Activity, ActivityMention, ActivityTarget
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.activities import VisibilityRules


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


@pytest.mark.parametrize(
    "child,parent,expected",
    [
        (Visibility.PRIVATE, Visibility.PRIVATE, True),
        (Visibility.PRIVATE, Visibility.MENTIONED, True),
        (Visibility.PRIVATE, Visibility.LOCAL, True),
        (Visibility.PRIVATE, Visibility.FOLLOWERS, True),
        (Visibility.PRIVATE, Visibility.PUBLIC, True),
        (Visibility.MENTIONED, Visibility.PRIVATE, False),
        (Visibility.MENTIONED, Visibility.MENTIONED, True),
        (Visibility.MENTIONED, Visibility.LOCAL, True),
        (Visibility.MENTIONED, Visibility.FOLLOWERS, True),
        (Visibility.MENTIONED, Visibility.PUBLIC, True),
        (Visibility.LOCAL, Visibility.PRIVATE, False),
        (Visibility.LOCAL, Visibility.MENTIONED, False),
        (Visibility.LOCAL, Visibility.LOCAL, True),
        (Visibility.LOCAL, Visibility.FOLLOWERS, True),
        (Visibility.LOCAL, Visibility.PUBLIC, True),
        (Visibility.FOLLOWERS, Visibility.PRIVATE, False),
        (Visibility.FOLLOWERS, Visibility.MENTIONED, False),
        (Visibility.FOLLOWERS, Visibility.LOCAL, False),
        (Visibility.FOLLOWERS, Visibility.FOLLOWERS, True),
        (Visibility.FOLLOWERS, Visibility.PUBLIC, True),
        (Visibility.PUBLIC, Visibility.PRIVATE, False),
        (Visibility.PUBLIC, Visibility.MENTIONED, False),
        (Visibility.PUBLIC, Visibility.LOCAL, False),
        (Visibility.PUBLIC, Visibility.FOLLOWERS, False),
        (Visibility.PUBLIC, Visibility.PUBLIC, True),
    ],
)
def test_visibility_can_contain(child, parent, expected):
    """All 25 combinations of activity and entity visibility."""
    assert VisibilityRules.can_contain(child, parent) == expected


def test_visibility_can_contain_accepts_strings():
    """can_contain accepts plain visibility strings as well as enum values."""
    assert VisibilityRules.can_contain("private", "public") is True
    assert VisibilityRules.can_contain("public", "followers") is False


def test_visibility_federates():
    """Only mentioned, followers, and public visibility leave the instance."""
    assert Visibility.federates(Visibility.PUBLIC) is True
    assert Visibility.federates(Visibility.FOLLOWERS) is True
    assert Visibility.federates(Visibility.MENTIONED) is True
    assert Visibility.federates(Visibility.LOCAL) is False
    assert Visibility.federates(Visibility.PRIVATE) is False


def test_enforce_activity_visibility_allowed():
    """enforce_activity_visibility passes when the activity fits the entity."""
    VisibilityRules.enforce_activity_visibility(Visibility.FOLLOWERS, Visibility.PUBLIC)
    VisibilityRules.enforce_activity_visibility("private", "mentioned")


def test_enforce_activity_visibility_denied():
    """enforce_activity_visibility raises 422 when the activity is too visible."""
    with pytest.raises(HTTPException) as excinfo:
        VisibilityRules.enforce_activity_visibility(Visibility.PUBLIC, Visibility.FOLLOWERS)
    assert excinfo.value.status_code == 422
    assert "public" in excinfo.value.detail
    assert "followers" in excinfo.value.detail


def test_enforce_activity_visibility_invalid_value():
    """enforce_activity_visibility raises 422 for unknown visibility values."""
    with pytest.raises(HTTPException) as excinfo:
        VisibilityRules.enforce_activity_visibility("bogus", Visibility.PUBLIC)
    assert excinfo.value.status_code == 422


@pytest.mark.parametrize(
    "visibility,expected_to,expected_cc",
    [
        (
            Visibility.PUBLIC,
            [AS_PUBLIC],
            ["https://local.example/users/alice/followers"],
        ),
        (
            Visibility.FOLLOWERS,
            ["https://local.example/users/alice/followers"],
            [],
        ),
        (Visibility.LOCAL, [], []),
        (Visibility.PRIVATE, [], []),
    ],
)
def test_activity_audience(visibility, expected_to, expected_cc):
    """activity_audience maps each visibility to its AP addressing."""
    assert activity_audience(visibility, "https://local.example/users/alice") == (
        expected_to,
        expected_cc,
    )


def test_activity_audience_mentioned():
    """mentioned visibility addresses the resolved mention actor URLs."""
    to, cc = activity_audience(
        Visibility.MENTIONED,
        "https://local.example/users/alice",
        mention_actor_urls=[
            "https://remote.example/users/bob",
            "https://other.example/users/carol",
            "https://remote.example/users/bob",
        ],
    )
    assert to == ["https://other.example/users/carol", "https://remote.example/users/bob"]
    assert cc == []


def test_create_visibility_update_activity():
    """The Update payload carries the new audience on the envelope and object."""
    payload = create_visibility_update_activity(
        "https://local.example/users/alice",
        "https://local.example/users/alice/objects/1",
        Visibility.FOLLOWERS,
    )

    assert payload["type"] == "Update"
    assert payload["actor"] == "https://local.example/users/alice"
    assert payload["to"] == ["https://local.example/users/alice/followers"]
    assert payload["cc"] == []
    assert payload["object"]["id"] == "https://local.example/users/alice/objects/1"
    assert payload["object"]["to"] == payload["to"]
    assert payload["object"]["cc"] == payload["cc"]


@pytest.mark.asyncio
async def test_cascade_visibility_update_noop(db_session, regular_user):
    """No change in visibility short-circuits without touching the entity."""
    track = await _make_track(db_session, regular_user, visibility=Visibility.PRIVATE.value)
    activity = _make_activity("track", track.id, visibility=Visibility.PRIVATE.value)
    db_session.add(activity)
    await db_session.flush()

    # The entity is private so a public update would fail — a no-op skips it.
    await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.PRIVATE)
    assert activity.visibility == Visibility.PRIVATE.value


@pytest.mark.asyncio
async def test_cascade_visibility_update_changes_visibility(db_session, regular_user):
    """A valid downgrade updates the stored visibility."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.FOLLOWERS)
    await db_session.flush()

    assert activity.visibility == Visibility.FOLLOWERS.value


@pytest.mark.asyncio
async def test_cascade_visibility_update_exceeds_entity(db_session, regular_user):
    """The new visibility cannot exceed the entity's visibility."""
    track = await _make_track(db_session, regular_user, visibility=Visibility.LOCAL.value)
    activity = _make_activity("track", track.id, visibility=Visibility.LOCAL.value)
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.PUBLIC)
    assert excinfo.value.status_code == 422
    assert activity.visibility == Visibility.LOCAL.value


@pytest.mark.asyncio
async def test_cascade_visibility_update_missing_entity(db_session):
    """A missing parent entity produces a 404."""
    activity = _make_activity("track", "missing-track")
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.FOLLOWERS)
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_cascade_visibility_update_invalid_value(db_session, regular_user):
    """Unknown visibility values are rejected with a 422."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id)
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await VisibilityRules.cascade_visibility_update(db_session, activity, "bogus")
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_cascade_visibility_update_sends_update_to_sent_inboxes(db_session, regular_user, monkeypatch):
    """Federatable visibility changes fan an Update out to sent inboxes."""
    regular_user.actor_url = "https://local.example/users/regular"
    regular_user.private_key_pem = "private-key"
    track = await _make_track(db_session, regular_user)

    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    activity.targets.append(ActivityTarget(inbox_url="https://b.example/inbox", state="sent"))
    activity.targets.append(ActivityTarget(inbox_url="https://c.example/inbox", state="failed"))
    activity.mentions.append(
        ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
    )
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.FOLLOWERS)

    assert activity.visibility == Visibility.FOLLOWERS.value
    assert deliver.delay.call_count == 2
    for call in deliver.delay.call_args_list:
        payload, inbox, key_id, key = call.args
        assert payload["type"] == "Update"
        assert payload["actor"] == regular_user.actor_url
        assert payload["object"]["id"] == activity.source_id
        assert payload["to"] == [f"{regular_user.actor_url}/followers"]
        assert inbox in {"https://a.example/inbox", "https://b.example/inbox"}
        assert key_id == f"{regular_user.actor_url}#main-key"
        assert key == "private-key"


@pytest.mark.asyncio
async def test_cascade_visibility_update_mentioned_audience(db_session, regular_user, monkeypatch):
    """mentioned visibility addresses only the resolved mention actor URLs."""
    regular_user.actor_url = "https://local.example/users/regular"
    regular_user.private_key_pem = "private-key"
    track = await _make_track(db_session, regular_user)

    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    activity.mentions.append(
        ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
    )
    activity.mentions.append(ActivityMention(handle="@unresolved"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.MENTIONED)

    payload, _, _, _ = deliver.delay.call_args.args
    assert payload["to"] == ["https://remote.example/users/bob"]
    assert payload["cc"] == []


@pytest.mark.asyncio
async def test_cascade_visibility_update_retracts_when_unfederated(db_session, regular_user, monkeypatch):
    """Downgrading to private/local sends a Tombstone so remotes drop the object."""
    regular_user.actor_url = "https://local.example/users/regular"
    regular_user.private_key_pem = "private-key"
    track = await _make_track(db_session, regular_user)

    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.PRIVATE)

    assert activity.visibility == Visibility.PRIVATE.value
    assert deliver.delay.call_count == 1
    payload, inbox, _, _ = deliver.delay.call_args.args
    assert payload["type"] == "Delete"
    assert payload["object"]["type"] == "Tombstone"
    assert payload["object"]["id"] == activity.source_id
    assert inbox == "https://a.example/inbox"


@pytest.mark.asyncio
async def test_cascade_visibility_update_remote_activity_no_fanout(db_session, monkeypatch):
    """Remote activities update locally without any federation delivery."""
    track = await _make_track(db_session, None)
    activity = _make_activity(
        "track",
        track.id,
        source_type="remote",
        source_actor="https://remote.example/users/bob",
        source_id="https://remote.example/users/bob/objects/9",
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.FOLLOWERS)

    assert activity.visibility == Visibility.FOLLOWERS.value
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_cascade_visibility_update_retracted_activity_no_fanout(db_session, regular_user, monkeypatch):
    """Soft-deleted local activities do not federate visibility updates."""
    from datetime import datetime, timezone

    regular_user.private_key_pem = "private-key"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    activity.deleted_at = datetime.now(timezone.utc)
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.FOLLOWERS)

    assert activity.visibility == Visibility.FOLLOWERS.value
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_cascade_visibility_update_without_key_still_updates(db_session, regular_user, monkeypatch):
    """The local update applies even when delivery cannot be signed."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=None)
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await VisibilityRules.cascade_visibility_update(db_session, activity, Visibility.FOLLOWERS)

    assert activity.visibility == Visibility.FOLLOWERS.value
    deliver.delay.assert_not_called()
