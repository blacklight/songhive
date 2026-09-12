"""
Tests for the notifications REST API and the admin purge endpoint.
"""

import pytest
import sqlalchemy as sa

from songhive.models.audit_log import AuditLog
from songhive.models.notification import Notification
from songhive.services import notifications


async def _make_notification(db_session, user, type="like", source_url=None):
    """Create a notification row directly in the test session."""
    notification = Notification(
        user_id=user.id,
        type=type,
        actor_url="https://remote.example/users/bob",
        source_url=source_url,
        payload={"actor_name": "bob"},
        delivered_targets=["in_app"],
    )
    db_session.add(notification)
    await db_session.commit()
    return notification


def test_notifications_require_auth(client):
    """All notification endpoints reject unauthenticated requests."""
    assert client.get("/api/v1/notifications/").status_code == 401
    assert client.get("/api/v1/notifications/unread-count").status_code == 401
    assert client.post("/api/v1/notifications/seen", json={"ids": ["x"]}).status_code == 401
    assert client.post("/api/v1/notifications/unseen", json={"ids": ["x"]}).status_code == 401
    assert client.post("/api/v1/notifications/seen-all").status_code == 401
    assert client.delete("/api/v1/notifications/some-id").status_code == 401
    assert client.post("/api/v1/notifications/delete", json={"ids": ["x"]}).status_code == 401
    assert client.post("/api/v1/notifications/clear").status_code == 401
    assert client.get("/api/v1/notifications/preferences").status_code == 401
    assert client.put("/api/v1/notifications/preferences", json={"preferences": []}).status_code == 401


@pytest.mark.asyncio
async def test_list_notifications_pagination(client, db_session, regular_user, auth_headers):
    """The list endpoint is newest-first, paginated, and sets X-Total-Count."""
    first = await _make_notification(db_session, regular_user, source_url="/a")
    second = await _make_notification(db_session, regular_user, source_url="/b")
    third = await _make_notification(db_session, regular_user, source_url="/c")

    headers = auth_headers(regular_user)
    response = client.get("/api/v1/notifications/", headers=headers)
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "3"
    items = response.json()
    assert [i["id"] for i in items] == [third.id, second.id, first.id]

    page = client.get("/api/v1/notifications/?limit=1&offset=1", headers=headers)
    assert page.status_code == 200
    assert page.headers["X-Total-Count"] == "3"
    assert [i["id"] for i in page.json()] == [second.id]

    item = page.json()[0]
    assert item["type"] == "like"
    assert item["actor_url"] == "https://remote.example/users/bob"
    assert item["payload"] == {"actor_name": "bob"}
    assert item["seen_at"] is None
    assert item["created_at"]


@pytest.mark.asyncio
async def test_list_notifications_seen_filter(client, db_session, regular_user, auth_headers):
    """The seen query param filters on seen_at NULL / not NULL."""
    unseen = await _make_notification(db_session, regular_user, source_url="/unseen")
    seen = await _make_notification(db_session, regular_user, source_url="/seen")
    await notifications.mark_seen(db_session, regular_user.id, [seen.id])
    await db_session.commit()

    headers = auth_headers(regular_user)
    unread = client.get("/api/v1/notifications/?seen=false", headers=headers)
    assert [i["id"] for i in unread.json()] == [unseen.id]
    assert unread.headers["X-Total-Count"] == "1"

    read = client.get("/api/v1/notifications/?seen=true", headers=headers)
    assert [i["id"] for i in read.json()] == [seen.id]


@pytest.mark.asyncio
async def test_list_notifications_type_filter(client, db_session, regular_user, auth_headers):
    """The type query param filters on a comma-separated type allowlist."""
    like = await _make_notification(db_session, regular_user, type="like", source_url="/like")
    follow = await _make_notification(db_session, regular_user, type="follow", source_url="/follow")
    await _make_notification(db_session, regular_user, type="mention", source_url="/mention")

    headers = auth_headers(regular_user)
    response = client.get("/api/v1/notifications/?type=like,follow", headers=headers)
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    assert [i["id"] for i in response.json()] == [follow.id, like.id]

    single = client.get("/api/v1/notifications/?type=mention", headers=headers)
    assert [i["type"] for i in single.json()] == ["mention"]

    # Unknown types are ignored; an allowlist of only unknowns matches nothing.
    none = client.get("/api/v1/notifications/?type=bogus", headers=headers)
    assert none.status_code == 200
    assert none.json() == []
    assert none.headers["X-Total-Count"] == "0"

    # The type filter composes with the seen filter.
    await notifications.mark_seen(db_session, regular_user.id, [like.id])
    await db_session.commit()
    unseen_likes = client.get("/api/v1/notifications/?type=like&seen=false", headers=headers)
    assert unseen_likes.json() == []


@pytest.mark.asyncio
async def test_notifications_scoped_to_current_user(client, db_session, regular_user, other_user, auth_headers):
    """A user only sees and modifies their own notifications."""
    mine = await _make_notification(db_session, regular_user, source_url="/mine")
    theirs = await _make_notification(db_session, other_user, source_url="/theirs")

    headers = auth_headers(regular_user)
    items = client.get("/api/v1/notifications/", headers=headers).json()
    assert [i["id"] for i in items] == [mine.id]

    response = client.post(
        "/api/v1/notifications/seen",
        json={"ids": [theirs.id]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"updated": 0}
    await db_session.refresh(theirs)
    assert theirs.seen_at is None


@pytest.mark.asyncio
async def test_mark_seen_unseen_and_seen_all(client, db_session, regular_user, auth_headers):
    """Bulk seen/unseen endpoints update rows and report affected counts."""
    n1 = await _make_notification(db_session, regular_user, source_url="/1")
    n2 = await _make_notification(db_session, regular_user, source_url="/2")
    headers = auth_headers(regular_user)

    count = client.get("/api/v1/notifications/unread-count", headers=headers)
    assert count.json() == {"count": 2}

    seen = client.post("/api/v1/notifications/seen", json={"ids": [n1.id]}, headers=headers)
    assert seen.json() == {"updated": 1}
    assert client.get("/api/v1/notifications/unread-count", headers=headers).json() == {"count": 1}

    unseen = client.post("/api/v1/notifications/unseen", json={"ids": [n1.id]}, headers=headers)
    assert unseen.json() == {"updated": 1}
    assert client.get("/api/v1/notifications/unread-count", headers=headers).json() == {"count": 2}

    all_seen = client.post("/api/v1/notifications/seen-all", headers=headers)
    assert all_seen.json() == {"updated": 2}
    assert client.get("/api/v1/notifications/unread-count", headers=headers).json() == {"count": 0}
    assert n2.id  # silence unused warnings


def test_mark_seen_validates_ids(client, regular_user, auth_headers):
    """Bulk endpoints reject an empty ids list."""
    headers = auth_headers(regular_user)
    response = client.post("/api/v1/notifications/seen", json={"ids": []}, headers=headers)
    assert response.status_code == 422
    response = client.post("/api/v1/notifications/delete", json={"ids": []}, headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_notification(client, db_session, regular_user, auth_headers):
    """DELETE removes the row and the list/unread-count reflect it."""
    notification = await _make_notification(db_session, regular_user, source_url="/x")
    headers = auth_headers(regular_user)

    response = client.delete(f"/api/v1/notifications/{notification.id}", headers=headers)
    assert response.status_code == 204

    remaining = await db_session.execute(sa.select(Notification.id))
    assert remaining.all() == []
    assert client.get("/api/v1/notifications/unread-count", headers=headers).json() == {"count": 0}

    # Deleting again reports 404.
    assert client.delete(f"/api/v1/notifications/{notification.id}", headers=headers).status_code == 404


@pytest.mark.asyncio
async def test_delete_notification_other_user(client, db_session, regular_user, other_user, auth_headers):
    """A user cannot delete another user's notification; it returns 404."""
    theirs = await _make_notification(db_session, other_user, source_url="/theirs")
    response = client.delete(f"/api/v1/notifications/{theirs.id}", headers=auth_headers(regular_user))
    assert response.status_code == 404
    await db_session.refresh(theirs)
    assert theirs.id


@pytest.mark.asyncio
async def test_delete_notifications_bulk(client, db_session, regular_user, other_user, auth_headers):
    """POST /delete removes the listed ids, scoped to the current user."""
    n1 = await _make_notification(db_session, regular_user, source_url="/1")
    n2 = await _make_notification(db_session, regular_user, source_url="/2")
    keep = await _make_notification(db_session, regular_user, source_url="/3")
    theirs = await _make_notification(db_session, other_user, source_url="/theirs")

    headers = auth_headers(regular_user)
    response = client.post(
        "/api/v1/notifications/delete",
        json={"ids": [n1.id, n2.id, theirs.id, "missing"]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": 2}

    remaining = await db_session.execute(sa.select(Notification.id))
    assert {row[0] for row in remaining.all()} == {keep.id, theirs.id}


@pytest.mark.asyncio
async def test_clear_notifications(client, db_session, regular_user, other_user, auth_headers):
    """POST /clear removes every notification of the current user only."""
    await _make_notification(db_session, regular_user, source_url="/1")
    await _make_notification(db_session, regular_user, source_url="/2")
    theirs = await _make_notification(db_session, other_user, source_url="/theirs")

    headers = auth_headers(regular_user)
    response = client.post("/api/v1/notifications/clear", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    assert client.get("/api/v1/notifications/", headers=headers).json() == []

    remaining = await db_session.execute(sa.select(Notification.id))
    assert [row[0] for row in remaining.all()] == [theirs.id]


def test_get_preferences_defaults(client, regular_user, auth_headers):
    """GET preferences returns all seven types with defaults."""
    response = client.get("/api/v1/notifications/preferences", headers=auth_headers(regular_user))
    assert response.status_code == 200
    prefs = response.json()["preferences"]
    assert len(prefs) == 7
    assert {p["type"] for p in prefs} == {
        "follow",
        "like",
        "boost",
        "quote",
        "reply",
        "mention",
        "share",
    }
    assert all(p["in_app"] and not p["email"] and not p["email_digest"] for p in prefs)


def test_put_preferences_persists_and_merges(client, regular_user, auth_headers):
    """PUT preferences upserts the given types; the GET view reflects them."""
    headers = auth_headers(regular_user)
    response = client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": [{"type": "like", "in_app": False, "email": True, "email_digest": True}]},
        headers=headers,
    )
    assert response.status_code == 200
    prefs = {p["type"]: p for p in response.json()["preferences"]}
    assert prefs["like"] == {"type": "like", "in_app": False, "email": True, "email_digest": True}
    assert prefs["follow"]["in_app"] is True

    again = client.get("/api/v1/notifications/preferences", headers=headers)
    assert again.json()["preferences"] == response.json()["preferences"]


def test_put_preferences_rejects_unknown_type(client, regular_user, auth_headers):
    """An unknown notification type is rejected with 422."""
    response = client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": [{"type": "poke", "in_app": True, "email": False, "email_digest": False}]},
        headers=auth_headers(regular_user),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_purge_endpoint(client, db_session, regular_user, admin_user, auth_headers):
    """The admin purge endpoint deletes old seen rows and writes an audit row."""
    old = await _make_notification(db_session, regular_user, source_url="/old")
    fresh = await _make_notification(db_session, regular_user, source_url="/fresh")
    unseen = await _make_notification(db_session, regular_user, source_url="/unseen")
    await notifications.mark_seen(db_session, regular_user.id, [old.id, fresh.id])

    from datetime import datetime, timedelta, timezone

    await db_session.execute(
        sa.update(Notification)
        .where(Notification.id == old.id)
        .values(seen_at=datetime.now(timezone.utc) - timedelta(days=365))
    )
    await db_session.commit()

    response = client.post("/api/v1/admin/notifications/purge", headers=auth_headers(admin_user))
    assert response.status_code == 200
    assert response.json() == {"deleted": 1}

    remaining = await db_session.execute(sa.select(Notification.id))
    assert {row[0] for row in remaining.all()} == {fresh.id, unseen.id}

    audit_rows = await db_session.execute(sa.select(AuditLog).where(AuditLog.action == "notification.purge"))
    entry = audit_rows.scalars().one()
    assert entry.actor_id == admin_user.id
    assert entry.details["deleted"] == 1
    assert entry.details["retention_days"] == 90


@pytest.mark.asyncio
async def test_admin_purge_requires_admin(client, db_session, regular_user, auth_headers):
    """Non-admin users get 403 on the purge endpoint."""
    response = client.post("/api/v1/admin/notifications/purge", headers=auth_headers(regular_user))
    assert response.status_code == 403
