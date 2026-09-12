"""
Tests for the incoming ActivityPub federation task.
"""

import asyncio
from unittest.mock import patch

from songhive.config.schema import SonghiveConfig
from songhive.models.base import get_session, init_db, reset_db
from songhive.services.auth import create_user
from songhive.tasks.federation import process_incoming


def _make_config(tmp_path, *, enabled=True, **overrides):
    """Build a test configuration with an optional private key path."""
    return SonghiveConfig(
        auth={"secret_key": "a" * 64},
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={
            "enabled": enabled,
            "instance_domain": "music.example.com",
            "private_key_path": tmp_path / "actor.pem",
            **overrides,
        },
    )


def test_process_incoming_noops_when_federation_disabled(tmp_path, caplog):
    """The task does nothing when federation is disabled."""
    config = _make_config(tmp_path, enabled=False)
    activity = {"actor": "https://remote.example/users/bob", "type": "Follow"}

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.load_config", return_value=config),
        patch("songhive.tasks.federation.get_federation_storage") as mock_storage,
    ):
        result = process_incoming(activity)

    assert result is None
    assert not mock_processor.called
    assert not mock_storage.called


def test_process_incoming_skips_blocked_domain(tmp_path):
    """The task forwards allow/block lists to InboxProcessor, which drops the activity."""
    config = _make_config(
        tmp_path,
        allowed_instances=["allowed.example"],
    )
    activity = {"actor": "https://blocked.example/users/bob", "type": "Follow"}

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.load_config", return_value=config),
        patch("songhive.tasks.federation.get_federation_storage") as mock_storage,
    ):
        mock_processor.return_value.process.return_value = None
        result = process_incoming(activity)

    assert result is None
    assert mock_storage.called
    call_kwargs = mock_processor.call_args.kwargs
    assert call_kwargs["allowed_instances"] == ["allowed.example"]
    assert call_kwargs["blocked_instances"] == []


def test_process_incoming_instance_actor(tmp_path):
    """Instance-targeted activities use the instance actor and key, with signatures enabled."""
    config = _make_config(tmp_path)
    activity = {"actor": "https://remote.example/users/bob", "type": "Follow"}

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.load_config", return_value=config),
        patch("songhive.tasks.federation.get_federation_storage") as mock_storage,
    ):
        result = process_incoming(activity)

    expected_actor = "https://music.example.com/ap/actor"
    expected_key_id = f"{expected_actor}#main-key"

    assert result is not None
    mock_storage.assert_called_once_with(config.database.url)
    call_args = mock_processor.call_args
    assert call_args.kwargs["actor_id"] == expected_actor
    assert call_args.kwargs["key_id"] == expected_key_id
    assert call_args.kwargs["private_key"] is not None

    processor_instance = mock_processor.return_value
    process_call = processor_instance.process.call_args
    assert process_call.args[0] == activity
    assert process_call.kwargs["method"] == "POST"
    assert process_call.kwargs["path"] == "/ap/inbox"
    assert process_call.kwargs.get("skip_verification") is not True

    # The generated key should be persisted on disk.
    assert (tmp_path / "actor.pem").exists()
    assert (tmp_path / "actor.pem").stat().st_size > 0


def test_process_incoming_user_actor(engine, tmp_path, monkeypatch):
    """User-targeted activities use the target user's actor and key, with signatures enabled."""
    config = _make_config(tmp_path)
    init_db(engine=engine, force=True)

    async def _create():
        async with get_session() as session:
            return await create_user(
                session,
                username="alice",
                email="alice@example.com",
                password="secret",
                config=config,
            )

    user = asyncio.run(_create())
    assert user.actor_url == "https://music.example.com/users/alice"
    assert user.private_key_pem

    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage") as mock_storage,
    ):
        activity = {"actor": "https://remote.example/users/bob", "type": "Follow"}
        result = process_incoming(activity, username="alice")

    expected_actor = "https://music.example.com/users/alice"
    expected_key_id = f"{expected_actor}#main-key"

    assert result is not None
    mock_storage.assert_called_once_with(config.database.url)
    call_args = mock_processor.call_args
    assert call_args.kwargs["actor_id"] == expected_actor
    assert call_args.kwargs["key_id"] == expected_key_id
    assert call_args.kwargs["private_key"] is not None

    processor_instance = mock_processor.return_value
    process_call = processor_instance.process.call_args
    assert process_call.args[0] == activity
    assert process_call.kwargs["method"] == "POST"
    assert process_call.kwargs["path"] == "/ap/inbox"
    assert process_call.kwargs.get("skip_verification") is not True

    reset_db()


def _patch_db(engine):
    """Return a patch that reinstalls the test engine on init_db calls."""
    return patch(
        "songhive.tasks.federation.init_db",
        lambda *a, **k: init_db(engine=engine, force=True),
    )


def _seed_alice(engine, config):
    """Create the local recipient user on the test engine."""
    init_db(engine=engine, force=True)

    async def _create():
        async with get_session() as session:
            return await create_user(
                session,
                username="alice",
                email="alice@example.com",
                password="secret",
                config=config,
            )

    return asyncio.run(_create())


def _notifications_for(engine, user_id):
    """Fetch all Notification rows for a user from the test engine."""
    from sqlalchemy import select

    from songhive.models.notification import Notification

    init_db(engine=engine, force=True)

    async def _load():
        async with get_session() as session:
            result = await session.execute(select(Notification).where(Notification.user_id == user_id))
            return list(result.scalars().all())

    rows = asyncio.run(_load())
    reset_db()
    return rows


def test_process_incoming_follow_creates_notification(engine, tmp_path, monkeypatch):
    """A federated Follow addressed to a local user creates a follow notification."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)

    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage"),
        _patch_db(engine),
    ):
        mock_processor.return_value.process.return_value = {"ok": True}
        activity = {
            "type": "Follow",
            "id": "https://remote.example/activities/1",
            "actor": "https://remote.example/users/bob",
            "object": "https://music.example.com/users/alice",
        }
        process_incoming(activity, username="alice")

    rows = _notifications_for(engine, user.id)
    assert len(rows) == 1
    assert rows[0].type == "follow"
    assert rows[0].actor_url == "https://remote.example/users/bob"
    assert rows[0].source_url == "https://remote.example/users/bob"


def test_process_incoming_like_and_announce(engine, tmp_path, monkeypatch):
    """Federated Like and Announce create like/boost notifications."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)

    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    for activity_type, _expected in (("Like", "like"), ("Announce", "boost")):
        with (
            patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
            patch("songhive.tasks.federation.get_federation_storage"),
            _patch_db(engine),
        ):
            mock_processor.return_value.process.return_value = {"ok": True}
            activity = {
                "type": activity_type,
                "id": f"https://remote.example/activities/{activity_type}",
                "actor": "https://remote.example/users/bob",
                "object": "https://music.example.com/users/alice/objects/t1",
            }
            process_incoming(activity, username="alice")

    rows = _notifications_for(engine, user.id)
    assert {r.type for r in rows} == {"like", "boost"}
    for row in rows:
        assert row.source_url == "https://music.example.com/users/alice/objects/t1"


def test_process_incoming_no_username_creates_nothing(engine, tmp_path, monkeypatch):
    """Instance-actor activities (username=None) create no notifications."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)

    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage"),
        _patch_db(engine),
    ):
        mock_processor.return_value.process.return_value = {"ok": True}
        activity = {
            "type": "Follow",
            "actor": "https://remote.example/users/bob",
            "object": "https://music.example.com/ap/actor",
        }
        process_incoming(activity, username=None)

    assert _notifications_for(engine, user.id) == []


def test_process_incoming_reply_and_quote_prefers_quote(engine, tmp_path, monkeypatch):
    """A note that is both a reply and a quote yields exactly one quote notification."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)

    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage"),
        _patch_db(engine),
    ):
        mock_processor.return_value.process.return_value = {"ok": True}
        activity = {
            "type": "Create",
            "id": "https://remote.example/activities/c1",
            "actor": "https://remote.example/users/bob",
            "object": {
                "type": "Note",
                "id": "https://remote.example/notes/1",
                "inReplyTo": "https://music.example.com/users/alice/objects/t1",
                "quoteUrl": "https://music.example.com/users/alice/objects/t2",
            },
        }
        process_incoming(activity, username="alice")

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["quote"]
    assert rows[0].source_url == "https://remote.example/notes/1"


def test_process_incoming_mention_stamps_notified_at(engine, tmp_path, monkeypatch):
    """A federated mention creates a mention notification and stamps notified_at."""
    from sqlalchemy import select

    from songhive.models.activity import Activity, ActivityMention

    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)

    async def _seed_mention():
        async with get_session() as session:
            activity_row = Activity(
                entity_type="track",
                entity_id="track-1",
                activity_type="create",
                source_type="local",
                source_actor="https://music.example.com/users/alice",
                source_id="https://music.example.com/users/alice/objects/m1",
                visibility="public",
            )
            session.add(activity_row)
            await session.flush()
            session.add(
                ActivityMention(
                    activity_id=activity_row.id,
                    handle="@alice@music.example.com",
                    actor_url=user.actor_url,
                    user_id=user.id,
                )
            )
            await session.commit()
            return activity_row.id

    init_db(engine=engine, force=True)
    activity_id = asyncio.run(_seed_mention())

    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage"),
        _patch_db(engine),
    ):
        mock_processor.return_value.process.return_value = {"ok": True}
        activity = {
            "type": "Create",
            "id": "https://remote.example/activities/c2",
            "actor": "https://remote.example/users/bob",
            "object": {
                "type": "Note",
                "id": "https://remote.example/notes/2",
                "tag": [
                    {
                        "type": "Mention",
                        "href": "https://music.example.com/users/alice",
                        "name": "@alice@music.example.com",
                    }
                ],
            },
        }
        process_incoming(activity, username="alice")

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["mention"]

    init_db(engine=engine, force=True)

    async def _check():
        async with get_session() as session:
            result = await session.execute(select(ActivityMention).where(ActivityMention.activity_id == activity_id))
            return result.scalars().one().notified_at

    notified_at = asyncio.run(_check())
    assert notified_at is not None
    reset_db()


def _process(engine, activity):
    """Run process_incoming with a mocked processor against the test engine."""
    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage"),
        _patch_db(engine),
    ):
        mock_processor.return_value.process.return_value = {"ok": True}
        return process_incoming(activity, username="alice")


def test_process_incoming_undo_follow_retracts_notification(engine, tmp_path, monkeypatch):
    """An Undo(Follow) removes the earlier follow notification from that actor."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    _process(
        engine,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f1",
            "actor": actor,
            "object": "https://music.example.com/users/alice",
        },
    )
    assert [r.type for r in _notifications_for(engine, user.id)] == ["follow"]

    _process(
        engine,
        {
            "type": "Undo",
            "id": "https://remote.example/activities/u1",
            "actor": actor,
            "object": {
                "type": "Follow",
                "id": "https://remote.example/activities/f1",
                "actor": actor,
                "object": "https://music.example.com/users/alice",
            },
        },
    )
    assert _notifications_for(engine, user.id) == []


def test_process_incoming_undo_like_retracts_notification(engine, tmp_path, monkeypatch):
    """An Undo(Like) removes the like notification for the undone object."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    object_url = "https://music.example.com/users/alice/objects/t1"
    _process(
        engine,
        {"type": "Like", "id": "https://remote.example/activities/l1", "actor": actor, "object": object_url},
    )
    _process(
        engine,
        {
            "type": "Like",
            "id": "https://remote.example/activities/l2",
            "actor": actor,
            "object": "https://music.example.com/users/alice/objects/t2",
        },
    )
    assert len(_notifications_for(engine, user.id)) == 2

    _process(
        engine,
        {
            "type": "Undo",
            "actor": actor,
            "object": {
                "type": "Like",
                "id": "https://remote.example/activities/l1",
                "actor": actor,
                "object": object_url,
            },
        },
    )
    rows = _notifications_for(engine, user.id)
    assert [r.source_url for r in rows] == ["https://music.example.com/users/alice/objects/t2"]


def test_process_incoming_delete_note_retracts_notification(engine, tmp_path, monkeypatch):
    """A Delete of a note removes quote/reply/mention notifications for it."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    note_id = "https://remote.example/notes/1"
    _process(
        engine,
        {
            "type": "Create",
            "id": "https://remote.example/activities/c1",
            "actor": actor,
            "object": {
                "type": "Note",
                "id": note_id,
                "inReplyTo": "https://music.example.com/users/alice/objects/t1",
            },
        },
    )
    assert [r.type for r in _notifications_for(engine, user.id)] == ["reply"]

    _process(
        engine,
        {
            "type": "Delete",
            "id": "https://remote.example/activities/d1",
            "actor": actor,
            "object": {"type": "Tombstone", "id": note_id},
        },
    )
    assert _notifications_for(engine, user.id) == []


def test_process_incoming_delete_actor_retracts_all(engine, tmp_path, monkeypatch):
    """A Delete of the actor itself removes every notification they produced."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    _process(
        engine,
        {"type": "Follow", "actor": actor, "object": "https://music.example.com/users/alice"},
    )
    _process(
        engine,
        {"type": "Like", "actor": actor, "object": "https://music.example.com/users/alice/objects/t1"},
    )
    _process(
        engine,
        {
            "type": "Follow",
            "actor": "https://remote.example/users/carol",
            "object": "https://music.example.com/users/alice",
        },
    )
    assert len(_notifications_for(engine, user.id)) == 3

    _process(
        engine,
        {
            "type": "Delete",
            "actor": actor,
            "object": {"type": "Person", "id": actor},
        },
    )
    rows = _notifications_for(engine, user.id)
    assert [r.actor_url for r in rows] == ["https://remote.example/users/carol"]


def test_process_incoming_undo_from_other_actor_keeps_notification(engine, tmp_path, monkeypatch):
    """An Undo only retracts notifications produced by its own actor."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    bob = "https://remote.example/users/bob"
    _process(
        engine,
        {"type": "Follow", "actor": bob, "object": "https://music.example.com/users/alice"},
    )
    # Mallory cannot retract bob's follow notification.
    _process(
        engine,
        {
            "type": "Undo",
            "actor": "https://remote.example/users/mallory",
            "object": {
                "type": "Follow",
                "actor": bob,
                "object": "https://music.example.com/users/alice",
            },
        },
    )
    assert [r.type for r in _notifications_for(engine, user.id)] == ["follow"]


def _seed_track(engine, user, federation_object_id="t1"):
    """Create a published track carrying a federation object id."""
    from songhive.models.artist import Artist
    from songhive.models.track import Track

    init_db(engine=engine, force=True)

    async def _create():
        async with get_session() as session:
            artist = Artist(name="Artist")
            session.add(artist)
            await session.flush()
            track = Track(
                title="Liked Song",
                artist_id=artist.id,
                owner_id=str(user.id),
                visibility="public",
                federation_object_id=federation_object_id,
            )
            session.add(track)
            await session.commit()
            return str(track.id)

    track_id = asyncio.run(_create())
    reset_db()
    return track_id


def test_process_incoming_actor_doc_enriches_payload(engine, tmp_path, monkeypatch):
    """The cached actor document provides display name and avatar for cards."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    with (
        patch("songhive.tasks.federation.InboxProcessor") as mock_processor,
        patch("songhive.tasks.federation.get_federation_storage") as mock_storage,
        _patch_db(engine),
    ):
        mock_processor.return_value.process.return_value = {"ok": True}
        mock_storage.return_value.get_cached_actor.return_value = {
            "name": "Bob From Remote",
            "icon": {"type": "Image", "url": "https://remote.example/avatars/bob.png"},
        }
        _process_incoming_activity = {
            "type": "Follow",
            "id": "https://remote.example/activities/f1",
            "actor": "https://remote.example/users/bob",
            "object": "https://music.example.com/users/alice",
        }
        process_incoming(_process_incoming_activity, username="alice")

    rows = _notifications_for(engine, user.id)
    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["actor_display_name"] == "Bob From Remote"
    assert payload["actor_avatar_url"] == "https://remote.example/avatars/bob.png"


def test_process_incoming_like_resolves_local_track(engine, tmp_path, monkeypatch):
    """A Like on a local object id resolves to the track for card rendering."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    track_id = _seed_track(engine, user, federation_object_id="t1")
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    _process(
        engine,
        {
            "type": "Like",
            "id": "https://remote.example/activities/l1",
            "actor": "https://remote.example/users/bob",
            "object": "https://music.example.com/users/alice/objects/t1",
        },
    )

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["like"]
    payload = rows[0].payload
    # source_url keeps the AP object id so Undo(Like) retraction still matches.
    assert rows[0].source_url == "https://music.example.com/users/alice/objects/t1"
    assert payload["item_type"] == "track"
    assert payload["item_id"] == track_id
    assert payload["item_title"] == "Liked Song"
    assert payload["local_url"] == f"/tracks/{track_id}"


def test_process_incoming_announce_resolves_page_url(engine, tmp_path, monkeypatch):
    """An Announce of a local ``/tracks/{id}`` page URL resolves to the track."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    track_id = _seed_track(engine, user)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    _process(
        engine,
        {
            "type": "Announce",
            "id": "https://remote.example/activities/a1",
            "actor": "https://remote.example/users/bob",
            "object": f"https://music.example.com/tracks/{track_id}",
        },
    )

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["boost"]
    payload = rows[0].payload
    assert payload["item_type"] == "track"
    assert payload["item_id"] == track_id
    assert payload["local_url"] == f"/tracks/{track_id}"


def test_process_incoming_reply_snapshots_note(engine, tmp_path, monkeypatch):
    """A reply notification carries the note's content snapshot."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    track_id = _seed_track(engine, user, federation_object_id="t9")
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    _process(
        engine,
        {
            "type": "Create",
            "id": "https://remote.example/activities/c9",
            "actor": "https://remote.example/users/bob",
            "object": {
                "type": "Note",
                "id": "https://remote.example/notes/9",
                "content": "<p>Great track!</p>",
                "summary": "music talk",
                "published": "2026-01-02T03:04:05Z",
                "url": [
                    {"type": "Link", "mediaType": "text/html", "href": "https://remote.example/@bob/9"},
                ],
                "inReplyTo": "https://music.example.com/users/alice/objects/t9",
            },
        },
    )

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["reply"]
    payload = rows[0].payload
    assert rows[0].source_url == "https://remote.example/notes/9"
    assert payload["object_content"] == "<p>Great track!</p>"
    assert payload["object_summary"] == "music talk"
    assert payload["object_url"] == "https://remote.example/@bob/9"
    assert payload["published"] == "2026-01-02T03:04:05Z"
    assert payload["target_url"] == "https://music.example.com/users/alice/objects/t9"
    assert payload["target_item_type"] == "track"
    assert payload["target_item_id"] == track_id
    assert payload["target_item_title"] == "Liked Song"
    assert payload["target_local_url"] == f"/tracks/{track_id}"


def test_process_incoming_note_snapshot_truncates_content(engine, tmp_path, monkeypatch):
    """Oversized note content is bounded in the stored payload."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    _process(
        engine,
        {
            "type": "Create",
            "id": "https://remote.example/activities/c10",
            "actor": "https://remote.example/users/bob",
            "object": {
                "type": "Note",
                "id": "https://remote.example/notes/10",
                "content": "x" * 20_000,
                "tag": [
                    {
                        "type": "Mention",
                        "href": "https://music.example.com/users/alice",
                        "name": "@alice@music.example.com",
                    }
                ],
            },
        },
    )

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["mention"]
    assert len(rows[0].payload["object_content"]) == 10_000
    # The note's Mention tags are snapshotted so the frontend can link
    # ``@handle`` text to the real actor URL.
    assert rows[0].payload["object_mentions"] == [
        {
            "handle": "@alice@music.example.com",
            "actor_url": "https://music.example.com/users/alice",
        }
    ]


def test_process_incoming_like_on_remote_object_stays_unresolved(engine, tmp_path, monkeypatch):
    """A Like on a non-local object keeps only the source URL."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    _process(
        engine,
        {
            "type": "Like",
            "id": "https://remote.example/activities/l2",
            "actor": "https://remote.example/users/bob",
            "object": "https://other.example/objects/nope",
        },
    )

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["like"]
    payload = rows[0].payload
    assert "item_type" not in payload
    assert "local_url" not in payload


def test_process_incoming_update_note_refreshes_snapshot(engine, tmp_path, monkeypatch):
    """An Update rewrites the note snapshot stored on its notifications."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    note_id = "https://remote.example/notes/u1"
    _process(
        engine,
        {
            "type": "Create",
            "id": "https://remote.example/activities/cu1",
            "actor": actor,
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>hello alice</p>",
                "tag": [
                    {
                        "type": "Mention",
                        "href": "https://music.example.com/users/alice",
                        "name": "@alice@music.example.com",
                    }
                ],
            },
        },
    )
    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["mention"]
    assert rows[0].payload["object_content"] == "<p>hello alice</p>"

    _process(
        engine,
        {
            "type": "Update",
            "id": "https://remote.example/activities/uu1",
            "actor": actor,
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>edited hello</p>",
                "summary": "new cw",
                "tag": [
                    {
                        "type": "Mention",
                        "href": "https://music.example.com/users/alice",
                        "name": "@alice@music.example.com",
                    }
                ],
            },
        },
    )

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["mention"]
    payload = rows[0].payload
    assert payload["object_content"] == "<p>edited hello</p>"
    assert payload["object_summary"] == "new cw"


def test_process_incoming_update_note_removes_stale_fields(engine, tmp_path, monkeypatch):
    """Snapshot fields absent from the updated object are removed."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    note_id = "https://remote.example/notes/u2"
    base = {
        "type": "Note",
        "id": note_id,
        "content": "<p>hi</p>",
        "summary": "cw",
        "tag": [
            {
                "type": "Mention",
                "href": "https://music.example.com/users/alice",
                "name": "@alice@music.example.com",
            }
        ],
    }
    _process(
        engine,
        {
            "type": "Create",
            "id": "https://remote.example/activities/cu2",
            "actor": actor,
            "object": base,
        },
    )
    rows = _notifications_for(engine, user.id)
    assert rows[0].payload["object_summary"] == "cw"

    edited = dict(base)
    edited.pop("summary")
    edited.pop("tag")
    # Removing the Mention tag would retract the row; keep the mention but
    # drop the ``name`` so the snapshot shrinks instead.
    edited["tag"] = [{"type": "Mention", "href": "https://music.example.com/users/alice"}]
    _process(
        engine,
        {
            "type": "Update",
            "id": "https://remote.example/activities/uu2",
            "actor": actor,
            "object": edited,
        },
    )

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["mention"]
    payload = rows[0].payload
    assert "object_summary" not in payload
    assert payload["object_mentions"] == [
        {"handle": "https://music.example.com/users/alice", "actor_url": "https://music.example.com/users/alice"}
    ]


def test_process_incoming_update_removed_mention_retracts(engine, tmp_path, monkeypatch):
    """A mention notification is retracted when the edit drops the tag."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    note_id = "https://remote.example/notes/u3"
    _process(
        engine,
        {
            "type": "Create",
            "id": "https://remote.example/activities/cu3",
            "actor": actor,
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>hi @alice</p>",
                "tag": [
                    {
                        "type": "Mention",
                        "href": "https://music.example.com/users/alice",
                    }
                ],
            },
        },
    )
    assert [r.type for r in _notifications_for(engine, user.id)] == ["mention"]

    _process(
        engine,
        {
            "type": "Update",
            "id": "https://remote.example/activities/uu3",
            "actor": actor,
            "object": {"type": "Note", "id": note_id, "content": "<p>hi nobody</p>"},
        },
    )
    assert _notifications_for(engine, user.id) == []


def test_process_incoming_update_retargeted_reply_retracts(engine, tmp_path, monkeypatch):
    """A reply notification is retracted when the note is retargeted away."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    note_id = "https://remote.example/notes/u4"
    _process(
        engine,
        {
            "type": "Create",
            "id": "https://remote.example/activities/cu4",
            "actor": actor,
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>nice</p>",
                "inReplyTo": "https://music.example.com/users/alice/objects/t1",
            },
        },
    )
    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["reply"]

    # The note now replies to a different object: it no longer concerns the
    # recipient.
    _process(
        engine,
        {
            "type": "Update",
            "id": "https://remote.example/activities/uu4",
            "actor": actor,
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>nice</p>",
                "inReplyTo": "https://remote.example/objects/other",
            },
        },
    )
    assert _notifications_for(engine, user.id) == []


def test_process_incoming_update_reply_same_target_refreshes(engine, tmp_path, monkeypatch):
    """A reply notification keeps its target and refreshes the snapshot."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    note_id = "https://remote.example/notes/u5"
    target = "https://music.example.com/users/alice/objects/t1"
    _process(
        engine,
        {
            "type": "Create",
            "id": "https://remote.example/activities/cu5",
            "actor": actor,
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>nice</p>",
                "inReplyTo": target,
            },
        },
    )

    _process(
        engine,
        {
            "type": "Update",
            "id": "https://remote.example/activities/uu5",
            "actor": actor,
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>even nicer</p>",
                "inReplyTo": target,
            },
        },
    )

    rows = _notifications_for(engine, user.id)
    assert [r.type for r in rows] == ["reply"]
    payload = rows[0].payload
    assert payload["object_content"] == "<p>even nicer</p>"
    assert payload["target_url"] == target


def test_process_incoming_update_actor_refreshes_profile(engine, tmp_path, monkeypatch):
    """An Update of the actor document refreshes actor_* payload fields."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    actor = "https://remote.example/users/bob"
    _process(
        engine,
        {
            "type": "Follow",
            "id": "https://remote.example/activities/f1",
            "actor": actor,
            "object": "https://music.example.com/users/alice",
        },
    )
    rows = _notifications_for(engine, user.id)
    assert rows[0].payload["actor_name"] == "bob"

    _process(
        engine,
        {
            "type": "Update",
            "id": "https://remote.example/activities/uu6",
            "actor": actor,
            "object": {
                "type": "Person",
                "id": actor,
                "preferredUsername": "bobby",
                "name": "Bob The Rocker",
                "icon": {"type": "Image", "url": "https://remote.example/avatars/bob2.png"},
            },
        },
    )

    rows = _notifications_for(engine, user.id)
    payload = rows[0].payload
    assert payload["actor_name"] == "bobby"
    assert payload["actor_display_name"] == "Bob The Rocker"
    assert payload["actor_avatar_url"] == "https://remote.example/avatars/bob2.png"


def test_process_incoming_update_ignores_other_actors(engine, tmp_path, monkeypatch):
    """An Update only touches the sender's own notifications."""
    config = _make_config(tmp_path)
    user = _seed_alice(engine, config)
    monkeypatch.setattr("songhive.tasks.federation.load_config", lambda *_, **__: config)

    note_id = "https://remote.example/notes/u7"
    for actor in ("https://remote.example/users/bob", "https://remote.example/users/carol"):
        _process(
            engine,
            {
                "type": "Create",
                "id": f"https://remote.example/activities/c-{actor.rsplit('/', 1)[-1]}",
                "actor": actor,
                "object": {
                    "type": "Note",
                    "id": note_id if "bob" in actor else f"{note_id}-carol",
                    "content": "<p>hi</p>",
                    "inReplyTo": "https://music.example.com/users/alice/objects/t1",
                },
            },
        )
    assert len(_notifications_for(engine, user.id)) == 2

    _process(
        engine,
        {
            "type": "Update",
            "id": "https://remote.example/activities/uu7",
            "actor": "https://remote.example/users/bob",
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>bob edited</p>",
                "inReplyTo": "https://music.example.com/users/alice/objects/t1",
            },
        },
    )

    rows = {r.actor_url: r for r in _notifications_for(engine, user.id)}
    assert rows["https://remote.example/users/bob"].payload["object_content"] == "<p>bob edited</p>"
    assert rows["https://remote.example/users/carol"].payload["object_content"] == "<p>hi</p>"
