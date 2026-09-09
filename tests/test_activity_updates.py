"""
Activity update tests - the ``update_activity`` service and the
``PATCH /api/v1/activities/{id}`` endpoint: content edits re-run the
mention pipeline, replace ``activity_mentions`` rows, rebuild an
embedded payload object's ``content``/``tag``, and fan an ``Update``
carrying the rebuilt object out to the inboxes that already received the
activity via ``fan_out_activity_update``; visibility edits cascade
through ``VisibilityRules.cascade_visibility_update``.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from songhive.federation.activities import create_object_update_activity
from songhive.models._enums import Visibility
from songhive.models.activity import Activity, ActivityMention, ActivityTarget
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.activities import (
    fan_out_activity_update,
    sync_track_publications,
    update_activity,
)


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


async def _mention_rows(session, activity_id) -> list[ActivityMention]:
    """Return the persisted mention rows for an activity."""
    result = await session.execute(select(ActivityMention).where(ActivityMention.activity_id == activity_id))
    return list(result.scalars().all())


async def _target_rows(session, activity_id) -> list[ActivityTarget]:
    """Return the persisted delivery targets for an activity."""
    result = await session.execute(select(ActivityTarget).where(ActivityTarget.activity_id == activity_id))
    return list(result.scalars().all())


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


def _patch_resolution(monkeypatch, followers=(), inboxes=None):
    """Stub follower collection and per-actor inbox resolution."""
    followers_mock = MagicMock(return_value=list(followers))
    monkeypatch.setattr("songhive.services.federation.get_follower_inboxes", followers_mock)
    inboxes = dict(inboxes or {})
    resolve_mock = MagicMock(side_effect=lambda actor_url, config, **_: inboxes.get(actor_url))
    monkeypatch.setattr("songhive.services.federation.resolve_actor_inbox", resolve_mock)
    return followers_mock, resolve_mock


# ---------------------------------------------------------------------------
# update_activity service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_activity_renders_content(db_session, regular_user, other_user, config):
    """A content edit re-renders safe HTML with mention and tag links."""
    config.federation.instance_domain = "local.example"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        content="old",
        content_source="old",
    )
    db_session.add(activity)
    await db_session.flush()

    await update_activity(
        db_session,
        activity,
        content_source="hi @other #rock",
        config=config,
    )

    assert activity.content_source == "hi @other #rock"
    assert activity.content_type == "text/markdown"
    assert 'href="https://local.example/users/other"' in activity.content
    assert 'href="https://local.example/tags/rock"' in activity.content


@pytest.mark.asyncio
async def test_update_activity_replaces_mentions(db_session, regular_user, other_user, config):
    """Editing content replaces the activity's mention rows."""
    config.federation.instance_domain = "local.example"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    activity.mentions.append(ActivityMention(handle="@stale", user_id=regular_user.id))
    db_session.add(activity)
    await db_session.flush()

    await update_activity(db_session, activity, content_source="hi @other", config=config)

    mentions = await _mention_rows(db_session, activity.id)
    assert [(m.handle, m.user_id) for m in mentions] == [("@other", str(other_user.id))]


@pytest.mark.asyncio
async def test_update_activity_rebuilds_payload_object(db_session, regular_user, other_user, config):
    """An embedded payload object gets its content and tags rebuilt."""
    config.federation.instance_domain = "local.example"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {
                "id": "https://local.example/users/alice/objects/1",
                "content": "old",
                "tag": [{"type": "Hashtag", "name": "#genre", "href": "/tags/genre"}],
            },
        },
    )
    db_session.add(activity)
    await db_session.flush()

    await update_activity(
        db_session,
        activity,
        content_source="hi @other #rock",
        config=config,
    )
    await db_session.flush()

    obj = activity.payload["object"]
    assert 'href="https://local.example/users/other"' in obj["content"]
    assert obj["updated"]
    tag_names = {tag["name"] for tag in obj["tag"]}
    assert tag_names == {"#genre", "#rock", "@other"}
    mention = next(tag for tag in obj["tag"] if tag["type"] == "Mention")
    assert mention["href"] == "https://local.example/users/other"


@pytest.mark.asyncio
async def test_update_activity_normalizes_audio_payload(db_session, regular_user, config):
    """An edited ``Audio`` payload drops ``summary`` and keeps the track link in ``content``."""
    config.federation.instance_domain = "local.example"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {
                "id": "https://local.example/users/alice/objects/1",
                "type": "Audio",
                "name": "Test Artist - Test Track",
                "content": "old",
                "summary": "old",
                "url": [
                    {"type": "Link", "href": "https://local.example/audio/stream", "mediaType": "audio/mpeg"},
                    {
                        "type": "Link",
                        "href": f"https://local.example/tracks/{track.id}",
                        "mediaType": "text/html",
                    },
                ],
            },
        },
    )
    db_session.add(activity)
    await db_session.flush()

    await update_activity(db_session, activity, content_source="new post", config=config)
    await db_session.flush()

    obj = activity.payload["object"]
    assert "summary" not in obj
    assert obj["content"] == (
        f'new post<p><a href="https://local.example/tracks/{track.id}">Test Artist - Test Track</a></p>'
    )
    assert activity.content == obj["content"]


@pytest.mark.asyncio
async def test_update_activity_audio_link_is_not_duplicated(db_session, regular_user, config):
    """Re-normalizing an already-linked ``Audio`` content does not duplicate the link."""
    config.federation.instance_domain = "local.example"
    track = await _make_track(db_session, regular_user)
    link = f'<p><a href="https://local.example/tracks/{track.id}">Test Artist - Test Track</a></p>'
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {
                "id": "https://local.example/users/alice/objects/1",
                "type": "Audio",
                "name": "Test Artist - Test Track",
                "content": f"old{link}",
                "url": [
                    {
                        "type": "Link",
                        "href": f"https://local.example/tracks/{track.id}",
                        "mediaType": "text/html",
                    },
                ],
            },
        },
    )
    db_session.add(activity)
    await db_session.flush()

    await update_activity(db_session, activity, content_source="new post", config=config)
    await db_session.flush()

    assert activity.payload["object"]["content"] == f"new post{link}"


@pytest.mark.asyncio
async def test_update_activity_note_share_keeps_track_link(db_session, regular_user, config):
    """Editing a ``Note`` share re-appends the track link despite ``url`` being the object's id.

    A share's ``url`` is its own object id, so the track page cannot be
    derived from the stored object — ``update_activity`` passes it
    explicitly for track-bound activities.
    """
    config.federation.instance_domain = "local.example"
    track = await _make_track(db_session, regular_user)
    object_id = "https://local.example/users/alice/objects/share-1"
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {
                "id": object_id,
                "type": "Note",
                "name": "Test Artist - Test Track",
                "content": "old",
                "url": object_id,
            },
        },
    )
    db_session.add(activity)
    await db_session.flush()

    await update_activity(db_session, activity, content_source="new post", config=config)
    await db_session.flush()

    obj = activity.payload["object"]
    assert obj["content"] == (
        f'new post<p><a href="https://local.example/tracks/{track.id}">Test Artist - Test Track</a></p>'
    )


@pytest.mark.asyncio
async def test_update_activity_leaves_string_object_payload(db_session, regular_user, config):
    """Payloads whose ``object`` is a bare id (e.g. ``Like``) are untouched."""
    track = await _make_track(db_session, regular_user)
    payload = {"type": "Like", "object": "https://remote.example/objects/1"}
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        activity_type="like",
        payload=payload,
    )
    db_session.add(activity)
    await db_session.flush()

    await update_activity(db_session, activity, content_source="new #tag", config=config)

    assert activity.payload == payload


@pytest.mark.asyncio
async def test_update_activity_retracted(db_session, regular_user, config):
    """A retracted activity cannot be edited."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    activity.deleted_at = datetime.now(timezone.utc)
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await update_activity(db_session, activity, content_source="new", config=config)
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/v1/activities/{activity_id}
# ---------------------------------------------------------------------------


def test_update_endpoint_requires_auth(client):
    """Unauthenticated requests are rejected."""
    resp = client.patch("/api/v1/activities/whatever", json={"content": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_update_endpoint_not_found(client, regular_user, auth_headers):
    """A missing activity returns 404."""
    resp = client.patch(
        "/api/v1/activities/missing-id",
        json={"content": "x"},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_endpoint_forbidden(client, db_session, regular_user, other_user, auth_headers):
    """Users without manage rights on the entity cannot edit its activities."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.patch(
        f"/api/v1/activities/{activity.id}",
        json={"content": "x"},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_endpoint_content(client, db_session, regular_user, other_user, auth_headers):
    """The owner can edit content; mentions are re-resolved and persisted."""
    client.app.state.config.federation.instance_domain = "local.example"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.patch(
        f"/api/v1/activities/{activity.id}",
        json={"content": "hi @other"},
        headers=auth_headers(regular_user),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(activity.id)
    assert body["content_source"] == "hi @other"
    assert 'href="https://local.example/users/other"' in body["content"]
    assert body["mentions"][0]["handle"] == "@other"
    assert activity.content_source == "hi @other"
    assert 'href="https://local.example/users/other"' in activity.content
    mentions = await _mention_rows(db_session, activity.id)
    assert [m.handle for m in mentions] == ["@other"]


@pytest.mark.asyncio
async def test_update_endpoint_visibility(client, db_session, regular_user, auth_headers):
    """The owner can change the activity visibility."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.patch(
        f"/api/v1/activities/{activity.id}",
        json={"visibility": "local"},
        headers=auth_headers(regular_user),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(activity.id)
    assert body["visibility"] == Visibility.LOCAL.value
    assert activity.visibility == Visibility.LOCAL.value


@pytest.mark.asyncio
async def test_update_endpoint_visibility_exceeds_entity(client, db_session, regular_user, auth_headers):
    """A visibility that exceeds the containing entity's is rejected."""
    track = await _make_track(db_session, regular_user, visibility=Visibility.LOCAL.value)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.LOCAL.value,
    )
    db_session.add(activity)
    await db_session.flush()

    resp = client.patch(
        f"/api/v1/activities/{activity.id}",
        json={"visibility": "public"},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_endpoint_admin(client, db_session, admin_user, other_user, auth_headers):
    """Admins may edit activities on entities they do not own."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.patch(
        f"/api/v1/activities/{activity.id}",
        json={"content": "moderated"},
        headers=auth_headers(admin_user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(activity.id)
    assert body["content_source"] == "moderated"
    assert activity.content_source == "moderated"


@pytest.mark.asyncio
async def test_update_endpoint_retracted(client, db_session, regular_user, auth_headers):
    """A retracted activity cannot be edited through the API."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    activity.deleted_at = datetime.now(timezone.utc)
    db_session.add(activity)
    await db_session.flush()

    resp = client.patch(
        f"/api/v1/activities/{activity.id}",
        json={"content": "x"},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_endpoint_content_and_visibility(
    client, db_session, regular_user, other_user, auth_headers, monkeypatch
):
    """PATCH with both content and visibility on a payload-bearing activity
    persists both changes and does not crash on the mention relationship."""
    config = client.app.state.config
    config.federation.enabled = True
    config.federation.instance_domain = "local.example"
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {
                "id": "https://local.example/users/regular/objects/1",
                "type": "Audio",
                "name": "Test Track",
            },
        },
    )
    activity.mentions.append(
        ActivityMention(
            handle="@other",
            user_id=other_user.id,
            actor_url="https://local.example/users/other",
        )
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch)
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    resp = client.patch(
        f"/api/v1/activities/{activity.id}",
        json={"content": "hi @other", "visibility": "local"},
        headers=auth_headers(regular_user),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(activity.id)
    assert body["visibility"] == Visibility.LOCAL.value
    assert body["content_source"] == "hi @other"
    assert 'href="https://local.example/users/other"' in body["content"]
    assert activity.visibility == Visibility.LOCAL.value
    assert 'href="https://local.example/users/other"' in activity.content
    assert deliver.delay.called


# ---------------------------------------------------------------------------
# create_object_update_activity
# ---------------------------------------------------------------------------


def test_create_object_update_activity():
    """The Update embeds the full object with rewritten audience and a stamp."""
    object_doc = {
        "id": "https://local.example/users/alice/objects/1",
        "type": "Note",
        "content": "<p>edited</p>",
        "to": ["https://stale.example/audience"],
    }
    payload = create_object_update_activity(
        "https://local.example/users/alice",
        object_doc,
        Visibility.PUBLIC,
    )

    assert payload["type"] == "Update"
    assert payload["actor"] == "https://local.example/users/alice"
    assert payload["id"].startswith("https://local.example/users/alice/activities/")
    assert payload["to"] == ["https://www.w3.org/ns/activitystreams#Public"]
    assert payload["cc"] == ["https://local.example/users/alice/followers"]
    obj = payload["object"]
    assert obj["id"] == object_doc["id"]
    assert obj["content"] == "<p>edited</p>"
    assert obj["to"] == payload["to"]
    assert obj["cc"] == payload["cc"]
    assert obj["updated"] == payload["published"]
    # The stored document is not mutated.
    assert object_doc["to"] == ["https://stale.example/audience"]
    assert "updated" not in object_doc


# ---------------------------------------------------------------------------
# fan_out_activity_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_out_activity_update_delivers_to_sent_inboxes(db_session, regular_user, config, monkeypatch):
    """A content edit fans an Update carrying the rebuilt object to sent inboxes."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
        content="old",
        content_source="old",
        payload={
            "type": "Create",
            "object": {
                "id": "https://local.example/users/regular/objects/1",
                "type": "Note",
                "content": "old",
            },
        },
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    activity.targets.append(ActivityTarget(inbox_url="https://b.example/inbox", state="sent"))
    activity.targets.append(ActivityTarget(inbox_url="https://c.example/inbox", state="failed"))
    db_session.add(activity)
    await db_session.flush()

    await update_activity(db_session, activity, content_source="edited content", config=config)

    _patch_resolution(monkeypatch)
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await fan_out_activity_update(db_session, activity, config) == 2

    assert deliver.delay.call_count == 2
    for call in deliver.delay.call_args_list:
        payload, inbox, key_id, key = call.args
        assert payload["type"] == "Update"
        assert payload["actor"] == regular_user.actor_url
        obj = payload["object"]
        assert obj["id"] == "https://local.example/users/regular/objects/1"
        assert "edited content" in obj["content"]
        assert obj["updated"]
        assert obj["to"] == payload["to"]
        assert inbox in {"https://a.example/inbox", "https://b.example/inbox"}
        assert key_id == f"{regular_user.actor_url}#main-key"
        assert key == "private-key"


@pytest.mark.asyncio
async def test_fan_out_activity_update_reaches_new_mentions(db_session, regular_user, config, monkeypatch):
    """Actors first reached by the edit receive the stored payload, not the Update."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {"id": "https://local.example/users/regular/objects/1", "content": "old"},
        },
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    activity.mentions.append(
        ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
    )
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, inboxes={"https://remote.example/users/bob": "https://remote.example/inbox"})
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await fan_out_activity_update(db_session, activity, config) == 1

    deliveries = {call.args[1]: call.args[0] for call in deliver.delay.call_args_list}
    assert set(deliveries) == {"https://a.example/inbox", "https://remote.example/inbox"}
    assert deliveries["https://a.example/inbox"]["type"] == "Update"
    # The new recipient gets the stored Create carrying the updated object.
    assert deliveries["https://remote.example/inbox"] is activity.payload
    targets = {t.inbox_url: t for t in await _target_rows(db_session, activity.id)}
    assert targets["https://remote.example/inbox"].state == "sent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"source_type": "remote"},
        {"visibility": Visibility.PRIVATE.value},
        {"visibility": Visibility.LOCAL.value},
        {"payload": {"type": "Like", "object": "https://remote.example/objects/1"}},
        {"payload": None},
        {"deleted_at": datetime.now(timezone.utc)},
    ],
)
async def test_fan_out_activity_update_noop(db_session, regular_user, config, monkeypatch, overrides):
    """Remote, retracted, non-federating, or object-less activities deliver nothing."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    params = {
        "source_actor": regular_user.actor_url,
        "owner_user_id": regular_user.id,
        "payload": {
            "type": "Create",
            "object": {"id": "https://local.example/users/regular/objects/1", "content": "old"},
        },
    }
    params.update(overrides)
    activity = _make_activity("track", track.id, **params)
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch, followers=["https://new.example/inbox"])
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await fan_out_activity_update(db_session, activity, config) == 0
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_fan_out_activity_update_disabled_federation(db_session, regular_user, config, monkeypatch):
    """No delivery happens when federation is disabled."""
    config.federation.enabled = False
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {"id": "https://local.example/users/regular/objects/1", "content": "old"},
        },
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await fan_out_activity_update(db_session, activity, config) == 0
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_fan_out_activity_update_without_signing_key(db_session, regular_user, config, monkeypatch):
    """No delivery happens when the owner cannot sign."""
    config = _fed_config(config)
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {"id": "https://local.example/users/regular/objects/1", "content": "old"},
        },
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await fan_out_activity_update(db_session, activity, config) == 0
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_update_endpoint_fans_out_update(client, db_session, regular_user, auth_headers, monkeypatch):
    """A content edit through the API delivers an Update to sent inboxes."""
    config = client.app.state.config
    config.federation.enabled = True
    config.federation.instance_domain = "local.example"
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {"id": "https://local.example/users/regular/objects/1", "content": "old"},
        },
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch)
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    resp = client.patch(
        f"/api/v1/activities/{activity.id}",
        json={"content": "edited"},
        headers=auth_headers(regular_user),
    )

    assert resp.status_code == 200
    deliver.delay.assert_called_once()
    payload, inbox, key_id, _ = deliver.delay.call_args.args
    assert payload["type"] == "Update"
    assert "edited" in payload["object"]["content"]
    assert payload["object"]["updated"]
    assert inbox == "https://a.example/inbox"
    assert key_id == f"{regular_user.actor_url}#main-key"


# ---------------------------------------------------------------------------
# sync_track_publications — track metadata edits re-sync published objects
# ---------------------------------------------------------------------------


def _publication_activity(track, owner: User, **overrides) -> Activity:
    """Build a local ``create`` activity carrying a ``Create(Audio)`` payload."""
    object_id = "https://local.example/users/regular/objects/pub-1"
    params = {
        "source_actor": owner.actor_url,
        "source_id": object_id,
        "owner_user_id": owner.id,
        "payload": {
            "type": "Create",
            "object": {
                "id": object_id,
                "type": "Audio",
                "name": "Test Artist - Test Track",
                "content": "old description",
            },
        },
    }
    params.update(overrides)
    return _make_activity("track", track.id, **params)


@pytest.mark.asyncio
async def test_sync_track_publications_rebuilds_and_fans_out(db_session, regular_user, config, monkeypatch):
    """A track metadata edit rebuilds the published object and fans an Update out."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _publication_activity(track, regular_user)
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    track.title = "Renamed"
    track.description = "fresh description"
    await db_session.flush()

    _patch_resolution(monkeypatch)
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await sync_track_publications(db_session, track, config=config) == 1

    obj = activity.payload["object"]
    assert obj["id"] == "https://local.example/users/regular/objects/pub-1"
    assert "Renamed" in obj["name"]
    assert "fresh description" in obj["content"]
    assert "summary" not in obj
    assert obj["content"].endswith(
        f'<p><a href="https://local.example/tracks/{track.id}">Test Artist - Renamed</a></p>'
    )
    assert obj["published"]
    assert obj["updated"]
    assert activity.content == obj["content"]

    deliver.delay.assert_called_once()
    payload, inbox, key_id, _ = deliver.delay.call_args.args
    assert payload["type"] == "Update"
    assert payload["object"]["id"] == obj["id"]
    assert "fresh description" in payload["object"]["content"]
    assert payload["object"]["updated"]
    assert inbox == "https://a.example/inbox"
    assert key_id == f"{regular_user.actor_url}#main-key"


@pytest.mark.asyncio
async def test_sync_track_publications_preserves_oneoff_status(db_session, regular_user, config, monkeypatch):
    """A publication made with a custom status keeps that text as the post body."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _publication_activity(
        track,
        regular_user,
        content_source="custom share text",
        content="custom share text",
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    track.title = "Renamed"
    track.description = "fresh description"
    await db_session.flush()

    _patch_resolution(monkeypatch)
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await sync_track_publications(db_session, track, config=config) == 1

    obj = activity.payload["object"]
    assert "custom share text" in obj["content"]
    assert "fresh description" not in obj["content"]
    assert "Renamed" in obj["name"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"source_type": "remote"},
        {"deleted_at": datetime.now(timezone.utc)},
        {"payload": None},
        {"payload": {"type": "Create", "object": "https://remote.example/objects/1"}},
        {"activity_type": "like"},
    ],
)
async def test_sync_track_publications_skips_non_publication_rows(
    db_session, regular_user, config, monkeypatch, overrides
):
    """Remote, retracted, payload-less, and non-create activities are untouched."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _publication_activity(track, regular_user, **overrides)
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    track.description = "fresh description"
    await db_session.flush()

    _patch_resolution(monkeypatch)
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await sync_track_publications(db_session, track, config=config) == 0
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_sync_track_publications_noop_gates(db_session, regular_user, config, monkeypatch):
    """Non-public tracks, missing artists, and disabled federation do nothing."""
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user, visibility=Visibility.LOCAL.value)
    activity = _publication_activity(track, regular_user, visibility=Visibility.LOCAL.value)
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    # Non-public track.
    config = _fed_config(config)
    assert await sync_track_publications(db_session, track, config=config) == 0

    # Federation disabled.
    track.visibility = Visibility.PUBLIC.value
    config.federation.enabled = False
    assert await sync_track_publications(db_session, track, config=config) == 0
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_track_update_endpoint_syncs_publication(client, db_session, regular_user, auth_headers, monkeypatch):
    """PATCH /tracks/{id} re-syncs the stored object and delivers an Update."""
    config = client.app.state.config
    config.federation.enabled = True
    config.federation.instance_domain = "local.example"
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    activity = _publication_activity(track, regular_user)
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    _patch_resolution(monkeypatch)
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    resp = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"description": "brand new description", "title": "New Title"},
        headers=auth_headers(regular_user),
    )

    assert resp.status_code == 200
    deliver.delay.assert_called_once()
    payload, inbox, _, _ = deliver.delay.call_args.args
    assert payload["type"] == "Update"
    assert "brand new description" in payload["object"]["content"]
    assert "New Title" in payload["object"]["name"]
    assert payload["object"]["updated"]
    assert inbox == "https://a.example/inbox"


@pytest.mark.asyncio
async def test_track_update_endpoint_without_publication(client, db_session, regular_user, auth_headers, monkeypatch):
    """A metadata edit on an unpublished track delivers nothing."""
    config = client.app.state.config
    config.federation.enabled = True
    config.federation.instance_domain = "local.example"
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)

    _patch_resolution(monkeypatch)
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    resp = client.patch(
        f"/api/v1/tracks/{track.id}",
        json={"description": "brand new description"},
        headers=auth_headers(regular_user),
    )

    assert resp.status_code == 200
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_sync_track_publications_rebuilds_note_share(db_session, regular_user, config, monkeypatch):
    """A metadata edit rebuilds a ``Note`` share as a Note, not an Audio."""
    config = _fed_config(config)
    _federated_user(regular_user)
    track = await _make_track(db_session, regular_user)
    track.federation_object_id = "obj-1"
    object_id = "https://local.example/users/regular/objects/share-1"
    activity = _publication_activity(
        track,
        regular_user,
        source_id=object_id,
        content_source="custom share text",
        content="custom share text",
        payload={
            "type": "Create",
            "object": {
                "id": object_id,
                "type": "Note",
                "name": "Test Artist - Test Track",
                "published": "2026-03-14T12:00:00+00:00",
                "url": object_id,
                "content": "custom share text",
                "attachment": [
                    {
                        "type": "Audio",
                        "id": "https://local.example/users/regular/objects/obj-1",
                        "mediaType": "audio/mpeg",
                        "url": "https://local.example/api/v1/files/file-1/download",
                        "name": "Test Track",
                    }
                ],
            },
        },
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    track.title = "Renamed"
    await db_session.flush()

    _patch_resolution(monkeypatch)
    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    assert await sync_track_publications(db_session, track, config=config) == 1

    obj = activity.payload["object"]
    assert obj["type"] == "Note"
    assert obj["id"] == object_id
    # The share's ``url`` is its own object id — the track page stays in the
    # appended content link.
    assert obj["url"] == object_id
    assert "Renamed" in obj["name"]
    # The stored share date is preserved rather than re-stamped.
    assert obj["published"] == "2026-03-14T12:00:00+00:00"
    # The one-off share text survives the metadata re-sync, still ending
    # with the track-page link.
    assert "custom share text" in obj["content"]
    assert obj["content"].endswith(f'<a href="https://local.example/tracks/{track.id}">Test Artist - Renamed</a></p>')
    assert obj["attachment"][0]["type"] == "Audio"
    assert obj["attachment"][0]["id"] == "https://local.example/users/regular/objects/obj-1"
    assert obj["updated"]

    deliver.delay.assert_called_once()
    payload, _, _, _ = deliver.delay.call_args.args
    assert payload["type"] == "Update"
    assert payload["object"]["type"] == "Note"
