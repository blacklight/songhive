"""
Remote reply materialization tests — inbound ``Create`` replies become
``source_type="remote"`` ``Activity`` rows that can be listed, counted and
interacted with, while ``Update``/``Delete`` revise or retract them.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from pubby import Interaction, InteractionType
from sqlalchemy import func, select

from songhive.config.schema import SonghiveConfig
from songhive.federation.incoming import materialize_remote_reply, sync_remote_activity
from songhive.models._enums import Visibility
from songhive.models.activity import Activity, ActivityMention
from songhive.models.artist import Artist
from songhive.models.base import get_session, init_db, reset_db
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import activities as activity_service
from songhive.services.activities import (
    boost_activity,
    like_activity,
    list_activity_replies,
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
async def test_materialize_skips_non_public_reply(db_session, regular_user):
    """Followers-only replies stay interaction-only, like pubby's storage."""
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
async def test_update_remote_reply_retracts_non_public(db_session, regular_user):
    """An Update that drops public addressing retracts the row."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
    await db_session.flush()
    reply = await materialize_remote_reply(db_session, activity=_create_activity(parent))
    assert reply is not None

    update = _create_activity(parent, public=False)
    update["type"] = "Update"
    await sync_remote_activity(db_session, activity=update)
    await db_session.flush()

    assert reply.deleted_at is not None


@pytest.mark.asyncio
async def test_update_unknown_reply_materializes(db_session, regular_user):
    """An Update for a reply whose Create was missed materializes it."""
    track = await _make_track(db_session, regular_user)
    parent = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(parent)
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
                visibility="public",
            )
            session.add(parent)
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
