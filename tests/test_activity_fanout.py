"""
Activity fan-out tests - visibility-driven audience resolution and the
``ActivityTarget`` bookkeeping wrapped around ``deliver_activity`` enqueueing.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

from songhive.models._enums import Visibility
from songhive.models.activity import Activity, ActivityMention, ActivityTarget
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import activities as activity_service


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


def _fed_config(config):
    """Enable federation on the test config."""
    config.federation.enabled = True
    config.federation.instance_domain = "local.example"
    return config


def _federated_user(user: User) -> User:
    """Give a test user a provisioned actor URL and signing key."""
    user.actor_url = "https://local.example/users/regular"
    user.private_key_pem = "private-key"
    return user


async def _targets(session, activity_id) -> list[ActivityTarget]:
    """Return the persisted delivery targets for an activity."""
    result = await session.execute(select(ActivityTarget).where(ActivityTarget.activity_id == activity_id))
    return list(result.scalars().all())


def _patch_resolution(monkeypatch, followers=(), inboxes=None):
    """Stub follower collection and per-actor inbox resolution."""
    followers_mock = MagicMock(return_value=list(followers))
    monkeypatch.setattr("songhive.services.federation.get_follower_inboxes", followers_mock)
    inboxes = dict(inboxes or {})
    resolve_mock = MagicMock(side_effect=lambda actor_url, config, **_: inboxes.get(actor_url))
    monkeypatch.setattr("songhive.services.federation.resolve_actor_inbox", resolve_mock)
    return followers_mock, resolve_mock


# ---------------------------------------------------------------------------
# resolve_audience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_audience_public_includes_followers_and_mentions(
    db_session, config, regular_user, other_user, monkeypatch
):
    """Public activities reach follower inboxes and remote mentioned actors."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    activity.mentions.append(
        ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
    )
    activity.mentions.append(
        ActivityMention(handle="@other", actor_url="https://local.example/users/other", user_id=other_user.id)
    )
    activity.mentions.append(ActivityMention(handle="@unresolved"))
    db_session.add(activity)
    await db_session.flush()

    followers, resolve = _patch_resolution(
        monkeypatch,
        followers=["https://f1.example/inbox", "https://f2.example/inbox"],
        inboxes={"https://remote.example/users/bob": "https://remote.example/inbox"},
    )

    audience = await activity_service.resolve_audience(db_session, activity, config)

    assert audience == {
        "https://f1.example/inbox",
        "https://f2.example/inbox",
        "https://remote.example/inbox",
    }
    followers.assert_called_once_with("https://local.example/users/alice", config.database.url)
    # Only the remote mention is resolved: the local-user and unresolved
    # mentions have no remote inbox.
    resolve.assert_called_once()
    assert resolve.call_args.args[:2] == ("https://remote.example/users/bob", config)


@pytest.mark.asyncio
async def test_resolve_audience_followers_visibility(db_session, config, regular_user, monkeypatch):
    """Followers-visible activities reach followers and remote mentions."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.FOLLOWERS.value,
    )
    activity.mentions.append(
        ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
    )
    db_session.add(activity)
    await db_session.flush()

    followers, _ = _patch_resolution(
        monkeypatch,
        followers=["https://f1.example/inbox"],
        inboxes={"https://remote.example/users/bob": "https://remote.example/inbox"},
    )

    audience = await activity_service.resolve_audience(db_session, activity, config)

    assert audience == {"https://f1.example/inbox", "https://remote.example/inbox"}
    followers.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_audience_mentioned_skips_followers(db_session, config, regular_user, monkeypatch):
    """Mentioned-only activities resolve mention inboxes without followers."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.MENTIONED.value,
    )
    activity.mentions.append(
        ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
    )
    db_session.add(activity)
    await db_session.flush()

    followers, resolve = _patch_resolution(
        monkeypatch,
        followers=["https://f1.example/inbox"],
        inboxes={"https://remote.example/users/bob": "https://remote.example/inbox"},
    )

    audience = await activity_service.resolve_audience(db_session, activity, config)

    assert audience == {"https://remote.example/inbox"}
    followers.assert_not_called()
    resolve.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_audience_non_federated_visibility_empty(db_session, config, regular_user, monkeypatch):
    """Private and local activities never produce a remote audience."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    followers, resolve = _patch_resolution(monkeypatch, followers=["https://f1.example/inbox"])

    for visibility in (Visibility.PRIVATE.value, Visibility.LOCAL.value):
        activity = _make_activity(
            "track",
            track.id,
            visibility=visibility,
            source_id=f"https://local.example/users/alice/objects/{visibility}",
        )
        activity.mentions.append(
            ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
        )
        db_session.add(activity)
        await db_session.flush()

        assert await activity_service.resolve_audience(db_session, activity, config) == set()

    followers.assert_not_called()
    resolve.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_audience_federation_disabled(db_session, config, regular_user, monkeypatch):
    """A federation-disabled config yields an empty audience."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id)
    activity.mentions.append(
        ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
    )
    db_session.add(activity)
    await db_session.flush()

    followers, _ = _patch_resolution(monkeypatch, followers=["https://f1.example/inbox"])

    assert await activity_service.resolve_audience(db_session, activity, config) == set()
    followers.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_audience_skips_local_domain_actor_url(db_session, config, regular_user, monkeypatch):
    """Mentions carrying an actor URL on the local domain are not resolved."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.MENTIONED.value,
    )
    activity.mentions.append(
        ActivityMention(handle="@carol@local.example", actor_url="https://local.example/users/carol")
    )
    db_session.add(activity)
    await db_session.flush()

    _, resolve = _patch_resolution(monkeypatch)

    assert await activity_service.resolve_audience(db_session, activity, config) == set()
    resolve.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_audience_drops_unresolvable_inboxes(db_session, config, regular_user, monkeypatch):
    """Mentions whose inbox cannot be resolved are excluded."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.MENTIONED.value,
    )
    activity.mentions.append(
        ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
    )
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, inboxes={})

    assert await activity_service.resolve_audience(db_session, activity, config) == set()


# ---------------------------------------------------------------------------
# fan_out_activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_out_activity_delivers_and_records_targets(db_session, config, regular_user, monkeypatch):
    """Each resolved inbox gets a delivery enqueue and a ``sent`` target row."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    payload = {"type": "Create", "object": {"id": "https://local.example/objects/1"}}
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        payload=payload,
    )
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, followers=["https://a.example/inbox", "https://b.example/inbox"])
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await activity_service.fan_out_activity(db_session, activity, config) == 2

    assert deliver.delay.call_count == 2
    for call in deliver.delay.call_args_list:
        sent_payload, inbox, key_id, key = call.args
        assert sent_payload is payload
        assert inbox in {"https://a.example/inbox", "https://b.example/inbox"}
        assert key_id == "https://local.example/users/regular#main-key"
        assert key == "private-key"

    targets = await _targets(db_session, activity.id)
    assert {t.inbox_url for t in targets} == {"https://a.example/inbox", "https://b.example/inbox"}
    for target in targets:
        assert target.state == "sent"
        assert target.attempts == 1
        assert target.last_attempt_at is not None
        assert target.last_error is None


@pytest.mark.asyncio
async def test_fan_out_activity_skips_blocked_inboxes(db_session, config, regular_user, monkeypatch):
    """Inboxes on blocked or non-allowed instances are recorded as skipped."""
    config = _fed_config(config)
    config.federation.blocked_instances = ["blocked.example"]
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        payload={"type": "Create"},
    )
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(
        monkeypatch,
        followers=["https://blocked.example/inbox", "https://ok.example/inbox"],
    )
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await activity_service.fan_out_activity(db_session, activity, config) == 1

    deliver.delay.assert_called_once()
    assert deliver.delay.call_args.args[1] == "https://ok.example/inbox"

    targets = {t.inbox_url: t for t in await _targets(db_session, activity.id)}
    assert targets["https://blocked.example/inbox"].state == "skipped"
    assert targets["https://blocked.example/inbox"].attempts == 0
    assert targets["https://ok.example/inbox"].state == "sent"


@pytest.mark.asyncio
async def test_fan_out_activity_marks_failed_on_enqueue_error(db_session, config, regular_user, monkeypatch):
    """A broker failure records a ``failed`` target with the error."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        payload={"type": "Create"},
    )
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, followers=["https://a.example/inbox"])
    deliver = MagicMock()
    deliver.delay.side_effect = RuntimeError("broker down")
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await activity_service.fan_out_activity(db_session, activity, config) == 0

    target = (await _targets(db_session, activity.id))[0]
    assert target.state == "failed"
    assert target.attempts == 1
    assert "broker down" in target.last_error


@pytest.mark.asyncio
async def test_fan_out_activity_dedupes_recorded_inboxes(db_session, config, regular_user, monkeypatch):
    """Inboxes already booked in ``activity_targets`` are not re-delivered."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        payload={"type": "Create"},
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, followers=["https://a.example/inbox", "https://b.example/inbox"])
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await activity_service.fan_out_activity(db_session, activity, config) == 1

    deliver.delay.assert_called_once()
    assert deliver.delay.call_args.args[1] == "https://b.example/inbox"
    assert (
        await db_session.scalar(select(func.count(ActivityTarget.id)).where(ActivityTarget.activity_id == activity.id))
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"source_type": "remote"},
        {"payload": None},
        {"visibility": Visibility.PRIVATE.value},
        {"visibility": Visibility.LOCAL.value},
    ],
)
async def test_fan_out_activity_noop_activities(db_session, config, regular_user, monkeypatch, overrides):
    """Remote, payload-less, and non-federating activities record nothing."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    params = {
        "owner_user_id": regular_user.id,
        "source_actor": regular_user.actor_url,
        "payload": {"type": "Create"},
    }
    params.update(overrides)
    activity = _make_activity("track", track.id, **params)
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, followers=["https://a.example/inbox"])
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await activity_service.fan_out_activity(db_session, activity, config) == 0
    assert await _targets(db_session, activity.id) == []
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_fan_out_activity_retracted_activity_noop(db_session, config, regular_user, monkeypatch):
    """Soft-deleted activities are never fanned out."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        payload={"type": "Create"},
    )
    activity.deleted_at = datetime.now(timezone.utc)
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, followers=["https://a.example/inbox"])
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await activity_service.fan_out_activity(db_session, activity, config) == 0
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_fan_out_activity_federation_disabled(db_session, config, regular_user, monkeypatch):
    """Federation-disabled configs produce no deliveries or targets."""
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        payload={"type": "Create"},
    )
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, followers=["https://a.example/inbox"])
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await activity_service.fan_out_activity(db_session, activity, config) == 0
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_fan_out_activity_requires_signing_key(db_session, config, regular_user, monkeypatch):
    """An owner without a private key cannot fan out."""
    config = _fed_config(config)
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        payload={"type": "Create"},
    )
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, followers=["https://a.example/inbox"])
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await activity_service.fan_out_activity(db_session, activity, config) == 0
    deliver.delay.assert_not_called()


# ---------------------------------------------------------------------------
# fan_out_like_activity
# ---------------------------------------------------------------------------


def _remote_target() -> Activity:
    """A remote activity whose author lives on a remote instance."""
    return _make_activity(
        "track",
        "track-1",
        source_type="remote",
        source_actor="https://remote.example/users/bob",
        source_id="https://remote.example/users/bob/objects/9",
    )


def _make_like(**overrides) -> Activity:
    """Build a local ``like`` activity carrying a ``Like`` payload."""
    params = {
        "activity_type": "like",
        "source_id": "https://local.example/users/regular/objects/like-1",
        "payload": {"type": "Like", "object": "https://remote.example/users/bob/objects/9"},
    }
    params.update(overrides)
    return _make_activity("track", "track-1", **params)


@pytest.mark.asyncio
async def test_fan_out_like_activity_remote_target(db_session, config, regular_user, monkeypatch):
    """A like on a remote activity reaches the author's inbox and followers."""
    config = _fed_config(config)
    _federated_user(regular_user)
    like = _make_like(
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
    )
    db_session.add(like)
    await db_session.flush()

    _patch_resolution(
        monkeypatch,
        followers=["https://f1.example/inbox"],
        inboxes={"https://remote.example/users/bob": "https://remote.example/inbox"},
    )
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    sent = await activity_service.fan_out_like_activity(
        db_session, like=like, target=_remote_target(), author=regular_user, config=config
    )

    assert sent == 2
    assert {call.args[1] for call in deliver.delay.call_args_list} == {
        "https://remote.example/inbox",
        "https://f1.example/inbox",
    }
    for call in deliver.delay.call_args_list:
        assert call.args[0] is like.payload
        assert call.args[2] == "https://local.example/users/regular#main-key"

    targets = {t.inbox_url: t.state for t in await _targets(db_session, like.id)}
    assert targets == {
        "https://remote.example/inbox": "sent",
        "https://f1.example/inbox": "sent",
    }


@pytest.mark.asyncio
async def test_fan_out_like_activity_local_target(db_session, config, regular_user, monkeypatch):
    """Likes on local activities skip author resolution but keep the audience."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    like = _make_like(
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
    )
    db_session.add_all([target, like])
    await db_session.flush()

    _, resolve = _patch_resolution(monkeypatch, followers=["https://f1.example/inbox"])
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    sent = await activity_service.fan_out_like_activity(
        db_session, like=like, target=target, author=regular_user, config=config
    )

    assert sent == 1
    resolve.assert_not_called()
    deliver.delay.assert_called_once()
    assert deliver.delay.call_args.args[1] == "https://f1.example/inbox"


@pytest.mark.asyncio
async def test_fan_out_like_activity_unresolvable_author_inbox(db_session, config, regular_user, monkeypatch):
    """An unresolvable author inbox still leaves the like's own audience."""
    config = _fed_config(config)
    _federated_user(regular_user)
    like = _make_like(
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
    )
    db_session.add(like)
    await db_session.flush()

    _patch_resolution(monkeypatch, followers=["https://f1.example/inbox"], inboxes={})
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    sent = await activity_service.fan_out_like_activity(
        db_session, like=like, target=_remote_target(), author=regular_user, config=config
    )

    assert sent == 1
    deliver.delay.assert_called_once()
    assert deliver.delay.call_args.args[1] == "https://f1.example/inbox"


@pytest.mark.asyncio
async def test_fan_out_like_activity_non_federated_like(db_session, config, regular_user, monkeypatch):
    """Private and local likes never resolve or deliver anything."""
    config = _fed_config(config)
    _federated_user(regular_user)
    like = _make_like(
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        visibility=Visibility.LOCAL.value,
    )
    db_session.add(like)
    await db_session.flush()

    _, resolve = _patch_resolution(monkeypatch, followers=["https://f1.example/inbox"])
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    sent = await activity_service.fan_out_like_activity(
        db_session, like=like, target=_remote_target(), author=regular_user, config=config
    )

    assert sent == 0
    resolve.assert_not_called()
    deliver.delay.assert_not_called()
    assert await _targets(db_session, like.id) == []


# ---------------------------------------------------------------------------
# POST /api/v1/activities/{activity_id}/like end-to-end fan-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_like_endpoint_records_delivery_target(
    client, db_session, regular_user, other_user, auth_headers, monkeypatch
):
    """A federated like persists a ``sent`` ActivityTarget for the author's inbox."""
    client.app.state.config.federation.enabled = True
    client.app.state.config.federation.instance_domain = "local.example"

    track = await _make_track(db_session, other_user)
    target = _make_activity(
        "track",
        track.id,
        source_type="remote",
        source_actor="https://remote.example/users/bob",
        source_id="https://remote.example/users/bob/objects/9",
        owner_user_id=other_user.id,
    )
    db_session.add(target)
    await db_session.flush()

    _patch_resolution(monkeypatch, inboxes={"https://remote.example/users/bob": "https://remote.example/inbox"})
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    resp = client.post(f"/api/v1/activities/{target.id}/like", headers=auth_headers(regular_user))

    assert resp.status_code == 201
    like_id = resp.json()["activity_id"]

    deliver.delay.assert_called_once()
    assert deliver.delay.call_args.args[1] == "https://remote.example/inbox"
    assert deliver.delay.call_args.args[2] == "https://local.example/users/regular#main-key"

    targets = await _targets(db_session, like_id)
    assert len(targets) == 1
    assert targets[0].inbox_url == "https://remote.example/inbox"
    assert targets[0].state == "sent"
    assert targets[0].attempts == 1
