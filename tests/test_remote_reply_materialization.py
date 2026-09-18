"""
Remote reply materialization tests — inbound ``Create`` replies become
``source_type="remote"`` ``Activity`` rows that can be listed, counted and
interacted with, standalone posts from followed actors are cached as
``remote_objects`` mirrors, and ``Update``/``Delete`` revise or retract
them.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pubby import Interaction, InteractionType
from sqlalchemy import func, select

from songhive.config.schema import SonghiveConfig
from songhive.federation.incoming import (
    materialize_remote_announce,
    materialize_remote_quote,
    materialize_remote_reply,
    sync_remote_activity,
)
from songhive.models._enums import Visibility
from songhive.models.activity import Activity, ActivityMention
from songhive.models.artist import Artist
from songhive.models.base import get_session, init_db, reset_db
from songhive.models.follow import Follow
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import activities as activity_service
from songhive.services import remote_content as remote_content_service
from songhive.services.activities import (
    boost_activity,
    like_activity,
    list_activity_interactors,
    list_activity_quotes,
    list_activity_replies,
    quote_activity,
    reply_to_activity,
    resolve_interaction_summaries,
)
from songhive.services.auth import create_user
from songhive.tasks.federation import process_incoming


async def _make_artist(session, name: str = "Test Artist") -> Artist:
    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _make_track(session, owner: User | None, visibility: str = Visibility.PUBLIC.value) -> Track:
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
    config.federation.enabled = True
    config.federation.instance_domain = "local.example"
    return config


def _create_activity(
    parent: Activity,
    *,
    object_id: str = "https://remote.example/notes/r1",
    actor: str = "https://remote.example/users/bob",
    content: str = "<p>remote reply</p>",
    public: bool = True,
    tags: list | None = None,
    published: str = "2026-09-13T17:08:00Z",
) -> dict:
    """Build an inbound ``Create(Note)`` replying to ``parent``."""
    note = {
        "type": "Note",
        "id": object_id,
        "attributedTo": actor,
        "content": content,
        "inReplyTo": parent.source_id,
        "published": published,
        "to": ["https://www.w3.org/ns/activitystreams#Public"] if public else [f"{actor}/followers"],
        "cc": [],
    }
    if tags:
        note["tag"] = tags
    return {
        "type": "Create",
        "id": f"{object_id}#create",
        "actor": actor,
        "object": note,
        "to": note["to"],
        "cc": note["cc"],
    }


def _follow_row(user: User, target_actor_url: str) -> Follow:
    """An accepted outbound follow — admits the actor's inbound activities."""
    return Follow(
        user_id=str(user.id),
        actor_url=user.actor_url or "https://local.example/users/regular",
        target_actor_url=target_actor_url,
        state="accepted",
    )


# ---------------------------------------------------------------------------
# materialize_remote_reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_remote_reply(db_session, regular_user):
    """A public remote reply to a local activity becomes a remote reply row."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    reply = await materialize_remote_reply(
        db_session,
        activity=_create_activity(parent),
    )

    assert reply is not None
    assert reply.source_type == "remote"
    assert reply.activity_type == "reply"
    assert reply.source_actor == "https://remote.example/users/bob"
    assert reply.source_id == "https://remote.example/notes/r1"
    assert reply.in_reply_to_activity_id == str(parent.id)
    assert reply.entity_type == "track"
    assert reply.entity_id == str(track.id)
    assert reply.owner_user_id is None
    assert reply.visibility == Visibility.PUBLIC.value
    assert reply.content == "<p>remote reply</p>"
    assert reply.published_at.isoformat().startswith("2026-09-13")


@pytest.mark.asyncio
async def test_materialize_resolves_mentions_and_tags(db_session, regular_user):
    """Mention tags resolve local users; hashtags link the reply to tags."""
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    reply = await materialize_remote_reply(
        db_session,
        activity=_create_activity(
            parent,
            tags=[
                {
                    "type": "Mention",
                    "href": "https://local.example/users/regular",
                    "name": "@regular@local.example",
                },
                {
                    "type": "Mention",
                    "href": "https://remote.example/users/carol",
                    "name": "@carol@remote.example",
                },
                {"type": "Hashtag", "name": "#music"},
            ],
        ),
    )

    assert reply is not None
    rows = (
        (await db_session.execute(select(ActivityMention).where(ActivityMention.activity_id == reply.id)))
        .scalars()
        .all()
    )
    mentions = {m.actor_url: m for m in rows}
    local = mentions["https://local.example/users/regular"]
    assert local.user_id == regular_user.id
    assert local.handle == "@regular@local.example"
    remote = mentions["https://remote.example/users/carol"]
    assert remote.user_id is None


@pytest.mark.asyncio
async def test_materialize_skips_non_public_reply_without_local_audience(db_session, regular_user):
    """A non-public reply addressing no local user is not materialized."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    reply = await materialize_remote_reply(
        db_session,
        activity=_create_activity(parent, public=False),
    )

    assert reply is None
    assert (await db_session.scalar(select(func.count(Activity.id)).where(Activity.source_type == "remote"))) == 0


@pytest.mark.asyncio
async def test_materialize_mentioned_reply_visible_to_addressee(db_session, regular_user, config, monkeypatch):
    """A direct remote reply materializes as ``mentioned`` for its audience."""
    config = _fed_config(config)
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    activity = _create_activity(parent, public=False)
    activity["object"]["to"] = [regular_user.actor_url]
    reply = await materialize_remote_reply(db_session, activity=activity)

    assert reply is not None
    assert reply.visibility == Visibility.MENTIONED.value
    rows = (
        (await db_session.execute(select(ActivityMention).where(ActivityMention.activity_id == reply.id)))
        .scalars()
        .all()
    )
    assert [(m.actor_url, m.user_id) for m in rows] == [(regular_user.actor_url, regular_user.id)]

    _patch_interactions(monkeypatch, [])
    local, _ = await list_activity_replies(db_session, activity=parent, user=regular_user, config=config)
    assert [a.id for a in local] == [reply.id]


@pytest.mark.asyncio
async def test_materialize_mentioned_reply_via_tag_only(db_session, regular_user):
    """A non-public reply mentioning a local user via tag materializes too."""
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    activity = _create_activity(parent, public=False)
    activity["object"]["tag"] = [
        {
            "type": "Mention",
            "href": regular_user.actor_url,
            "name": "@regular@local.example",
        }
    ]
    reply = await materialize_remote_reply(db_session, activity=activity)

    assert reply is not None
    assert reply.visibility == Visibility.MENTIONED.value


@pytest.mark.asyncio
async def test_mentioned_reply_hidden_from_others(
    db_session, regular_user, other_user, admin_user, config, monkeypatch
):
    """Non-mentioned users — including admins and anonymous viewers — cannot see it."""
    config = _fed_config(config)
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    activity = _create_activity(parent, public=False)
    activity["object"]["to"] = [regular_user.actor_url]
    reply = await materialize_remote_reply(db_session, activity=activity)
    assert reply is not None

    _patch_interactions(monkeypatch, [])
    for viewer in (other_user, admin_user, None):
        local, _ = await list_activity_replies(db_session, activity=parent, user=viewer, config=config)
        assert local == []
        assert await activity_service.can_view_activity(db_session, viewer, reply) is False


@pytest.mark.asyncio
async def test_materialize_skips_unknown_parent(db_session, regular_user):
    """Replies to objects the instance does not know are not materialized."""
    activity = _create_activity(
        _make_activity("track", "t1"),
    )
    activity["object"]["inReplyTo"] = "https://elsewhere.example/objects/unknown"

    reply = await materialize_remote_reply(db_session, activity=activity)

    assert reply is None


@pytest.mark.asyncio
async def test_materialize_skips_spoofed_attribution(db_session, regular_user):
    """Objects whose author or host does not match the signer are dropped."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    activity = _create_activity(parent)
    activity["object"]["attributedTo"] = "https://remote.example/users/mallory"
    assert await materialize_remote_reply(db_session, activity=activity) is None

    activity = _create_activity(parent, object_id="https://other.example/notes/r9")
    assert await materialize_remote_reply(db_session, activity=activity) is None


@pytest.mark.asyncio
async def test_materialize_is_idempotent(db_session, regular_user):
    """Re-delivering the same Create returns the existing row."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    first = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    second = await materialize_remote_reply(db_session, activity=_create_activity(parent))

    assert first is not None and second is not None
    assert second.id == first.id
    assert (await db_session.scalar(select(func.count(Activity.id)).where(Activity.source_type == "remote"))) == 1


@pytest.mark.asyncio
async def test_materialize_chains_to_remote_parent(db_session, regular_user):
    """A remote reply to a materialized remote reply links to its row."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    first = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert first is not None

    second = await materialize_remote_reply(
        db_session,
        activity=_create_activity(
            first,
            object_id="https://remote.example/notes/r2",
            actor="https://remote.example/users/carol",
        ),
    )

    assert second is not None
    assert second.in_reply_to_activity_id == str(first.id)


@pytest.mark.asyncio
async def test_materialize_clamps_to_entity_visibility(db_session, regular_user):
    """On a non-public entity the stored visibility is clamped."""
    track = await _make_track(db_session, regular_user, visibility=Visibility.FOLLOWERS.value)
    parent = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.FOLLOWERS.value,
    )
    db_session.add(parent)
    await db_session.flush()

    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))

    assert reply is not None
    assert reply.visibility == Visibility.FOLLOWERS.value


# ---------------------------------------------------------------------------
# Update / Delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_remote_reply_revises_content(db_session, regular_user):
    """An inbound Update refreshes content and payload."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    update = _create_activity(parent, content="<p>edited</p>")
    update["type"] = "Update"
    await sync_remote_activity(db_session, activity=update)
    await db_session.flush()

    assert reply.content == "<p>edited</p>"
    assert reply.payload["type"] == "Update"


@pytest.mark.asyncio
async def test_update_remote_reply_degrades_to_mentioned(db_session, regular_user):
    """An Update that drops public addressing degrades the row to ``mentioned``."""
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    update = _create_activity(parent, public=False)
    update["object"]["to"] = [regular_user.actor_url]
    update["type"] = "Update"
    await sync_remote_activity(db_session, activity=update)
    await db_session.flush()

    assert reply.deleted_at is None
    assert reply.visibility == Visibility.MENTIONED.value
    rows = (
        (await db_session.execute(select(ActivityMention).where(ActivityMention.activity_id == reply.id)))
        .scalars()
        .all()
    )
    assert [(m.actor_url, m.user_id) for m in rows] == [(regular_user.actor_url, regular_user.id)]


@pytest.mark.asyncio
async def test_update_unknown_reply_materializes(db_session, regular_user):
    """An Update for a reply whose Create was missed materializes it."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    # The update replays a missed Create — admitted because bob is followed.
    db_session.add(_follow_row(regular_user, "https://remote.example/users/bob"))
    await db_session.flush()

    update = _create_activity(parent)
    update["type"] = "Update"
    await sync_remote_activity(db_session, activity=update)

    row = await db_session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == "https://remote.example/notes/r1",
        )
    )
    assert row is not None


@pytest.mark.asyncio
async def test_delete_remote_reply_retracts(db_session, regular_user):
    """An inbound Delete soft-deletes the materialized row."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    await sync_remote_activity(
        db_session,
        activity={
            "type": "Delete",
            "actor": "https://remote.example/users/bob",
            "object": "https://remote.example/notes/r1",
        },
    )
    await db_session.flush()

    assert reply.deleted_at is not None


@pytest.mark.asyncio
async def test_delete_remote_reply_requires_author(db_session, regular_user):
    """A Delete from a different actor does not retract the row."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    await sync_remote_activity(
        db_session,
        activity={
            "type": "Delete",
            "actor": "https://remote.example/users/mallory",
            "object": "https://remote.example/notes/r1",
        },
    )
    await db_session.flush()

    assert reply.deleted_at is None


# ---------------------------------------------------------------------------
# Dedup against the Pubby interaction listing
# ---------------------------------------------------------------------------


def _reply_interaction(target: str, object_id: str, actor: str = "https://remote.example/users/bob") -> Interaction:
    return Interaction(
        source_actor_id=actor,
        target_resource=target,
        interaction_type=InteractionType.REPLY,
        activity_id=f"{object_id}#create",
        object_id=object_id,
        content="<p>remote</p>",
        published=datetime.now(timezone.utc),
    )


def _patch_interactions(monkeypatch, interactions):
    """Route remote-interaction lookups to a fixed list filtered by target."""
    monkeypatch.setattr(
        activity_service,
        "_fetch_remote_interactions",
        lambda cfg, ids, interaction_type=None: {
            sid: [
                i
                for i in interactions
                if i.target_resource == sid and (interaction_type is None or i.interaction_type == interaction_type)
            ]
            for sid in ids
        },
    )


@pytest.mark.asyncio
async def test_summaries_count_materialized_reply_once(db_session, regular_user, config, monkeypatch):
    """A reply backed by both a row and an interaction counts once."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    _patch_interactions(
        monkeypatch,
        [
            _reply_interaction(parent.source_id, reply.source_id),
            _reply_interaction(parent.source_id, "https://remote.example/notes/r2"),
        ],
    )

    summaries = await resolve_interaction_summaries(db_session, [parent], regular_user, config)

    assert summaries[str(parent.id)].reply_count == 2


@pytest.mark.asyncio
async def test_replies_listing_excludes_materialized(db_session, regular_user, config, monkeypatch):
    """Materialized replies come back as cards, not as remote entries."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    _patch_interactions(
        monkeypatch,
        [
            _reply_interaction(parent.source_id, reply.source_id),
            _reply_interaction(parent.source_id, "https://remote.example/notes/r2"),
        ],
    )

    local, remote = await list_activity_replies(db_session, activity=parent, user=regular_user, config=config)

    assert [a.id for a in local] == [reply.id]
    assert [i.object_id for i in remote] == ["https://remote.example/notes/r2"]


# ---------------------------------------------------------------------------
# Interactions with materialized remote replies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_like_remote_reply_fans_out_to_remote_author(db_session, regular_user, config, monkeypatch):
    """Liking a materialized remote reply delivers the Like to its author."""
    config = _fed_config(config)
    regular_user.private_key_pem = "private-key"
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    like = await like_activity(db_session, activity=reply, author=regular_user)

    assert like.in_reply_to_activity_id == str(reply.id)
    assert like.payload["object"] == reply.source_id

    monkeypatch.setattr(
        activity_service.federation_service,
        "resolve_actor_inbox",
        lambda *a, **k: "https://remote.example/inbox",
    )
    monkeypatch.setattr(
        activity_service.federation_service,
        "get_follower_inboxes",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        activity_service.federation_service,
        "get_object_follower_inboxes",
        lambda *a, **k: [],
    )
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    sent = await activity_service.fan_out_like_activity(
        db_session, like=like, target=reply, author=regular_user, config=config
    )

    assert sent == 1
    deliver.delay.assert_called_once()
    assert deliver.delay.call_args.args[1] == "https://remote.example/inbox"


@pytest.mark.asyncio
async def test_boost_remote_reply_fans_out_to_remote_author(db_session, regular_user, config, monkeypatch):
    """Boosting a materialized remote reply delivers the Announce to its author."""
    config = _fed_config(config)
    regular_user.private_key_pem = "private-key"
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    boost = await boost_activity(db_session, activity=reply, author=regular_user)

    assert boost.in_reply_to_activity_id == str(reply.id)
    assert boost.payload["object"] == reply.source_id

    monkeypatch.setattr(
        activity_service.federation_service,
        "resolve_actor_inbox",
        lambda *a, **k: "https://remote.example/inbox",
    )
    monkeypatch.setattr(
        activity_service.federation_service,
        "get_follower_inboxes",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        activity_service.federation_service,
        "get_object_follower_inboxes",
        lambda *a, **k: [],
    )
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    sent = await activity_service.fan_out_boost_activity(
        db_session, boost=boost, target=reply, author=regular_user, config=config
    )

    assert sent == 1
    deliver.delay.assert_called_once()
    assert deliver.delay.call_args.args[1] == "https://remote.example/inbox"


@pytest.mark.asyncio
async def test_reply_to_remote_reply_addresses_remote_author(db_session, regular_user, config, monkeypatch):
    """Replying to a materialized remote reply mentions its remote author."""
    config = _fed_config(config)
    regular_user.private_key_pem = "private-key"
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    remote_reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert remote_reply is not None

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)
    monkeypatch.setattr(
        activity_service.federation_service,
        "get_follower_inboxes",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        activity_service.federation_service,
        "get_object_follower_inboxes",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        activity_service.federation_service,
        "resolve_actor_inbox",
        lambda *a, **k: "https://remote.example/inbox",
    )

    reply = await reply_to_activity(
        db_session,
        activity=remote_reply,
        author=regular_user,
        config=config,
        status_text="a local reply to a remote reply",
    )

    assert reply.in_reply_to_activity_id == str(remote_reply.id)
    rows = (
        (await db_session.execute(select(ActivityMention).where(ActivityMention.activity_id == reply.id)))
        .scalars()
        .all()
    )
    assert "https://remote.example/users/bob" in {m.actor_url for m in rows}
    note = reply.payload["object"]
    assert note["inReplyTo"] == remote_reply.source_id
    assert any(
        t.get("type") == "Mention" and t.get("href") == "https://remote.example/users/bob" for t in note.get("tag", [])
    )
    deliver.delay.assert_called()


@pytest.mark.asyncio
async def test_retract_remote_reply_soft_deletes(db_session, regular_user):
    """Deleting a materialized remote reply soft-deletes the row."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    await activity_service.retract_activity(db_session, reply)
    await db_session.flush()

    assert reply.deleted_at is not None
    assert await db_session.get(Activity, reply.id) is not None


# ---------------------------------------------------------------------------
# process_incoming integration
# ---------------------------------------------------------------------------


def _task_config(tmp_path):
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={
            "enabled": True,
            "instance_domain": "local.example",
            "private_key_path": tmp_path / "actor.pem",
        },
    )


def _patch_db(engine):
    return patch(
        "songhive.tasks.federation.init_db",
        lambda *a, **k: init_db(engine=engine, force=True),
    )


def test_process_incoming_materializes_remote_reply(engine, tmp_path, monkeypatch):
    """The inbox task materializes a public remote reply on a local activity."""
    config = _task_config(tmp_path)
    init_db(engine=engine, force=True)

    async def _seed():
        async with get_session() as session:
            user = await create_user(
                session,
                username="alice",
                email="alice@example.com",
                password="secret",
                config=config,
            )
            artist = Artist(name="Test Artist")
            session.add(artist)
            await session.flush()
            track = Track(title="Test Track", artist_id=artist.id, owner_id=user.id, visibility="public")
            session.add(track)
            await session.flush()
            parent = Activity(
                entity_type="track",
                entity_id=str(track.id),
                activity_type="create",
                source_type="local",
                source_actor=user.actor_url,
                source_id=f"{user.actor_url}/objects/1",
                owner_user_id=str(user.id),
                visibility="public",
            )
            session.add(parent)
            # alice follows bob, so his inbound activities are admitted.
            session.add(_follow_row(user, "https://remote.example/users/bob"))
            await session.commit()
            return parent.source_id

    parent_source_id = asyncio.run(_seed())
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    activity = {
        "type": "Create",
        "id": "https://remote.example/notes/r1#create",
        "actor": actor,
        "object": {
            "type": "Note",
            "id": "https://remote.example/notes/r1",
            "attributedTo": actor,
            "content": "<p>hello from the fediverse</p>",
            "inReplyTo": parent_source_id,
            "to": ["https://www.w3.org/ns/activitystreams#Public"],
            "cc": [],
        },
    }

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage"),
        _patch_db(engine),
    ):
        mock_processor.return_value.process.return_value = None
        process_incoming(activity, username="alice")

    init_db(engine=engine, force=True)

    async def _check():
        async with get_session() as session:
            return await session.scalar(
                select(Activity).where(
                    Activity.source_type == "remote",
                    Activity.source_id == "https://remote.example/notes/r1",
                )
            )

    row = asyncio.run(_check())
    reset_db()

    assert row is not None
    assert row.activity_type == "reply"
    assert row.in_reply_to_activity_id is not None


@pytest.mark.asyncio
async def test_reply_count_respects_mentioned_visibility(db_session, regular_user, admin_user, config, monkeypatch):
    """A ``mentioned`` remote reply counts only for its audience."""
    config = _fed_config(config)
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    activity = _create_activity(parent, public=False)
    activity["object"]["to"] = [regular_user.actor_url]
    reply = await materialize_remote_reply(db_session, activity=activity)
    assert reply is not None

    _patch_interactions(monkeypatch, [])

    assert (await resolve_interaction_summaries(db_session, [parent], regular_user, config))[
        str(parent.id)
    ].reply_count == 1
    assert (await resolve_interaction_summaries(db_session, [parent], admin_user, config))[
        str(parent.id)
    ].reply_count == 0
    assert (await resolve_interaction_summaries(db_session, [parent], None, config))[str(parent.id)].reply_count == 0


# ---------------------------------------------------------------------------
# Shared-inbox delivery
# ---------------------------------------------------------------------------


def test_process_incoming_shared_inbox_notifies_and_materializes(engine, tmp_path, monkeypatch):
    """A private reply delivered to ``/ap/inbox`` notifies the addressee."""
    from songhive.models.notification import Notification

    config = _task_config(tmp_path)
    init_db(engine=engine, force=True)

    async def _seed():
        async with get_session() as session:
            user = await create_user(
                session,
                username="alice",
                email="alice@example.com",
                password="secret",
                config=config,
            )
            artist = Artist(name="Test Artist")
            session.add(artist)
            await session.flush()
            track = Track(title="Test Track", artist_id=artist.id, owner_id=user.id, visibility="public")
            session.add(track)
            await session.flush()
            parent = Activity(
                entity_type="track",
                entity_id=str(track.id),
                activity_type="create",
                source_type="local",
                source_actor=user.actor_url,
                source_id=f"{user.actor_url}/objects/1",
                owner_user_id=str(user.id),
                visibility="public",
            )
            session.add(parent)
            # alice follows bob, so his inbound activities are admitted.
            session.add(_follow_row(user, "https://remote.example/users/bob"))
            await session.commit()
            return user.id, user.actor_url, parent.source_id

    user_id, actor_url, parent_source_id = asyncio.run(_seed())
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    activity = {
        "type": "Create",
        "id": "https://remote.example/notes/priv1#create",
        "actor": actor,
        "to": [actor_url],
        "object": {
            "type": "Note",
            "id": "https://remote.example/notes/priv1",
            "attributedTo": actor,
            "content": "<p>private reply</p>",
            "inReplyTo": parent_source_id,
            "to": [actor_url],
            "tag": [{"type": "Mention", "href": actor_url, "name": "@alice@local.example"}],
        },
    }

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage"),
        _patch_db(engine),
    ):
        mock_processor.return_value.process.return_value = None
        process_incoming(activity, username=None)

    init_db(engine=engine, force=True)

    async def _check():
        async with get_session() as session:
            row = await session.scalar(
                select(Activity).where(
                    Activity.source_type == "remote",
                    Activity.source_id == "https://remote.example/notes/priv1",
                )
            )
            notifications = (
                (await session.execute(select(Notification).where(Notification.user_id == user_id))).scalars().all()
            )
            return row, notifications

    row, notifications = asyncio.run(_check())
    reset_db()

    assert row is not None
    assert row.visibility == Visibility.MENTIONED.value
    # The reply notification already tells the addressee the note concerns
    # them — a redundant ``mention`` for the same note is suppressed.
    assert [n.type for n in notifications] == ["reply"]

    # The materialized reply's own identity lets clients render its card
    # and link to its ``/activities/{id}`` page instead of a remote object
    # id that does not dereference to a browser page.
    for notification in notifications:
        payload = notification.payload
        assert payload["object_activity_id"] == str(row.id)
        assert payload["object_page_url"] == f"/activities/{row.id}"
        assert payload["object_type"] == "Note"
    reply = next(n for n in notifications if n.type == "reply")
    assert reply.payload["target_object_activity_id"] is not None


# ---------------------------------------------------------------------------
# Shared-inbox recipient resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_inbox_recipients_reply_owner(db_session, regular_user):
    """A public reply notifies the parent owner even when not addressed."""
    from songhive.federation.notifications import resolve_inbox_recipients

    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    activity = _create_activity(parent)
    recipients = await resolve_inbox_recipients(db_session, activity=activity)
    assert [user.id for user in recipients] == [regular_user.id]


@pytest.mark.asyncio
async def test_resolve_inbox_recipients_restricted_reply_no_audience(db_session, regular_user):
    """A non-public reply that addresses nobody local notifies nobody."""
    from songhive.federation.notifications import resolve_inbox_recipients

    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    activity = _create_activity(parent, public=False)
    recipients = await resolve_inbox_recipients(db_session, activity=activity)
    assert recipients == []


@pytest.mark.asyncio
async def test_resolve_inbox_recipients_restricted_reply_addressee(db_session, regular_user):
    """A non-public reply notifies its local addressees and mention targets."""
    from songhive.federation.notifications import resolve_inbox_recipients

    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    activity = _create_activity(parent, public=False)
    activity["object"]["to"] = [regular_user.actor_url]
    recipients = await resolve_inbox_recipients(db_session, activity=activity)
    assert [user.id for user in recipients] == [regular_user.id]

    activity = _create_activity(parent, public=False, object_id="https://remote.example/notes/r2")
    activity["object"]["tag"] = [{"type": "Mention", "href": regular_user.actor_url, "name": "@regular"}]
    recipients = await resolve_inbox_recipients(db_session, activity=activity)
    assert [user.id for user in recipients] == [regular_user.id]


@pytest.mark.asyncio
async def test_resolve_inbox_recipients_like_target_owner(db_session, regular_user):
    """A ``Like`` on a local object resolves its owner for notification."""
    from songhive.federation.notifications import resolve_inbox_recipients

    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    activity = {
        "type": "Like",
        "id": "https://remote.example/likes/1",
        "actor": "https://remote.example/users/bob",
        "object": parent.source_id,
    }
    recipients = await resolve_inbox_recipients(db_session, activity=activity)
    assert [user.id for user in recipients] == [regular_user.id]


# ---------------------------------------------------------------------------
# materialize_remote_quote
# ---------------------------------------------------------------------------


def _create_quote(
    quoted: Activity,
    *,
    object_id: str = "https://remote.example/notes/q1",
    actor: str = "https://remote.example/users/bob",
    content: str = "<p>remote quote</p>",
    public: bool = True,
    field: str = "quoteUrl",
    published: str = "2026-09-14T10:00:00Z",
) -> dict:
    """Build an inbound ``Create(Note)`` quoting ``quoted``."""
    note = {
        "type": "Note",
        "id": object_id,
        "attributedTo": actor,
        "content": content,
        field: quoted.source_id,
        "published": published,
        "to": ["https://www.w3.org/ns/activitystreams#Public"] if public else [f"{actor}/followers"],
        "cc": [],
    }
    return {
        "type": "Create",
        "id": f"{object_id}#create",
        "actor": actor,
        "object": note,
        "to": note["to"],
        "cc": note["cc"],
    }


@pytest.mark.asyncio
async def test_materialize_remote_quote(db_session, regular_user):
    """A public remote quote of a local activity becomes a remote quote row."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    quote = await materialize_remote_quote(db_session, activity=_create_quote(target))

    assert quote is not None
    assert quote.source_type == "remote"
    assert quote.activity_type == "quote"
    assert quote.source_actor == "https://remote.example/users/bob"
    assert quote.source_id == "https://remote.example/notes/q1"
    assert quote.in_reply_to_activity_id == str(target.id)
    assert quote.entity_type == "track"
    assert quote.entity_id == str(track.id)
    assert quote.owner_user_id is None
    assert quote.visibility == Visibility.PUBLIC.value
    assert quote.content == "<p>remote quote</p>"


@pytest.mark.asyncio
async def test_materialize_remote_quote_strips_re_fallback(db_session, regular_user):
    """The remote server's ``RE:`` quote fallback is not stored as content."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    activity = _create_quote(
        target,
        content=(
            f'<p class="quote-inline">RE: <a href="{target.source_id}">{target.source_id}</a></p>'
            "<p>my take on this</p>"
        ),
    )
    quote = await materialize_remote_quote(db_session, activity=activity)

    assert quote is not None
    assert quote.content == "<p>my take on this</p>"


@pytest.mark.asyncio
async def test_materialize_remote_quote_strips_bare_re_tail(db_session, regular_user):
    """A bare ``RE: <url>`` tail (Misskey-style) is stripped too."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    activity = _create_quote(target, content=f"<p>my take</p> RE: {target.source_id}")
    quote = await materialize_remote_quote(db_session, activity=activity)

    assert quote is not None
    assert quote.content == "<p>my take</p>"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["quote", "quoteUri", "quoteUrl", "_misskey_quote"])
async def test_materialize_remote_quote_fields(db_session, regular_user, field):
    """FEP-0449, Fedibird, Mastodon and Misskey quote fields all resolve the target."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    quote = await materialize_remote_quote(db_session, activity=_create_quote(target, field=field))

    assert quote is not None
    assert quote.activity_type == "quote"
    assert quote.in_reply_to_activity_id == str(target.id)


@pytest.mark.asyncio
async def test_materialize_remote_quote_unknown_target(db_session, regular_user):
    """Quotes of objects the instance does not know are not materialized."""
    activity = _create_quote(_make_activity("track", "t1"))
    activity["object"]["quoteUrl"] = "https://elsewhere.example/objects/unknown"

    assert await materialize_remote_quote(db_session, activity=activity) is None


@pytest.mark.asyncio
async def test_materialize_remote_quote_idempotent(db_session, regular_user):
    """Re-delivering the same quote returns the existing row."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    first = await materialize_remote_quote(db_session, activity=_create_quote(target))
    second = await materialize_remote_quote(db_session, activity=_create_quote(target))

    assert first is not None and second is not None
    assert second.id == first.id
    assert (await db_session.scalar(select(func.count(Activity.id)).where(Activity.source_type == "remote"))) == 1


@pytest.mark.asyncio
async def test_materialize_remote_quote_skips_non_public_without_local_audience(db_session, regular_user):
    """A non-public quote addressing no local user is not materialized."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    quote = await materialize_remote_quote(db_session, activity=_create_quote(target, public=False))

    assert quote is None


@pytest.mark.asyncio
async def test_quote_takes_precedence_over_reply(db_session, regular_user):
    """An object carrying both quote and reply fields is a quote."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    db_session.add(_follow_row(regular_user, "https://remote.example/users/bob"))
    await db_session.flush()

    activity = _create_quote(target)
    activity["object"]["inReplyTo"] = target.source_id
    await sync_remote_activity(db_session, activity=activity)

    row = await db_session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == "https://remote.example/notes/q1",
        )
    )
    assert row is not None
    assert row.activity_type == "quote"


@pytest.mark.asyncio
async def test_update_remote_quote_revises_content(db_session, regular_user):
    """An inbound Update refreshes a materialized quote."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()
    quote = await materialize_remote_quote(db_session, activity=_create_quote(target))
    assert quote is not None

    update = _create_quote(target, content="<p>edited quote</p>")
    update["type"] = "Update"
    await sync_remote_activity(db_session, activity=update)
    await db_session.flush()

    assert quote.content == "<p>edited quote</p>"
    assert quote.payload["type"] == "Update"


@pytest.mark.asyncio
async def test_delete_remote_quote_retracts(db_session, regular_user):
    """An inbound Delete soft-deletes the materialized quote."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()
    quote = await materialize_remote_quote(db_session, activity=_create_quote(target))
    assert quote is not None

    await sync_remote_activity(
        db_session,
        activity={
            "type": "Delete",
            "actor": "https://remote.example/users/bob",
            "object": "https://remote.example/notes/q1",
        },
    )
    await db_session.flush()

    assert quote.deleted_at is not None


@pytest.mark.asyncio
async def test_remote_quote_counts_and_lists_once(db_session, regular_user, config, monkeypatch):
    """A materialized remote quote counts once and lists as a card."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()
    quote = await materialize_remote_quote(db_session, activity=_create_quote(target))
    assert quote is not None

    quote_interaction = Interaction(
        source_actor_id="https://remote.example/users/bob",
        target_resource=target.source_id,
        interaction_type=InteractionType.QUOTE,
        object_id=quote.source_id,
    )
    remote_only = Interaction(
        source_actor_id="https://remote.example/users/carol",
        target_resource=target.source_id,
        interaction_type=InteractionType.QUOTE,
        object_id="https://remote.example/notes/q2",
    )
    _patch_interactions(monkeypatch, [quote_interaction, remote_only])

    summaries = await resolve_interaction_summaries(db_session, [target], regular_user, config)
    assert summaries[str(target.id)].quote_count == 2
    assert summaries[str(target.id)].reply_count == 0

    local, remote = await list_activity_quotes(db_session, activity=target, user=regular_user, config=config)
    assert [a.id for a in local] == [quote.id]
    assert [i.object_id for i in remote] == ["https://remote.example/notes/q2"]


# ---------------------------------------------------------------------------
# materialize_remote_announce
# ---------------------------------------------------------------------------


def _announce(
    object_uri: str,
    *,
    actor: str = "https://remote.example/users/bob",
    announce_id: str = "https://remote.example/activities/a1",
    public: bool = True,
    published: str = "2026-09-15T12:00:00Z",
) -> dict:
    """Build an inbound ``Announce`` boosting ``object_uri``."""
    return {
        "type": "Announce",
        "id": announce_id,
        "actor": actor,
        "object": object_uri,
        "published": published,
        "to": ["https://www.w3.org/ns/activitystreams#Public"] if public else [f"{actor}/followers"],
        "cc": [],
    }


@pytest.mark.asyncio
async def test_materialize_remote_announce_known_target(db_session, regular_user):
    """An announce of a known activity becomes a remote announce row."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    announce = await materialize_remote_announce(db_session, activity=_announce(target.source_id))

    assert announce is not None
    assert announce.source_type == "remote"
    assert announce.activity_type == "announce"
    assert announce.source_actor == "https://remote.example/users/bob"
    assert announce.source_id == "https://remote.example/activities/a1"
    assert announce.in_reply_to_activity_id == str(target.id)
    assert announce.entity_type == "track"
    assert announce.entity_id == str(track.id)
    assert announce.owner_user_id is None
    assert announce.visibility == Visibility.PUBLIC.value
    assert announce.published_at.isoformat().startswith("2026-09-15")


@pytest.mark.asyncio
async def test_materialize_remote_announce_via_sync_requires_follow(db_session, regular_user, config):
    """``sync_remote_activity`` stores announces only from followed actors."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    activity = _announce(target.source_id)
    await sync_remote_activity(db_session, activity=activity, config=config)
    await db_session.flush()
    assert await db_session.scalar(select(func.count(Activity.id)).where(Activity.activity_type == "announce")) == 0

    db_session.add(_follow_row(regular_user, "https://remote.example/users/bob"))
    await db_session.flush()
    await sync_remote_activity(db_session, activity=activity, config=config)
    await db_session.flush()

    row = await db_session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == "https://remote.example/activities/a1",
        )
    )
    assert row is not None
    assert row.activity_type == "announce"
    assert row.in_reply_to_activity_id == str(target.id)


@pytest.mark.asyncio
async def test_materialize_remote_announce_dereferences_unknown_target(db_session, regular_user, config, monkeypatch):
    """An unknown boosted object is fetched, cached and mirrored first."""
    config = _fed_config(config)
    boosted_url = "https://elsewhere.example/users/kali/statuses/1"
    calls = []

    async def _fake_dereference(session, cfg, url, *, refresh=False):
        calls.append(url)
        remote_object = remote_content_service.RemoteObject(
            canonical_url=url,
            domain="elsewhere.example",
            object_type="Note",
            actor_url="https://elsewhere.example/users/kali",
            visibility="public",
            content="<p>boosted post</p>",
        )
        session.add(remote_object)
        await session.flush()
        boosted = _make_activity(
            "remote",
            remote_object.id,
            owner_user_id=None,
            source_type="remote",
            source_actor="https://elsewhere.example/users/kali",
            source_id=url,
        )
        session.add(boosted)
        await session.flush()
        return remote_content_service.RemoteObjectResult(remote_object=remote_object, activity=boosted)

    actor_lookups = []

    async def _fake_lookup(session, cfg, actor_url):
        actor_lookups.append(actor_url)
        return None

    monkeypatch.setattr(remote_content_service, "dereference_remote_object", _fake_dereference)
    monkeypatch.setattr(remote_content_service, "lookup_remote_actor", _fake_lookup)

    announce = await materialize_remote_announce(
        db_session,
        activity=_announce(boosted_url),
        config=config,
    )

    assert calls == [boosted_url]
    assert actor_lookups == ["https://remote.example/users/bob"]
    assert announce is not None
    assert announce.entity_type == "remote"
    assert announce.in_reply_to_activity_id is not None
    # The row attaches to the mirror the dereference materialized.
    target = await db_session.get(Activity, announce.in_reply_to_activity_id)
    assert target is not None
    assert target.source_id == boosted_url


@pytest.mark.asyncio
async def test_materialize_remote_announce_fetch_failure_skips(db_session, regular_user, config, monkeypatch):
    """A boosted object that cannot be fetched leaves no row."""
    from songhive.federation.fetch import FetchError

    config = _fed_config(config)

    async def _boom(session, cfg, url, *, refresh=False):
        raise FetchError("unreachable", status_code=502, url=url)

    monkeypatch.setattr(remote_content_service, "dereference_remote_object", _boom)

    async def _fake_lookup(session, cfg, actor_url):
        return None

    monkeypatch.setattr(remote_content_service, "lookup_remote_actor", _fake_lookup)

    announce = await materialize_remote_announce(
        db_session,
        activity=_announce("https://elsewhere.example/statuses/gone"),
        config=config,
    )

    assert announce is None
    assert await db_session.scalar(select(func.count(Activity.id)).where(Activity.activity_type == "announce")) == 0


@pytest.mark.asyncio
async def test_materialize_remote_announce_unknown_target_without_config(db_session, regular_user):
    """Without a config the unknown boosted object cannot be fetched."""
    announce = await materialize_remote_announce(
        db_session,
        activity=_announce("https://elsewhere.example/statuses/unknown"),
    )

    assert announce is None


@pytest.mark.asyncio
async def test_materialize_remote_announce_idempotent(db_session, regular_user):
    """Re-delivering the same Announce returns the existing row."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    first = await materialize_remote_announce(db_session, activity=_announce(target.source_id))
    second = await materialize_remote_announce(db_session, activity=_announce(target.source_id))

    assert first is not None and second is not None
    assert second.id == first.id
    assert (await db_session.scalar(select(func.count(Activity.id)).where(Activity.activity_type == "announce"))) == 1


@pytest.mark.asyncio
async def test_materialize_remote_announce_skips_non_public_without_local_audience(db_session, regular_user):
    """A followers-only announce addressing no local user is not stored."""
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    announce = await materialize_remote_announce(
        db_session,
        activity=_announce(target.source_id, public=False),
    )

    assert announce is None


@pytest.mark.asyncio
async def test_materialize_remote_announce_non_public_to_local_user(db_session, regular_user):
    """A non-public announce addressing a local user stores ``mentioned``."""
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()

    activity = _announce(target.source_id, public=False)
    activity["to"] = [regular_user.actor_url]
    announce = await materialize_remote_announce(db_session, activity=activity)

    assert announce is not None
    assert announce.visibility == Visibility.MENTIONED.value


@pytest.mark.asyncio
async def test_undo_announce_retracts(db_session, regular_user, config):
    """An Undo(Announce) soft-deletes the materialized row."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()
    announce = await materialize_remote_announce(db_session, activity=_announce(target.source_id))
    assert announce is not None

    await sync_remote_activity(
        db_session,
        activity={
            "type": "Undo",
            "id": "https://remote.example/activities/u1",
            "actor": "https://remote.example/users/bob",
            "object": _announce(target.source_id),
        },
        config=config,
    )
    await db_session.flush()

    assert announce.deleted_at is not None


@pytest.mark.asyncio
async def test_undo_announce_bare_id_retracts(db_session, regular_user, config):
    """An Undo carrying only the announce id still retracts the row."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()
    announce = await materialize_remote_announce(db_session, activity=_announce(target.source_id))
    assert announce is not None

    await sync_remote_activity(
        db_session,
        activity={
            "type": "Undo",
            "id": "https://remote.example/activities/u1",
            "actor": "https://remote.example/users/bob",
            "object": "https://remote.example/activities/a1",
        },
        config=config,
    )
    await db_session.flush()

    assert announce.deleted_at is not None


@pytest.mark.asyncio
async def test_undo_announce_wrong_actor_keeps_row(db_session, regular_user, config):
    """An Undo only retracts announces by its own actor."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()
    announce = await materialize_remote_announce(db_session, activity=_announce(target.source_id))
    assert announce is not None

    await sync_remote_activity(
        db_session,
        activity={
            "type": "Undo",
            "id": "https://remote.example/activities/u1",
            "actor": "https://remote.example/users/mallory",
            "object": _announce(target.source_id),
        },
        config=config,
    )
    await db_session.flush()

    assert announce.deleted_at is None


@pytest.mark.asyncio
async def test_delete_announce_keeps_boosted_cache_row(db_session, regular_user, config):
    """Deleting an announce does not tombstone the boosted object's cache."""
    config = _fed_config(config)
    boosted_url = "https://elsewhere.example/users/kali/statuses/1"
    remote_object = remote_content_service.RemoteObject(
        canonical_url=boosted_url,
        domain="elsewhere.example",
        object_type="Note",
        actor_url="https://elsewhere.example/users/kali",
        visibility="public",
    )
    db_session.add(remote_object)
    await db_session.flush()
    boosted = _make_activity(
        "remote",
        remote_object.id,
        owner_user_id=None,
        source_type="remote",
        source_actor="https://elsewhere.example/users/kali",
        source_id=boosted_url,
    )
    db_session.add(boosted)
    await db_session.flush()
    announce = await materialize_remote_announce(db_session, activity=_announce(boosted_url))
    assert announce is not None

    await sync_remote_activity(
        db_session,
        activity={
            "type": "Delete",
            "actor": "https://remote.example/users/bob",
            "object": {"type": "Tombstone", "id": "https://remote.example/activities/a1"},
        },
        config=config,
    )
    await db_session.flush()

    assert announce.deleted_at is not None
    assert boosted.deleted_at is None
    assert remote_object.unavailable_at is None


@pytest.mark.asyncio
async def test_remote_announce_counts_and_lists_once(db_session, regular_user, config, monkeypatch):
    """A boost backed by a row and a BOOST interaction counts/lists once."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    target = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(target)
    await db_session.flush()
    announce = await materialize_remote_announce(db_session, activity=_announce(target.source_id))
    assert announce is not None

    # Pubby records the same inbound Announce as a BOOST interaction whose
    # ``activity_id`` is the announce's own id — the row and the record are
    # the same boost, so it must not count or list twice.
    same_boost = Interaction(
        source_actor_id="https://remote.example/users/bob",
        target_resource=target.source_id,
        interaction_type=InteractionType.BOOST,
        activity_id=announce.source_id,
        published=datetime.now(timezone.utc),
    )
    other_boost = Interaction(
        source_actor_id="https://remote.example/users/carol",
        target_resource=target.source_id,
        interaction_type=InteractionType.BOOST,
        activity_id="https://remote.example/activities/a9",
        published=datetime.now(timezone.utc),
    )
    _patch_interactions(monkeypatch, [same_boost, other_boost])

    summaries = await resolve_interaction_summaries(db_session, [target], regular_user, config)
    assert summaries[str(target.id)].boost_count == 2

    actors = await list_activity_interactors(db_session, activity=target, interaction_type="announce", config=config)
    assert [a.actor for a in actors] == [
        "https://remote.example/users/carol",
        "https://remote.example/users/bob",
    ]


# ---------------------------------------------------------------------------
# Accept(QuoteRequest) — outgoing quote authorization
# ---------------------------------------------------------------------------


def _accept_quote_request(
    *,
    actor: str = "https://remote.example/users/bob",
    quoted: str,
    instrument_id: str,
    instrument_actor: str = "https://local.example/users/regular",
    authorization: str = "https://remote.example/quote_authorizations/abc",
) -> dict:
    """Build an inbound ``Accept`` answering one of our QuoteRequests."""
    return {
        "type": "Accept",
        "id": "https://remote.example/accepts/1",
        "actor": actor,
        "object": {
            "type": "QuoteRequest",
            "actor": instrument_actor,
            "object": quoted,
            "instrument": {
                "type": "Note",
                "id": instrument_id,
                "attributedTo": instrument_actor,
            },
        },
        "result": authorization,
    }


@pytest.mark.asyncio
async def test_accept_quote_request_stamps_authorization(db_session, regular_user, config):
    """An Accept from the quoted author stamps quoteAuthorization on the note."""
    config = _fed_config(config)
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    target = _make_activity(
        "track",
        track.id,
        owner_user_id=None,
        source_type="remote",
        source_actor="https://remote.example/users/bob",
        source_id="https://remote.example/objects/p1",
    )
    db_session.add(target)
    await db_session.flush()
    quote = await quote_activity(db_session, activity=target, author=regular_user, config=config, status_text="qt")
    assert "quoteAuthorization" not in quote.payload["object"]

    await sync_remote_activity(
        db_session,
        activity=_accept_quote_request(
            quoted=target.source_id,
            instrument_id=quote.source_id,
        ),
    )
    await db_session.flush()

    assert quote.payload["object"]["quoteAuthorization"] == "https://remote.example/quote_authorizations/abc"


@pytest.mark.asyncio
async def test_accept_quote_request_wrong_actor_ignored(db_session, regular_user, config):
    """An Accept from anyone but the quoted post's author is dropped."""
    config = _fed_config(config)
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    target = _make_activity(
        "track",
        track.id,
        owner_user_id=None,
        source_type="remote",
        source_actor="https://remote.example/users/bob",
        source_id="https://remote.example/objects/p1",
    )
    db_session.add(target)
    await db_session.flush()
    quote = await quote_activity(db_session, activity=target, author=regular_user, config=config, status_text="qt")

    await sync_remote_activity(
        db_session,
        activity=_accept_quote_request(
            actor="https://remote.example/users/mallory",
            quoted=target.source_id,
            instrument_id=quote.source_id,
        ),
    )
    await db_session.flush()

    assert "quoteAuthorization" not in quote.payload["object"]


# ---------------------------------------------------------------------------
# thread-subscription relay (object-scoped follows)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_reply_relayed_to_object_followers(db_session, regular_user, config, monkeypatch):
    """A public remote reply is relayed to followers of its thread objects."""
    config = _fed_config(config)
    regular_user.actor_url = "https://local.example/users/regular"
    regular_user.private_key_pem = "private-key"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    object_inboxes = MagicMock(return_value=["https://sub.example/inbox"])
    monkeypatch.setattr(
        "songhive.federation.incoming.federation_service.get_object_follower_inboxes",
        object_inboxes,
    )
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    activity = _create_activity(parent)
    reply = await materialize_remote_reply(db_session, activity=activity, config=config)

    assert reply is not None
    # The followed thread root's id was queried.
    object_inboxes.assert_called_once_with({parent.source_id}, config.database.url)
    # The received activity is forwarded verbatim, signed by the local
    # ancestor's owner.
    deliver.delay.assert_called_once_with(
        activity,
        "https://sub.example/inbox",
        "https://local.example/users/regular#main-key",
        "private-key",
    )


@pytest.mark.asyncio
async def test_relay_walks_reply_chain_and_uses_instance_key_without_local_owner(
    db_session, regular_user, config, monkeypatch, tmp_path
):
    """Thread followers on any ancestor are reached; a remote-only chain
    falls back to the instance actor's key."""
    config = _fed_config(config)
    config.federation.private_key_path = tmp_path / "instance.pem"
    track = await _make_track(db_session, regular_user)
    root = _make_activity(
        "track",
        track.id,
        owner_user_id=None,
        source_type="remote",
        source_actor="https://remote.example/users/bob",
        source_id="https://remote.example/objects/root",
    )
    mid = _make_activity(
        "track",
        track.id,
        owner_user_id=None,
        source_type="remote",
        source_actor="https://remote.example/users/carol",
        source_id="https://remote.example/objects/mid",
    )
    db_session.add_all([root, mid])
    await db_session.flush()
    mid.in_reply_to_activity_id = str(root.id)
    await db_session.flush()

    monkeypatch.setattr(
        "songhive.federation.incoming.federation_service.get_object_follower_inboxes",
        MagicMock(return_value=["https://sub.example/inbox"]),
    )
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    reply = await materialize_remote_reply(
        db_session,
        activity=_create_activity(mid, object_id="https://remote.example/notes/r2"),
        config=config,
    )

    assert reply is not None
    deliver.delay.assert_called_once()
    args = deliver.delay.call_args.args
    assert args[1] == "https://sub.example/inbox"
    assert args[2] == "https://local.example/ap/actor#main-key"


@pytest.mark.asyncio
async def test_non_public_reply_not_relayed_to_object_followers(db_session, regular_user, config, monkeypatch):
    """A non-public reply is materialized for its addressee but never relayed."""
    config = _fed_config(config)
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()

    monkeypatch.setattr(
        "songhive.federation.incoming.federation_service.get_object_follower_inboxes",
        MagicMock(return_value=["https://sub.example/inbox"]),
    )
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    reply = await materialize_remote_reply(
        db_session,
        activity=_create_activity(
            parent,
            public=False,
            tags=[
                {
                    "type": "Mention",
                    "href": "https://local.example/users/regular",
                    "name": "@regular@local.example",
                }
            ],
        ),
        config=config,
    )

    assert reply is not None
    deliver.delay.assert_not_called()


# ---------------------------------------------------------------------------
# Standalone remote posts (no local thread target)
# ---------------------------------------------------------------------------


def _create_post(
    *,
    object_id: str = "https://remote.example/notes/p1",
    actor: str = "https://remote.example/users/bob",
    content: str = "<p>remote post</p>",
    public: bool = True,
    in_reply_to: str | None = None,
) -> dict:
    """Build an inbound ``Create(Note)`` with no local thread target."""
    note = {
        "type": "Note",
        "id": object_id,
        "attributedTo": actor,
        "content": content,
        "published": "2026-09-18T10:00:00Z",
        "to": ["https://www.w3.org/ns/activitystreams#Public"] if public else [f"{actor}/followers"],
        "cc": [],
    }
    if in_reply_to:
        note["inReplyTo"] = in_reply_to
    return {
        "type": "Create",
        "id": f"{object_id}#create",
        "actor": actor,
        "object": note,
        "to": note["to"],
        "cc": note["cc"],
    }


@pytest.mark.asyncio
async def test_standalone_remote_post_materializes(db_session, regular_user, config):
    """A followed actor's top-level post is cached and mirrored remotely."""
    config = _fed_config(config)
    db_session.add(_follow_row(regular_user, "https://remote.example/users/bob"))
    await db_session.flush()

    await sync_remote_activity(db_session, activity=_create_post(), config=config)
    await db_session.flush()

    row = await db_session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == "https://remote.example/notes/p1",
        )
    )
    assert row is not None
    assert row.entity_type == "remote"
    assert row.activity_type == "create"
    assert row.source_actor == "https://remote.example/users/bob"
    assert row.visibility == Visibility.PUBLIC.value
    assert row.content == "<p>remote post</p>"
    assert row.in_reply_to_activity_id is None

    remote_object = await db_session.scalar(
        select(remote_content_service.RemoteObject).where(
            remote_content_service.RemoteObject.canonical_url == "https://remote.example/notes/p1"
        )
    )
    assert remote_object is not None
    assert remote_object.actor_url == "https://remote.example/users/bob"
    assert remote_object.visibility == "public"
    assert row.entity_id == str(remote_object.id)

    listed, _ = await remote_content_service.list_cached_actor_activities(
        db_session, "https://remote.example/users/bob", user=regular_user
    )
    assert [a.id for a in listed] == [row.id]


@pytest.mark.asyncio
async def test_standalone_remote_post_requires_follow(db_session, config):
    """A top-level post from an actor nobody follows is not stored."""
    config = _fed_config(config)

    await sync_remote_activity(db_session, activity=_create_post(), config=config)
    await db_session.flush()

    assert await db_session.scalar(select(func.count(Activity.id)).where(Activity.source_type == "remote")) == 0
    assert await db_session.scalar(select(func.count(remote_content_service.RemoteObject.id))) == 0


@pytest.mark.asyncio
async def test_reply_to_uncached_parent_materializes_standalone(db_session, regular_user, config):
    """A reply whose parent is not cached still becomes a remote post."""
    config = _fed_config(config)
    db_session.add(_follow_row(regular_user, "https://remote.example/users/bob"))
    await db_session.flush()

    await sync_remote_activity(
        db_session,
        activity=_create_post(in_reply_to="https://elsewhere.example/notes/x1"),
        config=config,
    )
    await db_session.flush()

    row = await db_session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == "https://remote.example/notes/p1",
        )
    )
    assert row is not None
    assert row.entity_type == "remote"
    assert row.activity_type == "reply"
    assert row.in_reply_to_activity_id is None


@pytest.mark.asyncio
async def test_update_standalone_remote_post_revises(db_session, regular_user, config):
    """An inbound Update refreshes the mirrored post and its cache row."""
    config = _fed_config(config)
    db_session.add(_follow_row(regular_user, "https://remote.example/users/bob"))
    await db_session.flush()

    await sync_remote_activity(db_session, activity=_create_post(), config=config)
    await db_session.flush()

    update = _create_post(content="<p>edited post</p>")
    update["type"] = "Update"
    await sync_remote_activity(db_session, activity=update, config=config)
    await db_session.flush()

    row = await db_session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == "https://remote.example/notes/p1",
        )
    )
    assert row is not None
    assert row.content == "<p>edited post</p>"
    assert row.payload["type"] == "Update"

    remote_object = await db_session.scalar(
        select(remote_content_service.RemoteObject).where(
            remote_content_service.RemoteObject.canonical_url == "https://remote.example/notes/p1"
        )
    )
    assert remote_object is not None
    assert remote_object.content == "<p>edited post</p>"


@pytest.mark.asyncio
async def test_delete_standalone_remote_post_retracts(db_session, regular_user, config):
    """An inbound Delete retracts the post and tombstones the cache row."""
    config = _fed_config(config)
    db_session.add(_follow_row(regular_user, "https://remote.example/users/bob"))
    await db_session.flush()

    await sync_remote_activity(db_session, activity=_create_post(), config=config)
    await db_session.flush()

    await sync_remote_activity(
        db_session,
        activity={
            "type": "Delete",
            "actor": "https://remote.example/users/bob",
            "object": "https://remote.example/notes/p1",
        },
        config=config,
    )
    await db_session.flush()

    row = await db_session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == "https://remote.example/notes/p1",
        )
    )
    assert row is not None
    assert row.deleted_at is not None

    remote_object = await db_session.scalar(
        select(remote_content_service.RemoteObject).where(
            remote_content_service.RemoteObject.canonical_url == "https://remote.example/notes/p1"
        )
    )
    assert remote_object is not None
    assert remote_object.unavailable_at is not None


@pytest.mark.asyncio
async def test_non_public_standalone_post_is_stored_limited(db_session, regular_user, config):
    """A followers-only post from a followed actor is stored non-public."""
    config = _fed_config(config)
    db_session.add(_follow_row(regular_user, "https://remote.example/users/bob"))
    await db_session.flush()

    await sync_remote_activity(db_session, activity=_create_post(public=False), config=config)
    await db_session.flush()

    row = await db_session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == "https://remote.example/notes/p1",
        )
    )
    assert row is not None
    assert row.visibility == "local"

    remote_object = await db_session.scalar(
        select(remote_content_service.RemoteObject).where(
            remote_content_service.RemoteObject.canonical_url == "https://remote.example/notes/p1"
        )
    )
    assert remote_object is not None
    assert remote_object.visibility == "private"
