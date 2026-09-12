"""
Activity interaction tests - the like service and endpoint, activity view
rules, ``Like`` payload building, remote inbox resolution, and fan-out.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests
from fastapi import HTTPException
from sqlalchemy import select

from songhive.federation.activities import AS_PUBLIC, create_like_activity
from songhive.models._enums import Visibility
from songhive.models.activity import Activity, ActivityMention
from songhive.models.artist import Artist
from songhive.models.notification import Notification
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import activities as activity_service
from songhive.services import federation as federation_service
from songhive.services.activities import can_view_activity, like_activity


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
    """A private activity is limited to its owner and admins."""
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
    assert await can_view_activity(db_session, admin_user, activity)
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
