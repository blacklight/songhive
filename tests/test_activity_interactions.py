"""
Activity interaction tests - the like/boost/reply services and endpoints,
activity view rules, ``Like``/``Announce`` payload building, interaction
summaries, remote inbox resolution, and fan-out.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests
from fastapi import HTTPException
from pubby import Interaction, InteractionType
from sqlalchemy import select

from songhive.federation.activities import (
    AS_PUBLIC,
    create_announce_activity,
    create_like_activity,
    create_undo_activity,
)
from songhive.models._enums import Visibility
from songhive.models.activity import Activity, ActivityMention, ActivityTarget
from songhive.models.artist import Artist
from songhive.models.notification import Notification
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import activities as activity_service
from songhive.services import federation as federation_service
from songhive.services.activities import (
    boost_activity,
    can_view_activity,
    fan_out_unreaction_activity,
    like_activity,
    list_activity_interactors,
    list_activity_replies,
    reply_to_activity,
    resolve_interaction_summaries,
    unreact_activity,
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


def _fed_config(config):
    """Enable federation on the test config."""
    config.federation.enabled = True
    config.federation.instance_domain = "local.example"
    return config


class _FakeStorage:
    """In-memory stand-in for the pubby actor cache."""

    def __init__(self, cached: dict | None = None):
        self.cached = dict(cached or {})
        self.cache_writes = []

    def get_cached_actor(self, actor_id, max_age_seconds=86400.0):
        return self.cached.get(actor_id)

    def cache_remote_actor(self, actor_id, actor_data, fetched_at=None):
        self.cache_writes.append((actor_id, actor_data))
        self.cached[actor_id] = actor_data


class _FakeResponse:
    """Minimal ``requests`` response stub."""

    def __init__(self, data, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._data


# ---------------------------------------------------------------------------
# can_view_activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_can_view_activity_public(db_session, regular_user, other_user):
    """A public activity on a public entity is viewable by anyone."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    assert await can_view_activity(db_session, regular_user, activity)
    assert await can_view_activity(db_session, None, activity)


@pytest.mark.asyncio
async def test_can_view_activity_local_requires_auth(db_session, regular_user, other_user):
    """A local activity is viewable by authenticated users only."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.LOCAL.value,
    )
    db_session.add(activity)
    await db_session.flush()

    assert await can_view_activity(db_session, regular_user, activity)
    assert not await can_view_activity(db_session, None, activity)


@pytest.mark.asyncio
async def test_can_view_activity_mentioned(db_session, regular_user, other_user, make_user):
    """A mentioned-only activity is viewable by the owner and mentioned users."""
    third = await make_user("third", email_verified=True)
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.MENTIONED.value,
    )
    activity.mentions.append(ActivityMention(handle="@regular", user_id=regular_user.id))
    db_session.add(activity)
    await db_session.flush()

    assert await can_view_activity(db_session, regular_user, activity)
    assert await can_view_activity(db_session, other_user, activity)
    assert not await can_view_activity(db_session, third, activity)
    assert not await can_view_activity(db_session, None, activity)


@pytest.mark.asyncio
async def test_can_view_activity_private_owner_only(db_session, regular_user, other_user, admin_user):
    """A private activity is limited to its owner; admins get no bypass."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(activity)
    await db_session.flush()

    assert await can_view_activity(db_session, other_user, activity)
    assert not await can_view_activity(db_session, admin_user, activity)
    assert not await can_view_activity(db_session, regular_user, activity)


@pytest.mark.asyncio
async def test_can_view_activity_followers(db_session, regular_user, other_user):
    """Followers-visible activities are viewable by any authenticated user."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.FOLLOWERS.value,
    )
    db_session.add(activity)
    await db_session.flush()

    assert await can_view_activity(db_session, regular_user, activity)
    assert not await can_view_activity(db_session, None, activity)


@pytest.mark.asyncio
async def test_can_view_activity_inaccessible_entity(db_session, regular_user, other_user):
    """An activity on an inaccessible entity is not viewable."""
    track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(activity)
    await db_session.flush()

    assert not await can_view_activity(db_session, regular_user, activity)


@pytest.mark.asyncio
async def test_can_view_activity_retracted(db_session, regular_user):
    """Soft-deleted activities are never viewable."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()
    activity.deleted_at = datetime.now(timezone.utc)
    await db_session.flush()

    assert not await can_view_activity(db_session, regular_user, activity)


# ---------------------------------------------------------------------------
# like_activity service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_like_activity_creates_like(db_session, regular_user, other_user):
    """Liking records a like activity on the same entity, inheriting visibility."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)

    assert like.activity_type == "like"
    assert like.entity_type == "track"
    assert like.entity_id == str(track.id)
    assert like.in_reply_to_activity_id == str(activity.id)
    assert like.owner_user_id == regular_user.id
    assert like.visibility == activity.visibility
    assert like.payload["type"] == "Like"
    assert like.payload["actor"] == f"urn:songhive:user:{regular_user.username}"
    assert like.payload["object"] == activity.source_id
    assert like.payload["id"] == like.source_id
    assert like.payload["to"] == [AS_PUBLIC]


@pytest.mark.asyncio
async def test_like_activity_does_not_require_manage(db_session, regular_user, other_user):
    """A user may like an activity on an entity they can view but not manage."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)
    assert like.id is not None


@pytest.mark.asyncio
async def test_like_activity_idempotent(db_session, regular_user, other_user):
    """Liking the same activity twice raises a 400."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    await like_activity(db_session, activity=activity, author=regular_user)
    with pytest.raises(HTTPException) as excinfo:
        await like_activity(db_session, activity=activity, author=regular_user)
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_like_activity_retracted_target(db_session, regular_user):
    """A retracted activity cannot be liked."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()
    activity.deleted_at = datetime.now(timezone.utc)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await like_activity(db_session, activity=activity, author=regular_user)
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_like_activity_mentions_address_original_audience(db_session, regular_user, other_user):
    """The Like payload addresses the target's author and mentioned actors."""
    other_user.actor_url = "https://local.example/users/other"
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        source_actor=other_user.actor_url,
        visibility=Visibility.MENTIONED.value,
    )
    activity.mentions.append(
        ActivityMention(handle="@bob@remote.example", actor_url="https://remote.example/users/bob")
    )
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)

    assert sorted(like.payload["to"]) == sorted([other_user.actor_url, "https://remote.example/users/bob"])


# ---------------------------------------------------------------------------
# like_activity notifications
# ---------------------------------------------------------------------------


async def _notifications_for(session, user_id) -> list[Notification]:
    result = await session.execute(select(Notification).where(Notification.user_id == user_id))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_like_activity_notifies_local_owner(db_session, regular_user, other_user):
    """Liking another local user's activity creates a like notification."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)

    notifications = await _notifications_for(db_session, other_user.id)
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.type == "like"
    assert notification.actor_url == f"urn:songhive:user:{regular_user.username}"
    assert notification.source_url == activity.source_id
    payload = notification.payload
    assert payload["activity_id"] == like.source_id
    assert payload["actor_name"] == regular_user.username
    assert payload["item_type"] == "track"
    assert payload["item_id"] == str(track.id)
    assert payload["item_title"] == track.title
    assert payload["local_url"] == f"/tracks/{track.id}"
    # The reacted activity's own identity lets the client render the card.
    assert payload["object_activity_id"] == str(activity.id)
    assert payload["object_page_url"] == f"/tracks/{track.id}/activities"
    assert "object_type" not in payload  # the activity carries no payload object


@pytest.mark.asyncio
async def test_like_activity_notification_includes_object_type(db_session, regular_user, other_user):
    """The reacted activity's object type (Note/Audio) is in the payload."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        payload={"type": "Create", "object": {"type": "Note"}},
    )
    db_session.add(activity)
    await db_session.flush()

    await like_activity(db_session, activity=activity, author=regular_user)

    notification = (await _notifications_for(db_session, other_user.id))[0]
    assert notification.payload["object_type"] == "Note"


@pytest.mark.asyncio
async def test_like_activity_on_status_has_no_item_fields(db_session, regular_user, other_user):
    """Liking a standalone status links to the activity, not a bogus item.

    ``user`` entities have no item page: the notification carries the
    author's profile as ``local_url``/``object_page_url`` and no
    ``item_type``/``item_id`` so the client cannot build a broken
    ``/users/{id}`` card link.
    """
    activity = _make_activity(
        "user",
        other_user.id,
        owner_user_id=other_user.id,
        source_actor=f"urn:songhive:user:{other_user.username}",
        payload={"type": "Create", "object": {"type": "Note"}},
    )
    db_session.add(activity)
    await db_session.flush()

    await like_activity(db_session, activity=activity, author=regular_user)

    notification = (await _notifications_for(db_session, other_user.id))[0]
    payload = notification.payload
    assert "item_type" not in payload
    assert "item_id" not in payload
    assert payload["item_title"] == (other_user.display_name or other_user.username)
    assert payload["local_url"] == f"/@{other_user.username}"
    assert payload["object_page_url"] == f"/@{other_user.username}"
    assert payload["object_activity_id"] == str(activity.id)
    assert payload["object_type"] == "Note"


@pytest.mark.asyncio
async def test_like_activity_self_like_no_notification(db_session, regular_user):
    """Liking one's own activity does not notify."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    await like_activity(db_session, activity=activity, author=regular_user)

    assert await _notifications_for(db_session, regular_user.id) == []


@pytest.mark.asyncio
async def test_like_activity_remote_target_no_notification(db_session, regular_user, other_user):
    """Liking a remote-authored activity (no local owner) does not notify."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=None,
        source_type="remote",
        source_actor="https://remote.example/users/bob",
        source_id="https://remote.example/users/bob/objects/1",
    )
    db_session.add(activity)
    await db_session.flush()

    await like_activity(db_session, activity=activity, author=regular_user)

    assert await _notifications_for(db_session, other_user.id) == []


@pytest.mark.asyncio
async def test_like_activity_retract_removes_notification(db_session, regular_user, other_user):
    """Retracting the like removes the notification it produced."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)
    assert await _notifications_for(db_session, other_user.id) != []

    await activity_service.retract_activity(db_session, like)

    assert await _notifications_for(db_session, other_user.id) == []


# ---------------------------------------------------------------------------
# create_like_activity payload builder
# ---------------------------------------------------------------------------


def test_create_like_activity_public_audience():
    """A public Like addresses the AS public collection with followers in cc."""
    payload = create_like_activity(
        "https://local.example/users/alice",
        "https://remote.example/objects/1",
        Visibility.PUBLIC,
    )
    assert payload["type"] == "Like"
    assert payload["actor"] == "https://local.example/users/alice"
    assert payload["object"] == "https://remote.example/objects/1"
    assert payload["to"] == [AS_PUBLIC]
    assert payload["cc"] == ["https://local.example/users/alice/followers"]
    assert payload["id"].startswith("https://local.example/users/alice/activities/")


def test_create_like_activity_mentioned_audience():
    """A mentioned Like is addressed only to the given actor URLs."""
    payload = create_like_activity(
        "https://local.example/users/alice",
        "https://remote.example/objects/1",
        Visibility.MENTIONED,
        mention_actor_urls=["https://remote.example/users/bob"],
    )
    assert payload["to"] == ["https://remote.example/users/bob"]
    assert payload["cc"] == []


def test_create_like_activity_non_federated_audience():
    """Private and local Likes produce an empty audience."""
    for visibility in (Visibility.PRIVATE, Visibility.LOCAL):
        payload = create_like_activity(
            "https://local.example/users/alice",
            "https://remote.example/objects/1",
            visibility,
        )
        assert payload["to"] == []
        assert payload["cc"] == []


def test_create_like_activity_reuses_activity_id():
    """An explicit activity_id becomes the payload's ActivityPub id."""
    payload = create_like_activity(
        "https://local.example/users/alice",
        "https://remote.example/objects/1",
        Visibility.PUBLIC,
        activity_id="https://local.example/users/alice/objects/abc",
    )
    assert payload["id"] == "https://local.example/users/alice/objects/abc"


# ---------------------------------------------------------------------------
# resolve_actor_inbox
# ---------------------------------------------------------------------------


def test_resolve_actor_inbox_rejects_non_http(config):
    """Non-HTTP(S) actor ids have no resolvable inbox."""
    config = _fed_config(config)
    assert federation_service.resolve_actor_inbox("urn:songhive:user:bob", config) is None


def test_resolve_actor_inbox_blocked_domain(config):
    """Blocked domains are never contacted."""
    config = _fed_config(config)
    config.federation.blocked_instances = ["blocked.example"]
    assert federation_service.resolve_actor_inbox("https://blocked.example/users/bob", config) is None


def test_resolve_actor_inbox_uses_cache(config, monkeypatch):
    """A cached actor document short-circuits the HTTP fetch."""
    config = _fed_config(config)
    storage = _FakeStorage(
        {
            "https://remote.example/users/bob": {
                "id": "https://remote.example/users/bob",
                "inbox": "https://remote.example/users/bob/inbox",
                "endpoints": {"sharedInbox": "https://remote.example/inbox"},
            }
        }
    )
    monkeypatch.setattr("songhive.services.federation.create_activitypub_storage", lambda url: storage)
    get = MagicMock(side_effect=AssertionError("must not fetch"))
    monkeypatch.setattr("pubby.client.requests.get", get)

    inbox = federation_service.resolve_actor_inbox("https://remote.example/users/bob", config)

    assert inbox == "https://remote.example/inbox"
    get.assert_not_called()


def test_resolve_actor_inbox_fetches_and_caches(config, monkeypatch):
    """On a cache miss the actor document is fetched and cached."""
    config = _fed_config(config)
    storage = _FakeStorage()
    monkeypatch.setattr("songhive.services.federation.create_activitypub_storage", lambda url: storage)
    get = MagicMock(
        return_value=_FakeResponse(
            {
                "id": "https://remote.example/users/bob",
                "inbox": "https://remote.example/users/bob/inbox",
            }
        )
    )
    monkeypatch.setattr("pubby.client.requests.get", get)

    inbox = federation_service.resolve_actor_inbox("https://remote.example/users/bob", config)

    assert inbox == "https://remote.example/users/bob/inbox"
    get.assert_called_once()
    assert storage.cached["https://remote.example/users/bob"]["inbox"] == inbox


def test_resolve_actor_inbox_fetch_failure(config, monkeypatch):
    """A failed fetch resolves to None."""
    config = _fed_config(config)
    monkeypatch.setattr("songhive.services.federation.create_activitypub_storage", lambda url: _FakeStorage())
    monkeypatch.setattr(
        "pubby.client.requests.get",
        MagicMock(side_effect=requests.ConnectionError("boom")),
    )

    assert federation_service.resolve_actor_inbox("https://remote.example/users/bob", config) is None


# ---------------------------------------------------------------------------
# POST /api/v1/activities/{activity_id}/like
# ---------------------------------------------------------------------------


def test_like_endpoint_requires_auth(client):
    """Unauthenticated requests are rejected."""
    resp = client.post("/api/v1/activities/whatever/like")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_like_endpoint_not_found(client, regular_user, auth_headers):
    """A missing activity returns 404."""
    resp = client.post("/api/v1/activities/missing-id/like", headers=auth_headers(regular_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_like_endpoint_forbidden(client, db_session, regular_user, other_user, auth_headers):
    """Activities the user cannot view return 403."""
    track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_like_endpoint_creates_and_is_idempotent(client, db_session, regular_user, other_user, auth_headers):
    """A successful like returns 201 and the second like returns 400."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 201
    assert resp.json()["status"] == "ok"
    like_id = resp.json()["activity_id"]

    like = await db_session.get(Activity, like_id)
    assert like is not None
    assert like.activity_type == "like"
    assert like.in_reply_to_activity_id == str(activity.id)

    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_like_endpoint_fans_out_when_federated(client, db_session, regular_user, auth_headers, monkeypatch):
    """With federation enabled the like is fanned out to its audience."""
    client.app.state.config.federation.enabled = True
    client.app.state.config.federation.instance_domain = "local.example"
    fan_out = AsyncMock(return_value=0)
    monkeypatch.setattr(activity_service, "fan_out_like_activity", fan_out)

    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))

    assert resp.status_code == 201
    fan_out.assert_awaited_once()
    kwargs = fan_out.call_args.kwargs
    assert kwargs["target"].id == activity.id
    assert kwargs["like"].activity_type == "like"
    assert kwargs["author"].actor_url == "https://local.example/users/regular"
    assert kwargs["author"].private_key_pem


@pytest.mark.asyncio
async def test_like_endpoint_retracted_activity_404(client, db_session, regular_user, auth_headers):
    """A retracted activity cannot be liked through the API."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    activity.deleted_at = datetime.now(timezone.utc)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/activities/{activity_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_activity_endpoint(client, db_session, regular_user, other_user, auth_headers):
    """A viewable activity serializes with its interaction summary."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()
    await like_activity(db_session, activity=activity, author=regular_user)
    await db_session.commit()

    resp = client.get(f"/api/v1/activities/{activity.id}", headers=auth_headers(regular_user))

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(activity.id)
    assert body["activity_type"] == "create"
    assert body["like_count"] == 1
    assert body["liked"] is True
    assert body["can_interact"] is True


@pytest.mark.asyncio
async def test_get_activity_endpoint_anonymous_public(client, db_session, other_user):
    """Anonymous requesters may read public activities."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.commit()

    resp = client.get(f"/api/v1/activities/{activity.id}")

    assert resp.status_code == 200
    assert resp.json()["liked"] is False


@pytest.mark.asyncio
async def test_get_activity_endpoint_not_found(client, regular_user, auth_headers):
    """A missing activity returns 404."""
    resp = client.get("/api/v1/activities/missing-id", headers=auth_headers(regular_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_activity_endpoint_retracted_404(client, db_session, regular_user, auth_headers):
    """A retracted activity returns 404."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    activity.deleted_at = datetime.now(timezone.utc)
    db_session.add(activity)
    await db_session.commit()

    resp = client.get(f"/api/v1/activities/{activity.id}", headers=auth_headers(regular_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_activity_endpoint_forbidden(client, db_session, regular_user, other_user, auth_headers):
    """Activities the user cannot view return 403."""
    track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(activity)
    await db_session.commit()

    resp = client.get(f"/api/v1/activities/{activity.id}", headers=auth_headers(regular_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_activity_endpoint_object_fields_for_create(client, db_session, regular_user, auth_headers):
    """A ``Create`` activity exposes its embedded object's id and type."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        payload={
            "type": "Create",
            "object": {"type": "Audio", "id": "https://local.example/users/regular/objects/t1"},
        },
    )
    db_session.add(activity)
    await db_session.commit()

    resp = client.get(f"/api/v1/activities/{activity.id}", headers=auth_headers(regular_user))

    assert resp.status_code == 200
    body = resp.json()
    assert body["object_url"] == "https://local.example/users/regular/objects/t1"
    assert body["object_type"] == "Audio"
    assert body["can_interact"] is True


@pytest.mark.asyncio
async def test_get_activity_endpoint_reaction_fields(client, db_session, regular_user, other_user, auth_headers):
    """A reaction activity points ``object_url`` at the reacted object."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)
    await db_session.commit()

    resp = client.get(f"/api/v1/activities/{like.id}", headers=auth_headers(regular_user))

    assert resp.status_code == 200
    body = resp.json()
    assert body["activity_type"] == "like"
    assert body["object_url"] == activity.source_id
    # The object is a bare id reference, not an embedded document.
    assert body["object_type"] is None
    assert body["in_reply_to_activity_id"] == str(activity.id)
    # Reactions are not themselves interactable.
    assert body["can_interact"] is False


@pytest.mark.asyncio
async def test_update_endpoint_reaction_rejected(client, db_session, regular_user, other_user, auth_headers):
    """Like/announce activities carry no editable payload — PATCH is a 422."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)
    boost = await boost_activity(db_session, activity=activity, author=regular_user)
    await db_session.commit()

    for reaction_id in (like.id, boost.id):
        resp = client.patch(
            f"/api/v1/activities/{reaction_id}",
            json={"content": "edited"},
            headers=auth_headers(regular_user),
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_retract_activity_reaction_fans_out_undo(db_session, regular_user, other_user, config, monkeypatch):
    """Retracting a like enqueues ``Undo(Like)``, not ``Delete(Tombstone)``."""
    config = _fed_config(config)
    regular_user.private_key_pem = "private-key"
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)
    db_session.add(ActivityTarget(activity_id=like.id, inbox_url="https://remote.example/inbox", state="sent"))
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await activity_service.retract_activity(db_session, like)

    assert like.deleted_at is not None
    deliver.delay.assert_called_once()
    payload = deliver.delay.call_args.args[0]
    assert payload["type"] == "Undo"
    assert payload["object"]["type"] == "Like"
    assert payload["object"]["object"] == activity.source_id


@pytest.mark.asyncio
async def test_retract_activity_create_still_fans_out_delete(db_session, regular_user, config, monkeypatch):
    """Non-reaction retractions keep the ``Delete(Tombstone)`` behavior."""
    config = _fed_config(config)
    regular_user.private_key_pem = "private-key"
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        source_actor=regular_user.actor_url,
        source_id=f"{regular_user.actor_url}/objects/pub-1",
    )
    db_session.add(activity)
    await db_session.flush()
    db_session.add(ActivityTarget(activity_id=activity.id, inbox_url="https://remote.example/inbox", state="sent"))
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await activity_service.retract_activity(db_session, activity)

    assert activity.deleted_at is not None
    deliver.delay.assert_called_once()
    payload = deliver.delay.call_args.args[0]
    assert payload["type"] == "Delete"


# ---------------------------------------------------------------------------
# list_user_activities posts-mode filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_user_activities_posts_defaults(db_session, regular_user, other_user, config):
    """Posts mode includes creates and boosts, not replies or likes."""
    track = await _make_track(db_session, other_user)
    target = _make_activity("track", track.id, owner_user_id=other_user.id)
    own = _make_activity(
        "user",
        regular_user.id,
        owner_user_id=regular_user.id,
        source_id="https://local.example/users/regular/objects/own-1",
    )
    db_session.add_all([target, own])
    await db_session.flush()

    await boost_activity(db_session, activity=target, author=regular_user)
    await like_activity(db_session, activity=target, author=regular_user)
    await reply_to_activity(db_session, activity=target, author=regular_user, config=config, status_text="nice")

    activities, _ = await activity_service.list_user_activities(
        db_session, owner_user_id=str(regular_user.id), user=regular_user, mode="posts"
    )
    types = {a.activity_type for a in activities}
    assert types == {"create", "announce"}


@pytest.mark.asyncio
async def test_list_user_activities_posts_include_replies(db_session, regular_user, other_user, config):
    """``include_replies`` folds the user's replies into posts mode."""
    track = await _make_track(db_session, other_user)
    target = _make_activity("track", track.id, owner_user_id=other_user.id)
    own = _make_activity(
        "user",
        regular_user.id,
        owner_user_id=regular_user.id,
        source_id="https://local.example/users/regular/objects/own-1",
    )
    db_session.add_all([target, own])
    await db_session.flush()

    reply = await reply_to_activity(db_session, activity=target, author=regular_user, config=config, status_text="nice")

    activities, _ = await activity_service.list_user_activities(
        db_session,
        owner_user_id=str(regular_user.id),
        user=regular_user,
        mode="posts",
        include_replies=True,
    )
    assert reply.id in {a.id for a in activities}


@pytest.mark.asyncio
async def test_list_user_activities_posts_exclude_boosts(db_session, regular_user, other_user):
    """``include_boosts=False`` removes announces from posts mode."""
    track = await _make_track(db_session, other_user)
    target = _make_activity("track", track.id, owner_user_id=other_user.id)
    own = _make_activity(
        "user",
        regular_user.id,
        owner_user_id=regular_user.id,
        source_id="https://local.example/users/regular/objects/own-1",
    )
    db_session.add_all([target, own])
    await db_session.flush()

    await boost_activity(db_session, activity=target, author=regular_user)

    activities, _ = await activity_service.list_user_activities(
        db_session,
        owner_user_id=str(regular_user.id),
        user=regular_user,
        mode="posts",
        include_boosts=False,
    )
    assert {a.activity_type for a in activities} == {"create"}


@pytest.mark.asyncio
async def test_list_user_activities_all_ignores_include_flags(db_session, regular_user, other_user, config):
    """``mode="all"`` returns every authored activity regardless of flags."""
    track = await _make_track(db_session, other_user)
    target = _make_activity("track", track.id, owner_user_id=other_user.id)
    own = _make_activity(
        "user",
        regular_user.id,
        owner_user_id=regular_user.id,
        source_id="https://local.example/users/regular/objects/own-1",
    )
    db_session.add_all([target, own])
    await db_session.flush()

    await boost_activity(db_session, activity=target, author=regular_user)
    await like_activity(db_session, activity=target, author=regular_user)
    await reply_to_activity(db_session, activity=target, author=regular_user, config=config, status_text="nice")

    activities, _ = await activity_service.list_user_activities(
        db_session,
        owner_user_id=str(regular_user.id),
        user=regular_user,
        mode="all",
        include_boosts=False,
        include_replies=False,
    )
    types = {a.activity_type for a in activities}
    assert {"create", "announce", "like", "reply"} <= types


@pytest.mark.asyncio
async def test_user_activities_endpoint_posts_filters(client, db_session, regular_user, other_user, auth_headers):
    """The route forwards the include flags in posts mode."""
    track = await _make_track(db_session, other_user)
    target = _make_activity("track", track.id, owner_user_id=other_user.id)
    own = _make_activity(
        "user",
        regular_user.id,
        owner_user_id=regular_user.id,
        source_id="https://local.example/users/regular/objects/own-1",
    )
    db_session.add_all([target, own])
    await db_session.flush()

    await boost_activity(db_session, activity=target, author=regular_user)
    await db_session.commit()

    url = f"/api/v1/users/{regular_user.username}/activities"

    resp = client.get(url, headers=auth_headers(regular_user))
    assert resp.status_code == 200
    assert {a["activity_type"] for a in resp.json()["activities"]} == {"create", "announce"}

    resp = client.get(url, params={"include_boosts": "false"}, headers=auth_headers(regular_user))
    assert {a["activity_type"] for a in resp.json()["activities"]} == {"create"}

    resp = client.get(url, params={"include_boosts": "false", "mode": "all"}, headers=auth_headers(regular_user))
    assert "announce" in {a["activity_type"] for a in resp.json()["activities"]}


# ---------------------------------------------------------------------------
# create_announce_activity payload builder
# ---------------------------------------------------------------------------


def test_create_announce_activity_public_audience():
    """A public Announce addresses the public collection, followers in cc."""
    payload = create_announce_activity(
        "https://local.example/users/alice",
        "https://remote.example/objects/1",
        Visibility.PUBLIC,
    )
    assert payload["type"] == "Announce"
    assert payload["actor"] == "https://local.example/users/alice"
    assert payload["object"] == "https://remote.example/objects/1"
    assert payload["to"] == [AS_PUBLIC]
    assert payload["cc"] == ["https://local.example/users/alice/followers"]


def test_create_announce_activity_mentioned_audience():
    """A mentioned Announce addresses the listed actors only."""
    payload = create_announce_activity(
        "https://local.example/users/alice",
        "https://remote.example/objects/1",
        Visibility.MENTIONED,
        mention_actor_urls=["https://remote.example/users/bob"],
    )
    assert payload["to"] == ["https://remote.example/users/bob"]
    assert payload["cc"] == []


def test_create_announce_activity_reuses_activity_id():
    """An explicit activity_id becomes the payload's ActivityPub id."""
    payload = create_announce_activity(
        "https://local.example/users/alice",
        "https://remote.example/objects/1",
        Visibility.PUBLIC,
        activity_id="https://local.example/users/alice/objects/abc",
    )
    assert payload["id"] == "https://local.example/users/alice/objects/abc"


# ---------------------------------------------------------------------------
# boost_activity service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_boost_activity_creates_announce(db_session, regular_user, other_user):
    """Boosting records an announce activity on the same entity, inheriting visibility."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    boost = await boost_activity(db_session, activity=activity, author=regular_user)

    assert boost.activity_type == "announce"
    assert boost.entity_type == "track"
    assert boost.entity_id == str(track.id)
    assert boost.in_reply_to_activity_id == str(activity.id)
    assert boost.owner_user_id == regular_user.id
    assert boost.visibility == activity.visibility
    assert boost.payload["type"] == "Announce"
    assert boost.payload["actor"] == f"urn:songhive:user:{regular_user.username}"
    assert boost.payload["object"] == activity.source_id
    assert boost.payload["id"] == boost.source_id
    assert boost.payload["to"] == [AS_PUBLIC]


@pytest.mark.asyncio
async def test_boost_activity_idempotent(db_session, regular_user, other_user):
    """Boosting the same activity twice raises a 400."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    await boost_activity(db_session, activity=activity, author=regular_user)
    with pytest.raises(HTTPException) as excinfo:
        await boost_activity(db_session, activity=activity, author=regular_user)
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_boost_activity_retracted_target(db_session, regular_user):
    """A retracted activity cannot be boosted."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()
    activity.deleted_at = datetime.now(timezone.utc)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await boost_activity(db_session, activity=activity, author=regular_user)
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_boost_activity_notifies_local_owner(db_session, regular_user, other_user):
    """Boosting another local user's activity creates a boost notification."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    boost = await boost_activity(db_session, activity=activity, author=regular_user)

    notifications = await _notifications_for(db_session, other_user.id)
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.type == "boost"
    assert notification.source_url == activity.source_id
    assert notification.payload["activity_id"] == boost.source_id
    assert notification.payload["item_type"] == "track"


@pytest.mark.asyncio
async def test_boost_activity_self_boost_no_notification(db_session, regular_user):
    """Boosting one's own activity does not notify."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    await boost_activity(db_session, activity=activity, author=regular_user)

    assert await _notifications_for(db_session, regular_user.id) == []


# ---------------------------------------------------------------------------
# reply_to_activity service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reply_to_activity_creates_reply(db_session, regular_user, other_user, config):
    """Replying records a reply activity whose object carries inReplyTo."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    reply = await reply_to_activity(
        db_session,
        activity=activity,
        author=regular_user,
        config=config,
        status_text="nice track!",
    )

    assert reply.activity_type == "reply"
    assert reply.entity_type == "track"
    assert reply.entity_id == str(track.id)
    assert reply.in_reply_to_activity_id == str(activity.id)
    assert reply.owner_user_id == regular_user.id
    assert reply.visibility == activity.visibility
    assert reply.content and "nice track" in reply.content
    assert reply.payload["type"] == "Create"
    assert reply.payload["object"]["type"] == "Note"
    assert reply.payload["object"]["inReplyTo"] == activity.source_id

    # The replied-to owner is mentioned so mentioned-visibility replies stay
    # viewable to them.
    mention_rows = await db_session.execute(select(ActivityMention).where(ActivityMention.activity_id == reply.id))
    mentions = mention_rows.scalars().all()
    assert any(m.user_id == other_user.id for m in mentions)


@pytest.mark.asyncio
async def test_reply_to_activity_notifies_owner(db_session, regular_user, other_user, config):
    """Replying to another local user's activity creates a reply notification."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    reply = await reply_to_activity(
        db_session,
        activity=activity,
        author=regular_user,
        config=config,
        status_text="cool!",
    )

    notifications = await _notifications_for(db_session, other_user.id)
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.type == "reply"
    assert notification.source_url == reply.source_id
    assert notification.payload["activity_id"] == reply.source_id
    assert notification.payload["target_url"] == activity.source_id


@pytest.mark.asyncio
async def test_reply_to_activity_self_reply_no_notification(db_session, regular_user, config):
    """Replying to one's own activity does not notify."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    await reply_to_activity(
        db_session,
        activity=activity,
        author=regular_user,
        config=config,
        status_text="note to self",
    )

    assert await _notifications_for(db_session, regular_user.id) == []


@pytest.mark.asyncio
async def test_reply_to_activity_empty_422(db_session, regular_user, config):
    """An empty reply is rejected."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await reply_to_activity(
            db_session,
            activity=activity,
            author=regular_user,
            config=config,
            status_text="   ",
        )
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_reply_to_activity_visibility_capped(db_session, regular_user, other_user, config):
    """A reply may not exceed the replied-to activity's visibility."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.MENTIONED.value,
    )
    activity.mentions.append(ActivityMention(handle="@regular", user_id=regular_user.id))
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await reply_to_activity(
            db_session,
            activity=activity,
            author=regular_user,
            config=config,
            status_text="public reply to a mentioned post",
            visibility="public",
        )
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_reply_to_activity_retracted_target(db_session, regular_user, config):
    """A retracted activity cannot be replied to."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()
    activity.deleted_at = datetime.now(timezone.utc)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await reply_to_activity(
            db_session,
            activity=activity,
            author=regular_user,
            config=config,
            status_text="too late",
        )
    assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# resolve_interaction_summaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summaries_count_local_interactions(db_session, regular_user, other_user, config):
    """Local likes, boosts and replies are counted and viewer state is set."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    await like_activity(db_session, activity=activity, author=regular_user)
    await boost_activity(db_session, activity=activity, author=regular_user)
    await reply_to_activity(
        db_session,
        activity=activity,
        author=other_user,
        config=config,
        status_text="thanks!",
    )

    summaries = await resolve_interaction_summaries(db_session, [activity], regular_user, config)

    summary = summaries[str(activity.id)]
    assert summary.like_count == 1
    assert summary.boost_count == 1
    assert summary.reply_count == 1
    assert summary.liked is True
    assert summary.boosted is True

    # A different viewer sees the counts but not the flags.
    other = await resolve_interaction_summaries(db_session, [activity], other_user, config)
    assert other[str(activity.id)].liked is False
    assert other[str(activity.id)].boosted is False
    assert other[str(activity.id)].reply_count == 1


@pytest.mark.asyncio
async def test_summaries_count_remote_interactions(db_session, regular_user, config, monkeypatch):
    """Confirmed remote interactions from pubby storage add to the counts."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    remote_like = Interaction(
        source_actor_id="https://remote.example/users/bob",
        target_resource=activity.source_id,
        interaction_type=InteractionType.LIKE,
        activity_id="https://remote.example/activities/1",
        author_name="Bob",
        published=datetime.now(timezone.utc),
    )
    remote_reply = Interaction(
        source_actor_id="https://remote.example/users/carol",
        target_resource=activity.source_id,
        interaction_type=InteractionType.REPLY,
        activity_id="https://remote.example/activities/2",
        object_id="https://remote.example/objects/r1",
        content="<p>remote</p>",
        published=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        activity_service,
        "_fetch_remote_interactions",
        lambda cfg, ids, interaction_type=None: {
            sid: [
                i
                for i in (remote_like, remote_reply)
                if i.target_resource == sid and (interaction_type is None or i.interaction_type == interaction_type)
            ]
            for sid in ids
        },
    )

    summaries = await resolve_interaction_summaries(db_session, [activity], regular_user, config)

    summary = summaries[str(activity.id)]
    assert summary.like_count == 1
    assert summary.reply_count == 1
    assert summary.boost_count == 0


@pytest.mark.asyncio
async def test_summaries_remote_failure_is_ignored(db_session, regular_user, config, monkeypatch):
    """A failing remote interaction lookup falls back to local counts only."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()
    await like_activity(db_session, activity=activity, author=regular_user)

    def _boom(cfg, ids, interaction_type=None):
        raise RuntimeError("storage down")

    monkeypatch.setattr(activity_service, "_fetch_remote_interactions", _boom)

    summaries = await resolve_interaction_summaries(db_session, [activity], regular_user, config)
    assert summaries[str(activity.id)].like_count == 1


# ---------------------------------------------------------------------------
# list_activity_interactors / list_activity_replies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_interactors_returns_local_actor(db_session, regular_user, other_user, config):
    """Local likers resolve to their username and profile."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()
    await like_activity(db_session, activity=activity, author=regular_user)

    actors = await list_activity_interactors(db_session, activity=activity, interaction_type="like", config=config)

    assert len(actors) == 1
    assert actors[0].username == regular_user.username
    assert actors[0].actor == f"urn:songhive:user:{regular_user.username}"

    # The boost listing is separate.
    boosters = await list_activity_interactors(
        db_session, activity=activity, interaction_type="announce", config=config
    )
    assert boosters == []


@pytest.mark.asyncio
async def test_list_interactors_includes_remote_actors(db_session, regular_user, config, monkeypatch):
    """Confirmed remote likes surface with the actor identity they exposed."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    remote_like = Interaction(
        source_actor_id="https://remote.example/users/bob",
        target_resource=activity.source_id,
        interaction_type=InteractionType.LIKE,
        activity_id="https://remote.example/activities/1",
        author_name="Bob Remote",
        author_url="https://remote.example/@bob",
        author_photo="https://remote.example/avatars/bob.png",
        published=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        activity_service,
        "_fetch_remote_interactions",
        lambda cfg, ids, interaction_type=None: {sid: [remote_like] for sid in ids},
    )

    actors = await list_activity_interactors(db_session, activity=activity, interaction_type="like", config=config)

    assert len(actors) == 1
    assert actors[0].actor == "https://remote.example/users/bob"
    assert actors[0].display_name == "Bob Remote"
    assert actors[0].profile_url == "https://remote.example/@bob"
    assert actors[0].avatar_url == "https://remote.example/avatars/bob.png"


@pytest.mark.asyncio
async def test_list_interactors_invalid_type(db_session, regular_user, config):
    """Only like and announce are valid interactor listings."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await list_activity_interactors(db_session, activity=activity, interaction_type="reply", config=config)
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_list_replies_returns_local_replies(db_session, regular_user, other_user, config):
    """Local replies come back oldest-first, visibility-filtered."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    reply = await reply_to_activity(
        db_session,
        activity=activity,
        author=regular_user,
        config=config,
        status_text="first!",
    )

    local, remote = await list_activity_replies(db_session, activity=activity, user=other_user, config=config)
    assert [r.id for r in local] == [reply.id]
    assert remote == []

    # A retracted reply drops out of the listing.
    await activity_service.retract_activity(db_session, reply)
    local, _ = await list_activity_replies(db_session, activity=activity, user=other_user, config=config)
    assert local == []


@pytest.mark.asyncio
async def test_list_replies_returns_thread_descendants(db_session, regular_user, other_user, config):
    """Replies to replies are part of the root's thread listing."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    reply = await reply_to_activity(
        db_session, activity=activity, author=regular_user, config=config, status_text="first!"
    )
    nested = await reply_to_activity(db_session, activity=reply, author=other_user, config=config, status_text="nested")
    sibling = await reply_to_activity(
        db_session, activity=activity, author=regular_user, config=config, status_text="second!"
    )

    local, remote = await list_activity_replies(db_session, activity=activity, user=other_user, config=config)
    assert {r.id for r in local} == {reply.id, nested.id, sibling.id}
    assert remote == []

    # A reply's own listing returns just its sub-thread.
    local, _ = await list_activity_replies(db_session, activity=reply, user=other_user, config=config)
    assert [r.id for r in local] == [nested.id]


@pytest.mark.asyncio
async def test_list_replies_includes_remote_thread_replies(db_session, regular_user, config, monkeypatch):
    """Remote replies to any thread node surface, including remote-to-remote."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    reply = await reply_to_activity(
        db_session, activity=activity, author=regular_user, config=config, status_text="local reply"
    )

    direct = Interaction(
        source_actor_id="https://remote.example/users/bob",
        target_resource=activity.source_id,
        interaction_type=InteractionType.REPLY,
        object_id="https://remote.example/objects/r1",
        content="<p>on the root</p>",
    )
    on_local_reply = Interaction(
        source_actor_id="https://remote.example/users/carol",
        target_resource=reply.source_id,
        interaction_type=InteractionType.REPLY,
        object_id="https://remote.example/objects/r2",
        content="<p>on the reply</p>",
    )
    on_remote_reply = Interaction(
        source_actor_id="https://remote.example/users/dan",
        target_resource="https://remote.example/objects/r2",
        interaction_type=InteractionType.REPLY,
        object_id="https://remote.example/objects/r3",
        content="<p>on the remote reply</p>",
    )
    interactions = (direct, on_local_reply, on_remote_reply)
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

    local, remote = await list_activity_replies(db_session, activity=activity, user=regular_user, config=config)
    assert [r.id for r in local] == [reply.id]
    assert [i.object_id for i in remote] == [
        "https://remote.example/objects/r1",
        "https://remote.example/objects/r2",
        "https://remote.example/objects/r3",
    ]
    assert [i.target_resource for i in remote] == [
        activity.source_id,
        reply.source_id,
        "https://remote.example/objects/r2",
    ]


@pytest.mark.asyncio
async def test_summaries_count_nested_replies(db_session, regular_user, other_user, config):
    """``reply_count`` covers the whole sub-thread, not just direct replies."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    reply_b = await reply_to_activity(
        db_session, activity=activity, author=regular_user, config=config, status_text="b"
    )
    reply_c = await reply_to_activity(db_session, activity=reply_b, author=other_user, config=config, status_text="c")
    reply_d = await reply_to_activity(
        db_session, activity=activity, author=regular_user, config=config, status_text="d"
    )

    summaries = await resolve_interaction_summaries(
        db_session, [activity, reply_b, reply_c, reply_d], regular_user, config
    )
    assert summaries[str(activity.id)].reply_count == 3
    assert summaries[str(reply_b.id)].reply_count == 1
    assert summaries[str(reply_c.id)].reply_count == 0
    assert summaries[str(reply_d.id)].reply_count == 0


@pytest.mark.asyncio
async def test_summaries_count_remote_thread_replies(db_session, regular_user, config, monkeypatch):
    """Remote replies anywhere in the sub-thread count toward every ancestor."""
    config = _fed_config(config)
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    reply = await reply_to_activity(
        db_session, activity=activity, author=regular_user, config=config, status_text="local reply"
    )

    direct = Interaction(
        source_actor_id="https://remote.example/users/bob",
        target_resource=activity.source_id,
        interaction_type=InteractionType.REPLY,
        object_id="https://remote.example/objects/r1",
    )
    on_local_reply = Interaction(
        source_actor_id="https://remote.example/users/carol",
        target_resource=reply.source_id,
        interaction_type=InteractionType.REPLY,
        object_id="https://remote.example/objects/r2",
    )
    on_remote_reply = Interaction(
        source_actor_id="https://remote.example/users/dan",
        target_resource="https://remote.example/objects/r2",
        interaction_type=InteractionType.REPLY,
        object_id="https://remote.example/objects/r3",
    )
    interactions = (direct, on_local_reply, on_remote_reply)
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

    summaries = await resolve_interaction_summaries(db_session, [activity, reply], regular_user, config)
    # All three remote replies plus the local reply belong to the root thread.
    assert summaries[str(activity.id)].reply_count == 4
    # The local reply's own sub-thread holds the two chained remote replies.
    assert summaries[str(reply.id)].reply_count == 2


# ---------------------------------------------------------------------------
# POST /api/v1/activities/{activity_id}/boost
# ---------------------------------------------------------------------------


def test_boost_endpoint_requires_auth(client):
    """Unauthenticated requests are rejected."""
    resp = client.post("/api/v1/activities/whatever/boost")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_boost_endpoint_not_found(client, regular_user, auth_headers):
    """A missing activity returns 404."""
    resp = client.post("/api/v1/activities/missing-id/boost", headers=auth_headers(regular_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_boost_endpoint_forbidden(client, db_session, regular_user, other_user, auth_headers):
    """Activities the user cannot view return 403."""
    track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/boost", headers=auth_headers(regular_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_boost_endpoint_creates_and_is_idempotent(client, db_session, regular_user, other_user, auth_headers):
    """A successful boost returns 201 and the second boost returns 400."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/boost", headers=auth_headers(regular_user))
    assert resp.status_code == 201
    boost_id = resp.json()["activity_id"]

    boost = await db_session.get(Activity, boost_id)
    assert boost is not None
    assert boost.activity_type == "announce"
    assert boost.in_reply_to_activity_id == str(activity.id)

    resp = client.post(f"/api/v1/activities/{activity.id}/boost", headers=auth_headers(regular_user))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_boost_endpoint_fans_out_when_federated(client, db_session, regular_user, auth_headers, monkeypatch):
    """With federation enabled the boost is fanned out to its audience."""
    client.app.state.config.federation.enabled = True
    client.app.state.config.federation.instance_domain = "local.example"
    fan_out = AsyncMock(return_value=0)
    monkeypatch.setattr(activity_service, "fan_out_boost_activity", fan_out)

    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/boost", headers=auth_headers(regular_user))

    assert resp.status_code == 201
    fan_out.assert_awaited_once()
    kwargs = fan_out.call_args.kwargs
    assert kwargs["target"].id == activity.id
    assert kwargs["boost"].activity_type == "announce"


# ---------------------------------------------------------------------------
# POST /api/v1/activities/{activity_id}/reply
# ---------------------------------------------------------------------------


def test_reply_endpoint_requires_auth(client):
    """Unauthenticated requests are rejected."""
    resp = client.post("/api/v1/activities/whatever/reply", json={"status": "hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reply_endpoint_creates_reply(client, db_session, regular_user, other_user, auth_headers):
    """A reply returns the new reply activity serialized as a card."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(
        f"/api/v1/activities/{activity.id}/reply",
        headers=auth_headers(regular_user),
        json={"status": "nice!"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["activity_type"] == "reply"
    assert body["in_reply_to_activity_id"] == str(activity.id)
    assert "nice!" in body["content"]
    assert body["like_count"] == 0
    assert body["reply_count"] == 0


@pytest.mark.asyncio
async def test_reply_endpoint_forbidden(client, db_session, regular_user, other_user, auth_headers):
    """Activities the user cannot view return 403."""
    track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(
        f"/api/v1/activities/{activity.id}/reply",
        headers=auth_headers(regular_user),
        json={"status": "hi"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /{id}/likes, /{id}/boosts, /{id}/replies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_likes_endpoint_lists_likers(client, db_session, regular_user, other_user, auth_headers):
    """The likes listing returns the known liking accounts."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 201

    resp = client.get(f"/api/v1/activities/{activity.id}/likes")
    assert resp.status_code == 200
    actors = resp.json()["actors"]
    assert len(actors) == 1
    assert actors[0]["handle"] == f"@{regular_user.username}"
    assert actors[0]["username"] == regular_user.username


@pytest.mark.asyncio
async def test_boosts_endpoint_lists_boosters(client, db_session, regular_user, other_user, auth_headers):
    """The boosts listing returns the known boosting accounts."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/boost", headers=auth_headers(regular_user))
    assert resp.status_code == 201

    resp = client.get(f"/api/v1/activities/{activity.id}/boosts")
    assert resp.status_code == 200
    actors = resp.json()["actors"]
    assert len(actors) == 1
    assert actors[0]["username"] == regular_user.username


@pytest.mark.asyncio
async def test_likes_endpoint_forbidden(client, db_session, regular_user, other_user, auth_headers):
    """Activities the user cannot view return 403 for the likes listing."""
    track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(activity)
    await db_session.flush()

    resp = client.get(f"/api/v1/activities/{activity.id}/likes", headers=auth_headers(regular_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_replies_endpoint_lists_replies(client, db_session, regular_user, other_user, auth_headers):
    """The replies listing returns local replies as activity cards."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(
        f"/api/v1/activities/{activity.id}/reply",
        headers=auth_headers(regular_user),
        json={"status": "a reply"},
    )
    assert resp.status_code == 201

    resp = client.get(f"/api/v1/activities/{activity.id}/replies")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["activities"]) == 1
    assert body["activities"][0]["activity_type"] == "reply"
    assert "a reply" in body["activities"][0]["content"]
    assert body["remote_replies"] == []


@pytest.mark.asyncio
async def test_replies_endpoint_lists_thread_descendants(client, db_session, regular_user, other_user, auth_headers):
    """The replies listing returns nested replies and transitive counts."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(
        f"/api/v1/activities/{activity.id}/reply",
        headers=auth_headers(regular_user),
        json={"status": "a reply"},
    )
    assert resp.status_code == 201
    reply_id = resp.json()["id"]

    resp = client.post(
        f"/api/v1/activities/{reply_id}/reply",
        headers=auth_headers(other_user),
        json={"status": "nested reply"},
    )
    assert resp.status_code == 201
    nested_id = resp.json()["id"]

    resp = client.get(f"/api/v1/activities/{activity.id}/replies")
    assert resp.status_code == 200
    body = resp.json()
    by_id = {a["id"]: a for a in body["activities"]}
    assert set(by_id) == {reply_id, nested_id}
    assert by_id[reply_id]["in_reply_to_activity_id"] == str(activity.id)
    assert by_id[nested_id]["in_reply_to_activity_id"] == reply_id

    # The root card and the intermediate reply report transitive counts.
    resp = client.get(f"/api/v1/activities/{activity.id}")
    assert resp.json()["reply_count"] == 2
    resp = client.get(f"/api/v1/activities/{reply_id}")
    assert resp.json()["reply_count"] == 1


@pytest.mark.asyncio
async def test_activity_list_includes_interaction_counts(client, db_session, regular_user, other_user, auth_headers):
    """Entity activity lists carry counts and the requester's own state."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 201

    resp = client.get(
        f"/api/v1/track/{track.id}/activities",
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 200
    cards = [a for a in resp.json()["activities"] if a["id"] == str(activity.id)]
    assert len(cards) == 1
    assert cards[0]["like_count"] == 1
    assert cards[0]["liked"] is True
    assert cards[0]["can_interact"] is True


# ---------------------------------------------------------------------------
# unreact_activity (unlike / unboost)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unreact_activity_retracts_like(db_session, regular_user, other_user, config):
    """Unliking soft-deletes the like and drops it from the summaries."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)
    reaction = await unreact_activity(db_session, activity=activity, author=regular_user, interaction_type="like")

    assert reaction.id == like.id
    assert reaction.deleted_at is not None

    summary = (await resolve_interaction_summaries(db_session, [activity], regular_user, config))[str(activity.id)]
    assert summary.like_count == 0
    assert summary.liked is False


@pytest.mark.asyncio
async def test_unreact_activity_retracts_boost(db_session, regular_user, other_user, config):
    """Unboosting soft-deletes the announce and drops it from the summaries."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    boost = await boost_activity(db_session, activity=activity, author=regular_user)
    reaction = await unreact_activity(db_session, activity=activity, author=regular_user, interaction_type="announce")

    assert reaction.id == boost.id
    assert reaction.deleted_at is not None

    summary = (await resolve_interaction_summaries(db_session, [activity], regular_user, config))[str(activity.id)]
    assert summary.boost_count == 0
    assert summary.boosted is False


@pytest.mark.asyncio
async def test_unreact_activity_removes_notification(db_session, regular_user, other_user):
    """Unliking retracts the notification the like produced on the owner."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    await like_activity(db_session, activity=activity, author=regular_user)
    assert await _notifications_for(db_session, other_user.id) != []

    await unreact_activity(db_session, activity=activity, author=regular_user, interaction_type="like")
    assert await _notifications_for(db_session, other_user.id) == []


@pytest.mark.asyncio
async def test_unreact_activity_allows_reacting_again(db_session, regular_user, other_user):
    """A retracted like does not block a fresh like of the same activity."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    first = await like_activity(db_session, activity=activity, author=regular_user)
    await unreact_activity(db_session, activity=activity, author=regular_user, interaction_type="like")
    second = await like_activity(db_session, activity=activity, author=regular_user)

    assert second.id != first.id
    assert second.deleted_at is None


@pytest.mark.asyncio
async def test_unreact_activity_missing_reaction_404(db_session, regular_user, other_user):
    """Unliking an activity that was never liked returns 404."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await unreact_activity(db_session, activity=activity, author=regular_user, interaction_type="like")
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_unreact_activity_other_users_reaction_untouched(db_session, regular_user, other_user):
    """A user cannot retract another user's like."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=other_user)
    with pytest.raises(HTTPException) as excinfo:
        await unreact_activity(db_session, activity=activity, author=regular_user, interaction_type="like")
    assert excinfo.value.status_code == 404
    assert like.deleted_at is None


@pytest.mark.asyncio
async def test_unreact_activity_retracted_target_404(db_session, regular_user):
    """Unliking a retracted activity returns 404."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    activity.deleted_at = datetime.now(timezone.utc)
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await unreact_activity(db_session, activity=activity, author=regular_user, interaction_type="like")
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_unreact_activity_invalid_type_400(db_session, regular_user):
    """Only like/announce reactions can be retracted through this path."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await unreact_activity(db_session, activity=activity, author=regular_user, interaction_type="reply")
    assert excinfo.value.status_code == 400


# ---------------------------------------------------------------------------
# create_undo_activity payload builder
# ---------------------------------------------------------------------------


def test_create_undo_activity_wraps_reaction():
    """The Undo embeds the original Like and inherits its audience."""
    like = create_like_activity(
        "https://local.example/users/alice",
        "https://remote.example/objects/1",
        Visibility.PUBLIC,
    )
    undo = create_undo_activity(
        "https://local.example/users/alice", like, activity_id="https://local.example/users/alice/objects/1#undo"
    )
    assert undo["type"] == "Undo"
    assert undo["actor"] == "https://local.example/users/alice"
    assert undo["object"]["type"] == "Like"
    assert undo["object"]["id"] == like["id"]
    assert undo["to"] == like["to"]
    assert undo["cc"] == like["cc"]
    assert undo["id"] == "https://local.example/users/alice/objects/1#undo"


# ---------------------------------------------------------------------------
# fan_out_unreaction_activity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_out_unreaction_enqueues_undo(db_session, regular_user, other_user, config, monkeypatch):
    """The Undo is delivered to the inboxes the reaction reached."""
    config = _fed_config(config)
    regular_user.private_key_pem = "private-key"
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)
    db_session.add(ActivityTarget(activity_id=like.id, inbox_url="https://remote.example/inbox", state="sent"))
    db_session.add(ActivityTarget(activity_id=like.id, inbox_url="https://other.example/inbox", state="failed"))
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await unreact_activity(db_session, activity=activity, author=regular_user, interaction_type="like")
    await fan_out_unreaction_activity(db_session, reaction=like, author=regular_user, config=config)

    assert deliver.delay.call_count == 1
    payload, inbox, key_id, key = deliver.delay.call_args.args
    assert payload["type"] == "Undo"
    assert payload["actor"] == like.source_actor
    assert payload["object"]["type"] == "Like"
    assert payload["object"]["object"] == activity.source_id
    assert inbox == "https://remote.example/inbox"
    assert key_id == f"{like.source_actor}#main-key"
    assert key == "private-key"


@pytest.mark.asyncio
async def test_fan_out_unreaction_no_inboxes_noop(db_session, regular_user, other_user, config, monkeypatch):
    """A reaction that was never delivered produces no Undo deliveries."""
    config = _fed_config(config)
    regular_user.private_key_pem = "private-key"
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await fan_out_unreaction_activity(db_session, reaction=like, author=regular_user, config=config)
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_fan_out_unreaction_federation_disabled(db_session, regular_user, other_user, config, monkeypatch):
    """No deliveries are enqueued when federation is disabled."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    like = await like_activity(db_session, activity=activity, author=regular_user)
    db_session.add(ActivityTarget(activity_id=like.id, inbox_url="https://remote.example/inbox", state="sent"))
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await fan_out_unreaction_activity(db_session, reaction=like, author=regular_user, config=config)
    deliver.delay.assert_not_called()


# ---------------------------------------------------------------------------
# DELETE /api/v1/activities/{activity_id}/like + /boost
# ---------------------------------------------------------------------------


def test_unlike_endpoint_requires_auth(client):
    """Unauthenticated requests are rejected."""
    resp = client.delete("/api/v1/activities/whatever/like")
    assert resp.status_code == 401


def test_unboost_endpoint_requires_auth(client):
    """Unauthenticated requests are rejected."""
    resp = client.delete("/api/v1/activities/whatever/boost")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unlike_endpoint_retracts_like(client, db_session, regular_user, other_user, auth_headers):
    """DELETE /like removes the caller's like and reports the updated state."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 201
    like_id = resp.json()["activity_id"]

    resp = client.delete(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    like = await db_session.get(Activity, like_id)
    assert like.deleted_at is not None

    # The like listing no longer includes the retracted like, and the
    # activity can be liked again.
    resp = client.get(f"/api/v1/activities/{activity.id}/likes")
    assert resp.json()["actors"] == []
    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_unboost_endpoint_retracts_boost(client, db_session, regular_user, other_user, auth_headers):
    """DELETE /boost removes the caller's boost."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/boost", headers=auth_headers(regular_user))
    assert resp.status_code == 201
    boost_id = resp.json()["activity_id"]

    resp = client.delete(f"/api/v1/activities/{activity.id}/boost", headers=auth_headers(regular_user))
    assert resp.status_code == 200

    boost = await db_session.get(Activity, boost_id)
    assert boost.deleted_at is not None


@pytest.mark.asyncio
async def test_unlike_endpoint_not_liked_404(client, db_session, regular_user, other_user, auth_headers):
    """Unliking an activity that was not liked returns 404."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.delete(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unlike_endpoint_forbidden(client, db_session, regular_user, other_user, auth_headers):
    """Activities the user cannot view return 403."""
    track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(activity)
    await db_session.flush()

    resp = client.delete(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unlike_endpoint_fans_out_undo_when_federated(
    client, db_session, regular_user, auth_headers, monkeypatch
):
    """With federation enabled the retraction fans out an Undo."""
    client.app.state.config.federation.enabled = True
    client.app.state.config.federation.instance_domain = "local.example"
    fan_out = AsyncMock(return_value=None)
    monkeypatch.setattr(activity_service, "fan_out_unreaction_activity", fan_out)

    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.post(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 201

    resp = client.delete(f"/api/v1/activities/{activity.id}/like", headers=auth_headers(regular_user))
    assert resp.status_code == 200
    fan_out.assert_awaited_once()
    kwargs = fan_out.call_args.kwargs
    assert kwargs["reaction"].activity_type == "like"
    assert kwargs["reaction"].deleted_at is not None
    assert kwargs["author"].id == regular_user.id
