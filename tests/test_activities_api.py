"""
Public activity read API tests - ``GET /api/v1/{entity_type}/{entity_id}/activities``,
the ``list_activities`` service, visibility filtering, and cursor pagination.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from songhive.models import Visibility
from songhive.models.activity import Activity, ActivityMention
from songhive.models.artist import Artist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.activities import list_activities


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


# ---------------------------------------------------------------------------
# GET /api/v1/{entity_type}/{entity_id}/activities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_endpoint_invalid_entity_type(client):
    """An unsupported entity_type is rejected with 400."""
    resp = client.get("/api/v1/bogus/whatever/activities")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_endpoint_entity_not_found(client):
    """A missing entity returns 404."""
    resp = client.get("/api/v1/track/missing-id/activities")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_endpoint_forbidden_entity(client, db_session, regular_user, other_user, auth_headers):
    """Entities the requester cannot access return 403."""
    track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)

    resp = client.get(f"/api/v1/track/{track.id}/activities")
    assert resp.status_code == 403

    resp = client.get(f"/api/v1/track/{track.id}/activities", headers=auth_headers(regular_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_endpoint_anonymous_sees_public_only(client, db_session, other_user):
    """Anonymous requesters see only public activities on a public entity."""
    track = await _make_track(db_session, other_user)
    public = _make_activity("track", track.id, owner_user_id=other_user.id)
    local = _make_activity("track", track.id, owner_user_id=other_user.id, visibility=Visibility.LOCAL.value)
    private = _make_activity("track", track.id, owner_user_id=other_user.id, visibility=Visibility.PRIVATE.value)
    db_session.add_all([public, local, private])
    await db_session.flush()

    resp = client.get(f"/api/v1/track/{track.id}/activities")

    assert resp.status_code == 200
    body = resp.json()
    assert [a["id"] for a in body["activities"]] == [public.id]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_endpoint_response_shape(client, db_session, regular_user, other_user, auth_headers):
    """The response exposes the documented activity fields and mentions."""
    track = await _make_track(db_session, other_user)
    activity = _make_activity(
        "track",
        track.id,
        owner_user_id=other_user.id,
        content="<p>Hello</p>",
        content_source="Hello",
        content_type="text/markdown",
    )
    activity.mentions.append(ActivityMention(handle="@regular", user_id=regular_user.id))
    db_session.add(activity)
    await db_session.flush()

    resp = client.get(f"/api/v1/track/{track.id}/activities", headers=auth_headers(regular_user))

    assert resp.status_code == 200
    (item,) = resp.json()["activities"]
    assert item["id"] == activity.id
    assert item["entity_type"] == "track"
    assert item["entity_id"] == track.id
    assert item["activity_type"] == "create"
    assert item["source_type"] == "local"
    assert item["source_actor"] == "https://local.example/users/alice"
    assert item["source_id"] == activity.source_id
    assert item["local_object_id"] == activity.local_object_id
    assert item["owner_user_id"] == other_user.id
    assert item["visibility"] == "public"
    assert item["content"] == "<p>Hello</p>"
    assert item["content_source"] == "Hello"
    assert item["content_type"] == "text/markdown"
    assert item["published_at"]
    assert item["in_reply_to_activity_id"] is None
    assert item["mentions"] == [{"handle": "@regular", "actor_url": None, "user_id": regular_user.id}]
    assert "payload" not in item


@pytest.mark.asyncio
async def test_list_endpoint_visibility_filtering(
    client, db_session, regular_user, other_user, admin_user, make_user, auth_headers
):
    """Authenticated non-privileged users see public, local, and followers."""
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

    resp = client.get(f"/api/v1/track/{track.id}/activities", headers=auth_headers(regular_user))
    seen = {a["id"] for a in resp.json()["activities"]}
    assert seen == {
        by_visibility[Visibility.PUBLIC].id,
        by_visibility[Visibility.FOLLOWERS].id,
        by_visibility[Visibility.LOCAL].id,
    }

    # The owner sees every visibility.
    resp = client.get(f"/api/v1/track/{track.id}/activities", headers=auth_headers(other_user))
    seen = {a["id"] for a in resp.json()["activities"]}
    assert seen == {a.id for a in by_visibility.values()}

    # Admins see every visibility.
    resp = client.get(f"/api/v1/track/{track.id}/activities", headers=auth_headers(admin_user))
    seen = {a["id"] for a in resp.json()["activities"]}
    assert seen == {a.id for a in by_visibility.values()}

    # A user named in the mentions sees the mentioned activity.
    mentioned = by_visibility[Visibility.MENTIONED]
    mentioned.mentions.append(ActivityMention(handle="@regular", user_id=regular_user.id))
    await db_session.flush()
    resp = client.get(f"/api/v1/track/{track.id}/activities", headers=auth_headers(regular_user))
    seen = {a["id"] for a in resp.json()["activities"]}
    assert mentioned.id in seen
    assert by_visibility[Visibility.PRIVATE].id not in seen


@pytest.mark.asyncio
async def test_list_endpoint_excludes_retracted(client, db_session, regular_user, auth_headers):
    """Soft-deleted activities are never listed."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    retracted = _make_activity("track", track.id, owner_user_id=regular_user.id)
    retracted.deleted_at = datetime.now(timezone.utc)
    db_session.add_all([activity, retracted])
    await db_session.flush()

    resp = client.get(f"/api/v1/track/{track.id}/activities", headers=auth_headers(regular_user))

    assert [a["id"] for a in resp.json()["activities"]] == [activity.id]


@pytest.mark.asyncio
async def test_list_endpoint_filters(client, db_session, regular_user, auth_headers):
    """activity_type and source_type filter the listing."""
    track = await _make_track(db_session, regular_user)
    create = _make_activity("track", track.id, owner_user_id=regular_user.id)
    like = _make_activity("track", track.id, owner_user_id=regular_user.id, activity_type="like")
    remote = _make_activity("track", track.id, owner_user_id=regular_user.id, source_type="activitypub")
    db_session.add_all([create, like, remote])
    await db_session.flush()

    resp = client.get(
        f"/api/v1/track/{track.id}/activities",
        params={"activity_type": "like"},
        headers=auth_headers(regular_user),
    )
    assert [a["id"] for a in resp.json()["activities"]] == [like.id]

    resp = client.get(
        f"/api/v1/track/{track.id}/activities",
        params={"source_type": "activitypub"},
        headers=auth_headers(regular_user),
    )
    assert [a["id"] for a in resp.json()["activities"]] == [remote.id]

    # Unknown filter values simply yield an empty page.
    resp = client.get(
        f"/api/v1/track/{track.id}/activities",
        params={"activity_type": "bogus"},
        headers=auth_headers(regular_user),
    )
    assert resp.json()["activities"] == []


@pytest.mark.asyncio
async def test_list_endpoint_cursor_pagination(client, db_session, regular_user, auth_headers):
    """Pages are newest-first and the cursor continues without overlap."""
    track = await _make_track(db_session, regular_user)
    activities = []
    for day in range(1, 6):
        activity = _make_activity(
            "track",
            track.id,
            owner_user_id=regular_user.id,
            published_at=datetime(2026, 1, day, tzinfo=timezone.utc),
        )
        activities.append(activity)
        db_session.add(activity)
    await db_session.flush()
    expected = [a.id for a in reversed(activities)]

    resp = client.get(
        f"/api/v1/track/{track.id}/activities",
        params={"limit": 2},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [a["id"] for a in body["activities"]] == expected[:2]
    cursor = body["next_cursor"]
    assert cursor

    resp = client.get(
        f"/api/v1/track/{track.id}/activities",
        params={"limit": 2, "cursor": cursor},
        headers=auth_headers(regular_user),
    )
    body = resp.json()
    assert [a["id"] for a in body["activities"]] == expected[2:4]

    resp = client.get(
        f"/api/v1/track/{track.id}/activities",
        params={"limit": 2, "cursor": body["next_cursor"]},
        headers=auth_headers(regular_user),
    )
    body = resp.json()
    assert [a["id"] for a in body["activities"]] == expected[4:]
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_endpoint_invalid_cursor(client, db_session, regular_user, auth_headers):
    """A malformed cursor is rejected with 400."""
    track = await _make_track(db_session, regular_user)

    resp = client.get(
        f"/api/v1/track/{track.id}/activities",
        params={"cursor": "not-a-cursor"},
        headers=auth_headers(regular_user),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_endpoint_limit_bounds(client, db_session, regular_user, auth_headers):
    """limit is validated between 1 and 100."""
    track = await _make_track(db_session, regular_user)

    for limit in (0, 101):
        resp = client.get(
            f"/api/v1/track/{track.id}/activities",
            params={"limit": limit},
            headers=auth_headers(regular_user),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# list_activities service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_activities_entity_isolation(db_session, regular_user, other_user):
    """Activities are scoped to their entity."""
    track = await _make_track(db_session, regular_user)
    other_track = await _make_track(db_session, other_user)
    mine = _make_activity("track", track.id, owner_user_id=regular_user.id)
    theirs = _make_activity("track", other_track.id, owner_user_id=other_user.id)
    album_activity = _make_activity("album", track.id, owner_user_id=regular_user.id)
    db_session.add_all([mine, theirs, album_activity])
    await db_session.flush()

    activities, next_cursor = await list_activities(
        db_session, entity_type="track", entity_id=str(track.id), user=regular_user
    )

    assert [a.id for a in activities] == [mine.id]
    assert next_cursor is None


@pytest.mark.asyncio
async def test_list_activities_cursor_tiebreak(db_session, regular_user):
    """Activities sharing a published_at are paginated by id without gaps."""
    track = await _make_track(db_session, regular_user)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    activities = [
        _make_activity("track", track.id, owner_user_id=regular_user.id, published_at=stamp) for _ in range(3)
    ]
    db_session.add_all(activities)
    await db_session.flush()

    seen = []
    cursor = None
    for _ in range(3):
        page, cursor = await list_activities(
            db_session,
            entity_type="track",
            entity_id=str(track.id),
            user=regular_user,
            limit=2,
            cursor=cursor,
        )
        seen.extend(a.id for a in page)
        if cursor is None:
            break

    assert sorted(seen) == sorted(a.id for a in activities)
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_list_activities_invalid_cursor(db_session, regular_user):
    """The service raises 400 for malformed cursors."""
    track = await _make_track(db_session, regular_user)

    with pytest.raises(HTTPException) as excinfo:
        await list_activities(
            db_session,
            entity_type="track",
            entity_id=str(track.id),
            user=regular_user,
            cursor="!!!",
        )
    assert excinfo.value.status_code == 400
