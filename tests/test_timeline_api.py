"""
Cross-entity timeline tests - ``GET /api/v1/timeline``.

Covers the ``instance``/``federated``/``mine`` scopes, anonymous defaults,
mode and include flags, visibility filtering, and keyset pagination.
"""

import uuid
from datetime import datetime, timezone

import pytest

from songhive.models import Visibility
from songhive.models.activity import Activity
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User


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
    object_id = uuid.uuid4().hex[:12]
    params = {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "activity_type": "create",
        "source_type": "local",
        "source_actor": "https://local.example/users/alice",
        "source_id": f"https://local.example/users/alice/objects/{object_id}",
        "local_object_id": object_id,
        "visibility": Visibility.PUBLIC.value,
    }
    params.update(overrides)
    return Activity(**params)


@pytest.mark.asyncio
async def test_timeline_anonymous_defaults_to_instance_scope(client, db_session, other_user):
    """Anonymous requesters get the public instance feed without a scope."""
    track = await _make_track(db_session, other_user)
    public = _make_activity("track", track.id, owner_user_id=other_user.id)
    local = _make_activity("track", track.id, owner_user_id=other_user.id, visibility=Visibility.LOCAL.value)
    private = _make_activity("track", track.id, owner_user_id=other_user.id, visibility=Visibility.PRIVATE.value)
    db_session.add_all([public, local, private])
    await db_session.flush()

    resp = client.get("/api/v1/timeline")

    assert resp.status_code == 200
    body = resp.json()
    assert [a["id"] for a in body["activities"]] == [public.id]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_timeline_anonymous_mine_scope_unauthorized(client):
    """scope=mine requires authentication."""
    resp = client.get("/api/v1/timeline", params={"scope": "mine"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_timeline_authenticated_default_is_mine(client, db_session, regular_user, other_user, auth_headers):
    """Authenticated requesters default to their own activity."""
    track = await _make_track(db_session, other_user)
    mine = _make_activity("track", track.id, owner_user_id=regular_user.id)
    theirs = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add_all([mine, theirs])
    await db_session.flush()

    resp = client.get("/api/v1/timeline", headers=auth_headers(regular_user))

    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()["activities"]] == [mine.id]


@pytest.mark.asyncio
async def test_timeline_mine_scope_includes_own_visibilities(
    client, db_session, regular_user, other_user, auth_headers
):
    """scope=mine shows the caller's activities at every visibility."""
    track = await _make_track(db_session, other_user)
    public = _make_activity("track", track.id, owner_user_id=regular_user.id)
    private = _make_activity(
        "track",
        track.id,
        owner_user_id=regular_user.id,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add_all([public, private])
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "mine"},
        headers=auth_headers(regular_user),
    )

    assert resp.status_code == 200
    assert {a["id"] for a in resp.json()["activities"]} == {public.id, private.id}


@pytest.mark.asyncio
async def test_timeline_instance_scope_for_authenticated(client, db_session, regular_user, other_user, auth_headers):
    """Authenticated users see public, local and followers posts on the instance feed."""
    track = await _make_track(db_session, other_user)
    by_visibility = {}
    for visibility in (
        Visibility.PUBLIC,
        Visibility.FOLLOWERS,
        Visibility.LOCAL,
        Visibility.MENTIONED,
        Visibility.PRIVATE,
    ):
        activity = _make_activity("track", track.id, owner_user_id=other_user.id, visibility=visibility.value)
        by_visibility[visibility] = activity
        db_session.add(activity)
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance"},
        headers=auth_headers(regular_user),
    )

    seen = {a["id"] for a in resp.json()["activities"]}
    assert seen == {
        by_visibility[Visibility.PUBLIC].id,
        by_visibility[Visibility.FOLLOWERS].id,
        by_visibility[Visibility.LOCAL].id,
    }


@pytest.mark.asyncio
async def test_timeline_instance_scope_spans_entities(client, db_session, regular_user, other_user, auth_headers):
    """The instance feed aggregates activities across entity types."""
    track = await _make_track(db_session, other_user)
    on_track = _make_activity("track", track.id, owner_user_id=other_user.id)
    on_user = _make_activity("user", other_user.id, owner_user_id=other_user.id)
    db_session.add_all([on_track, on_user])
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance"},
        headers=auth_headers(regular_user),
    )

    seen = {a["id"] for a in resp.json()["activities"]}
    assert seen == {on_track.id, on_user.id}


@pytest.mark.asyncio
async def test_timeline_instance_scope_hides_inaccessible_entities(
    client, db_session, regular_user, other_user, auth_headers
):
    """Activities on entities the requester cannot access are excluded."""
    public_track = await _make_track(db_session, other_user)
    private_track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)
    visible = _make_activity("track", public_track.id, owner_user_id=other_user.id)
    hidden = _make_activity("track", private_track.id, owner_user_id=other_user.id)
    db_session.add_all([visible, hidden])
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance"},
        headers=auth_headers(regular_user),
    )

    assert [a["id"] for a in resp.json()["activities"]] == [visible.id]

    # The private entity's owner sees both.
    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance"},
        headers=auth_headers(other_user),
    )
    assert {a["id"] for a in resp.json()["activities"]} == {visible.id, hidden.id}


@pytest.mark.asyncio
async def test_timeline_invalid_scope_and_mode(client):
    """Unknown scope and mode values are rejected with 400."""
    resp = client.get("/api/v1/timeline", params={"scope": "following"})
    assert resp.status_code == 400

    resp = client.get("/api/v1/timeline", params={"mode": "bogus"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_timeline_mode_all_includes_interactions(client, db_session, regular_user, other_user, auth_headers):
    """mode=all surfaces likes and replies that posts mode hides."""
    track = await _make_track(db_session, other_user)
    create = _make_activity("track", track.id, owner_user_id=other_user.id)
    like = _make_activity("track", track.id, owner_user_id=other_user.id, activity_type="like")
    reply = _make_activity("track", track.id, owner_user_id=other_user.id, activity_type="reply")
    db_session.add_all([create, like, reply])
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance", "mode": "all"},
        headers=auth_headers(regular_user),
    )
    assert {a["id"] for a in resp.json()["activities"]} == {create.id, like.id, reply.id}

    # posts mode keeps create + announce (default include_boosts) but not
    # likes; replies are opt-in via include_replies.
    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance", "mode": "posts"},
        headers=auth_headers(regular_user),
    )
    assert [a["id"] for a in resp.json()["activities"]] == [create.id]

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance", "mode": "posts", "include_replies": True},
        headers=auth_headers(regular_user),
    )
    assert {a["id"] for a in resp.json()["activities"]} == {create.id, reply.id}


@pytest.mark.asyncio
async def test_timeline_posts_mode_boosts_flag(client, db_session, regular_user, other_user, auth_headers):
    """include_boosts controls announce activities in posts mode."""
    track = await _make_track(db_session, other_user)
    create = _make_activity("track", track.id, owner_user_id=other_user.id)
    boost = _make_activity("track", track.id, owner_user_id=other_user.id, activity_type="announce")
    db_session.add_all([create, boost])
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance", "include_boosts": False},
        headers=auth_headers(regular_user),
    )
    assert [a["id"] for a in resp.json()["activities"]] == [create.id]


@pytest.mark.asyncio
async def test_timeline_instance_scope_excludes_remote_sources(
    client, db_session, regular_user, other_user, auth_headers
):
    """scope=instance only surfaces activities that originate locally."""
    track = await _make_track(db_session, other_user)
    local = _make_activity("track", track.id, owner_user_id=other_user.id)
    remote = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        source_type="remote",
        source_actor="https://mastodon.example/users/masto",
    )
    webmention = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        activity_type="like",
        source_type="webmention",
        source_actor="https://blog.example/post/1",
    )
    db_session.add_all([local, remote, webmention])
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance", "mode": "all"},
        headers=auth_headers(regular_user),
    )
    assert [a["id"] for a in resp.json()["activities"]] == [local.id]


@pytest.mark.asyncio
async def test_timeline_federated_scope_includes_remote_sources(
    client, db_session, regular_user, other_user, auth_headers
):
    """scope=federated surfaces local, remote and webmention activities."""
    track = await _make_track(db_session, other_user)
    local = _make_activity("track", track.id, owner_user_id=other_user.id)
    remote = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        source_type="remote",
        source_actor="https://mastodon.example/users/masto",
    )
    webmention = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        activity_type="like",
        source_type="webmention",
        source_actor="https://blog.example/post/1",
    )
    db_session.add_all([local, remote, webmention])
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "federated", "mode": "all"},
        headers=auth_headers(regular_user),
    )
    assert {a["id"] for a in resp.json()["activities"]} == {local.id, remote.id, webmention.id}


@pytest.mark.asyncio
async def test_timeline_federated_scope_anonymous(client, db_session, other_user):
    """Anonymous requesters see the public subset of the federated feed."""
    track = await _make_track(db_session, other_user)
    public_remote = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        source_type="remote",
        source_actor="https://mastodon.example/users/masto",
    )
    local_remote = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        source_type="remote",
        source_actor="https://mastodon.example/users/masto",
        visibility=Visibility.LOCAL.value,
    )
    db_session.add_all([public_remote, local_remote])
    await db_session.flush()

    resp = client.get("/api/v1/timeline", params={"scope": "federated"})

    assert resp.status_code == 200
    assert [a["id"] for a in resp.json()["activities"]] == [public_remote.id]


@pytest.mark.asyncio
async def test_timeline_source_type_filter(client, db_session, regular_user, other_user, auth_headers):
    """source_type narrows the feed on top of the scope's source filter."""
    track = await _make_track(db_session, other_user)
    local = _make_activity("track", track.id, owner_user_id=other_user.id)
    remote = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        source_type="remote",
        source_actor="https://mastodon.example/users/masto",
    )
    db_session.add_all([local, remote])
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "federated", "source_type": "remote"},
        headers=auth_headers(regular_user),
    )
    assert [a["id"] for a in resp.json()["activities"]] == [remote.id]

    # The instance scope only ever matches local sources, so a remote
    # source_type filter can never return rows.
    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance", "source_type": "remote"},
        headers=auth_headers(regular_user),
    )
    assert resp.json()["activities"] == []


@pytest.mark.asyncio
async def test_timeline_excludes_retracted(client, db_session, regular_user, other_user, auth_headers):
    """Soft-deleted activities never appear on the timeline."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    retracted = _make_activity("track", track.id, owner_user_id=other_user.id)
    retracted.deleted_at = datetime.now(timezone.utc)
    db_session.add_all([activity, retracted])
    await db_session.flush()

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance"},
        headers=auth_headers(regular_user),
    )
    assert [a["id"] for a in resp.json()["activities"]] == [activity.id]


@pytest.mark.asyncio
async def test_timeline_cursor_pagination(client, db_session, regular_user, other_user, auth_headers):
    """Pages are newest-first and the cursor continues without overlap."""
    track = await _make_track(db_session, other_user)
    activities = []
    for day in range(1, 6):
        activity = _make_activity(
            "track",
            track.id,
            owner_user_id=other_user.id,
            published_at=datetime(2026, 1, day, tzinfo=timezone.utc),
        )
        activities.append(activity)
        db_session.add(activity)
    await db_session.flush()
    expected = [a.id for a in reversed(activities)]

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance", "limit": 2},
        headers=auth_headers(regular_user),
    )
    body = resp.json()
    assert [a["id"] for a in body["activities"]] == expected[:2]

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance", "limit": 2, "cursor": body["next_cursor"]},
        headers=auth_headers(regular_user),
    )
    body = resp.json()
    assert [a["id"] for a in body["activities"]] == expected[2:4]

    resp = client.get(
        "/api/v1/timeline",
        params={"scope": "instance", "limit": 2, "cursor": body["next_cursor"]},
        headers=auth_headers(regular_user),
    )
    body = resp.json()
    assert [a["id"] for a in body["activities"]] == expected[4:]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_timeline_invalid_cursor(client, auth_headers, regular_user):
    """A malformed cursor is rejected with 400."""
    resp = client.get(
        "/api/v1/timeline",
        params={"cursor": "not-a-cursor"},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_timeline_response_shape(client, db_session, other_user):
    """Timeline items carry the resolved actor profile fields."""
    track = await _make_track(db_session, other_user)
    other_user.display_name = "The Other"
    activity = _make_activity("track", track.id, owner_user_id=other_user.id)
    db_session.add(activity)
    await db_session.flush()

    resp = client.get("/api/v1/timeline")

    assert resp.status_code == 200
    (item,) = resp.json()["activities"]
    assert item["id"] == activity.id
    assert item["entity_type"] == "track"
    assert item["activity_type"] == "create"
    assert item["source_actor_display_name"] == "The Other"
