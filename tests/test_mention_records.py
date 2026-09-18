"""
Mention record tests - the permanent per-user archive backing ``/mentions``.

Covers the service layer (upsert/list/delete/update), the REST endpoint,
and the three pipelines that write records: local activities, the
ActivityPub inbox, and materialized Webmentions.
"""

import pytest
from sqlalchemy import select
from webmentions import Webmention, WebmentionDirection, WebmentionType

from songhive.config.schema import SonghiveConfig
from songhive.models._enums import Visibility
from songhive.models.activity import Activity
from songhive.models.mention_record import MentionRecord, MentionSource
from songhive.models.notification import Notification
from songhive.models.user import User
from songhive.services import mention_records
from songhive.webmentions.service import materialize_webmention, retract_webmention

DOMAIN = "music.example.com"
BOB = "https://remote.example/users/bob"


@pytest.fixture
def config(tmp_path):
    """Test config with an instance domain so actor URLs resolve."""
    return SonghiveConfig(
        server={
            "host": "127.0.0.1",
            "port": 8000,
            "debug": True,
            "cors_origins": ["http://localhost:8080"],
        },
        database={"url": f"sqlite+aiosqlite:///{tmp_path / 'songhive.db'}"},
        federation={"enabled": False, "instance_domain": DOMAIN},
        auth={"secret_key": "a" * 32},
        storage={
            "local_path": str(tmp_path / "media"),
            "backend": "local",
        },
    )


async def _records_for(db_session, user: User):
    """Return all mention records for ``user``."""
    rows = await db_session.execute(select(MentionRecord).where(MentionRecord.user_id == user.id))
    return rows.scalars().all()


async def _record_for(db_session, user: User, source_url: str):
    """Return the single record for ``(user, source_url)`` or ``None``."""
    return (
        await db_session.execute(
            select(MentionRecord).where(
                MentionRecord.user_id == user.id,
                MentionRecord.source_url == source_url,
            )
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_creates_and_refreshes(db_session, regular_user):
    """A second upsert for the same key updates the row instead of duplicating."""
    first = await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.ACTIVITYPUB,
        source_url="https://remote.example/notes/1",
        actor_url=BOB,
        visibility="public",
        payload={"object_content": "v1"},
    )
    second = await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.ACTIVITYPUB,
        source_url="https://remote.example/notes/1",
        actor_url=BOB,
        visibility="mentioned",
        payload={"object_content": "v2"},
    )

    assert second.id == first.id
    assert second.visibility == "mentioned"
    assert second.payload["object_content"] == "v2"
    assert len(await _records_for(db_session, regular_user)) == 1


@pytest.mark.asyncio
async def test_upsert_keys_on_source(db_session, regular_user):
    """The same URL through different pipelines stores separate records."""
    for source in MentionSource:
        await mention_records.upsert_mention(
            db_session,
            user_id=regular_user.id,
            source=source,
            source_url="https://remote.example/notes/1",
        )
    assert len(await _records_for(db_session, regular_user)) == 3


@pytest.mark.asyncio
async def test_upsert_merge_payload(db_session, regular_user):
    """``merge_payload`` merges keys and removes ``None``-valued ones."""
    record = await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.LOCAL,
        source_url="https://local.example/objects/1",
        payload={"actor_name": "alice", "object_content": "v1", "object_name": "t"},
    )
    updated = await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.LOCAL,
        source_url="https://local.example/objects/1",
        payload={"object_content": "v2", "object_name": None},
        merge_payload=True,
    )

    assert updated.id == record.id
    assert updated.payload == {"actor_name": "alice", "object_content": "v2"}


@pytest.mark.asyncio
async def test_list_mentions_filters_and_pagination(db_session, regular_user):
    """Listing is newest-first and honors source and private filters."""
    await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.LOCAL,
        source_url="local-1",
        visibility="public",
    )
    await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.ACTIVITYPUB,
        source_url="ap-1",
        visibility="mentioned",
    )
    await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.WEBMENTION,
        source_url="wm-1",
        visibility="private",
    )

    rows, total = await mention_records.list_mentions(db_session, regular_user.id)
    assert total == 3
    assert [r.source_url for r in rows] == ["wm-1", "ap-1", "local-1"]

    rows, total = await mention_records.list_mentions(db_session, regular_user.id, sources=["local", "webmention"])
    assert total == 2
    assert {r.source_url for r in rows} == {"local-1", "wm-1"}

    rows, total = await mention_records.list_mentions(db_session, regular_user.id, private_only=True)
    assert total == 2
    assert {r.source_url for r in rows} == {"ap-1", "wm-1"}

    rows, total = await mention_records.list_mentions(db_session, regular_user.id, limit=1, offset=1)
    assert total == 3
    assert [r.source_url for r in rows] == ["ap-1"]


@pytest.mark.asyncio
async def test_delete_mentions_scopes(db_session, regular_user, other_user):
    """Deletion honors user, source, source_url, actor and exclusion scopes."""
    for user, source_url, actor in (
        (regular_user, "keep", BOB),
        (regular_user, "drop", BOB),
        (regular_user, "drop-actor", "https://remote.example/users/carol"),
        (other_user, "drop", BOB),
    ):
        await mention_records.upsert_mention(
            db_session,
            user_id=user.id,
            source=MentionSource.ACTIVITYPUB,
            source_url=source_url,
            actor_url=actor,
        )

    # Scoped by source_url + user: only regular's "drop" goes.
    removed = await mention_records.delete_mentions(db_session, user_id=regular_user.id, source_url="drop")
    assert removed == 1

    # Scoped by actor: carol's records go regardless of source_url.
    removed = await mention_records.delete_mentions(
        db_session, user_id=regular_user.id, actor_urls=["https://remote.example/users/carol"]
    )
    assert removed == 1

    # ``exclude_user_ids`` keeps rows still addressed by an edit.
    removed = await mention_records.delete_mentions(db_session, source_url="drop", exclude_user_ids=[other_user.id])
    assert removed == 0

    remaining = await _records_for(db_session, regular_user)
    assert [r.source_url for r in remaining] == ["keep"]
    assert len(await _records_for(db_session, other_user)) == 1


@pytest.mark.asyncio
async def test_delete_mentions_referencing(db_session, regular_user):
    """Records are removed by ``source_url`` or a payload ``activity_id``."""
    await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.ACTIVITYPUB,
        source_url="https://remote.example/notes/1",
        payload={"activity_id": "https://remote.example/activities/c1"},
    )
    await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.ACTIVITYPUB,
        source_url="https://remote.example/notes/2",
    )

    removed = await mention_records.delete_mentions_referencing(
        db_session, ["https://remote.example/activities/c1"], user_id=regular_user.id
    )
    assert removed == 1
    removed = await mention_records.delete_mentions_referencing(
        db_session, ["https://remote.example/notes/2"], user_id=regular_user.id
    )
    assert removed == 1
    assert await _records_for(db_session, regular_user) == []


@pytest.mark.asyncio
async def test_update_mentions_visibility(db_session, regular_user):
    """Visibility rewrites apply to every record of the source object."""
    await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.LOCAL,
        source_url="local-1",
        visibility="public",
    )
    updated = await mention_records.update_mentions_visibility(db_session, "local-1", "private")
    assert updated == 1
    record = await _record_for(db_session, regular_user, "local-1")
    assert record.visibility == "private"


@pytest.mark.asyncio
async def test_update_mentions_from_actor(db_session, regular_user, other_user):
    """Actor field patches merge into stored payloads, removing ``None`` keys."""
    record = await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source=MentionSource.ACTIVITYPUB,
        source_url="ap-1",
        actor_url=BOB,
        payload={"actor_name": "bob", "actor_avatar_url": "https://a/x.png"},
    )
    await mention_records.upsert_mention(
        db_session,
        user_id=other_user.id,
        source=MentionSource.ACTIVITYPUB,
        source_url="ap-1",
        actor_url=BOB,
        payload={"actor_name": "bob"},
    )

    changed = await mention_records.update_mentions_from_actor(
        db_session,
        BOB,
        fields={"actor_name": "robert", "actor_avatar_url": None},
        user_id=regular_user.id,
    )
    assert changed == 1
    await db_session.refresh(record)
    assert record.payload == {"actor_name": "robert"}


# ---------------------------------------------------------------------------
# REST endpoint
# ---------------------------------------------------------------------------


def test_mentions_requires_auth(client):
    """The endpoint rejects unauthenticated requests."""
    assert client.get("/api/v1/mentions/").status_code == 401


@pytest.mark.asyncio
async def test_list_mentions_scoped_and_paginated(client, db_session, regular_user, other_user, auth_headers):
    """A user only sees their own records; the endpoint paginates."""
    first = await mention_records.upsert_mention(db_session, user_id=regular_user.id, source="local", source_url="/a")
    second = await mention_records.upsert_mention(db_session, user_id=regular_user.id, source="local", source_url="/b")
    await mention_records.upsert_mention(db_session, user_id=other_user.id, source="local", source_url="/theirs")
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.get("/api/v1/mentions/", headers=headers)
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "2"
    assert [i["id"] for i in response.json()] == [second.id, first.id]

    page = client.get("/api/v1/mentions/?limit=1&offset=1", headers=headers)
    assert [i["id"] for i in page.json()] == [first.id]
    assert page.headers["X-Total-Count"] == "2"

    item = page.json()[0]
    assert item["source"] == "local"
    assert item["source_url"] == "/a"
    assert item["created_at"]


@pytest.mark.asyncio
async def test_list_mentions_source_filter(client, db_session, regular_user, auth_headers):
    """The ``source`` param filters on a comma-separated source allowlist."""
    local = await mention_records.upsert_mention(db_session, user_id=regular_user.id, source="local", source_url="l")
    ap = await mention_records.upsert_mention(db_session, user_id=regular_user.id, source="activitypub", source_url="a")
    await mention_records.upsert_mention(db_session, user_id=regular_user.id, source="webmention", source_url="w")
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.get("/api/v1/mentions/?source=local,activitypub", headers=headers)
    assert {i["id"] for i in response.json()} == {local.id, ap.id}

    # Unknown sources are ignored; an allowlist of only unknowns is empty.
    none = client.get("/api/v1/mentions/?source=bogus", headers=headers)
    assert none.status_code == 200
    assert none.json() == []


@pytest.mark.asyncio
async def test_list_mentions_private_filter(client, db_session, regular_user, auth_headers):
    """``visibility=private`` restricts to non-public records."""
    await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source="local",
        source_url="pub",
        visibility="public",
    )
    mentioned = await mention_records.upsert_mention(
        db_session,
        user_id=regular_user.id,
        source="activitypub",
        source_url="priv",
        visibility="mentioned",
    )
    await db_session.commit()

    headers = auth_headers(regular_user)
    response = client.get("/api/v1/mentions/?visibility=private", headers=headers)
    assert [i["id"] for i in response.json()] == [mentioned.id]

    all_rows = client.get("/api/v1/mentions/?visibility=all", headers=headers)
    assert len(all_rows.json()) == 2


# ---------------------------------------------------------------------------
# Local activity pipeline
# ---------------------------------------------------------------------------


def _post_status(client, user, auth_headers, **kwargs):
    return client.post("/api/v1/statuses/", json=kwargs, headers=auth_headers(user))


@pytest.mark.asyncio
async def test_status_mention_creates_record(client, db_session, regular_user, other_user, auth_headers):
    """A ``@user`` status archives a local mention record for the target."""
    resp = _post_status(client, regular_user, auth_headers, status="hey @other")
    assert resp.status_code == 201
    activity_id = resp.json()["id"]

    record = (
        await db_session.execute(select(MentionRecord).where(MentionRecord.user_id == other_user.id))
    ).scalar_one()
    assert record.source == MentionSource.LOCAL.value
    assert record.activity_id == activity_id
    assert record.source_url == resp.json()["source_id"]
    assert record.visibility == "public"
    assert record.payload["actor_name"]
    assert record.payload["object_content"]


@pytest.mark.asyncio
async def test_status_mention_private_visibility(client, db_session, regular_user, other_user, auth_headers):
    """A non-public status archives with its stored visibility."""
    resp = _post_status(client, regular_user, auth_headers, status="psst @other", visibility="mentioned")
    assert resp.status_code == 201

    record = (
        await db_session.execute(select(MentionRecord).where(MentionRecord.user_id == other_user.id))
    ).scalar_one()
    assert record.visibility == "mentioned"


@pytest.mark.asyncio
async def test_notification_dismissal_keeps_record(client, db_session, regular_user, other_user, auth_headers):
    """Dismissing the notification does not remove the archived record."""
    resp = _post_status(client, regular_user, auth_headers, status="hey @other")
    assert resp.status_code == 201

    notification = (
        await db_session.execute(select(Notification).where(Notification.user_id == other_user.id))
    ).scalar_one()
    deleted = client.delete(f"/api/v1/notifications/{notification.id}", headers=auth_headers(other_user))
    assert deleted.status_code == 204

    record = (
        await db_session.execute(select(MentionRecord).where(MentionRecord.user_id == other_user.id))
    ).scalar_one_or_none()
    assert record is not None
    assert client.get("/api/v1/mentions/", headers=auth_headers(other_user)).json() != []


@pytest.mark.asyncio
async def test_status_edit_syncs_records(client, db_session, regular_user, other_user, auth_headers):
    """Edits archive new recipients and drop removed ones."""
    created = _post_status(client, regular_user, auth_headers, status="no mentions")
    assert created.status_code == 201
    activity_id = created.json()["id"]

    # Adding a mention archives a record.
    edited = client.patch(
        f"/api/v1/activities/{activity_id}",
        json={"content": "now @other"},
        headers=auth_headers(regular_user),
    )
    assert edited.status_code == 200
    record = (
        await db_session.execute(select(MentionRecord).where(MentionRecord.user_id == other_user.id))
    ).scalar_one_or_none()
    assert record is not None

    # Removing the mention drops the record.
    edited = client.patch(
        f"/api/v1/activities/{activity_id}",
        json={"content": "no mentions again"},
        headers=auth_headers(regular_user),
    )
    assert edited.status_code == 200
    assert await _records_for(db_session, other_user) == []


@pytest.mark.asyncio
async def test_status_retract_removes_record(client, db_session, regular_user, other_user, auth_headers):
    """Retracting the activity removes its archived records."""
    resp = _post_status(client, regular_user, auth_headers, status="hey @other")
    activity_id = resp.json()["id"]

    deleted = client.delete(f"/api/v1/activities/{activity_id}", headers=auth_headers(regular_user))
    assert deleted.status_code == 200
    assert await _records_for(db_session, other_user) == []


@pytest.mark.asyncio
async def test_status_visibility_update_syncs_record(client, db_session, regular_user, other_user, auth_headers):
    """Audience edits keep the record's stored visibility accurate."""
    resp = _post_status(client, regular_user, auth_headers, status="hey @other")
    activity_id = resp.json()["id"]

    edited = client.patch(
        f"/api/v1/activities/{activity_id}",
        json={"visibility": "mentioned"},
        headers=auth_headers(regular_user),
    )
    assert edited.status_code == 200

    record = (
        await db_session.execute(select(MentionRecord).where(MentionRecord.user_id == other_user.id))
    ).scalar_one()
    assert record.visibility == "mentioned"


# ---------------------------------------------------------------------------
# ActivityPub inbox pipeline
# ---------------------------------------------------------------------------


def _create_note_activity(note_id: str, *, tag=None, to=None, **extra):
    """Build an inbound ``Create`` wrapping a ``Note``."""
    note = {"type": "Note", "id": note_id, "content": "<p>hi</p>", **extra}
    if tag is not None:
        note["tag"] = tag
    if to is not None:
        note["to"] = to
    return {
        "type": "Create",
        "id": "https://remote.example/activities/c1",
        "actor": BOB,
        "object": note,
    }


def _mention_tag(actor_url: str):
    return [{"type": "Mention", "href": actor_url, "name": "@user"}]


@pytest.fixture
async def alice(db_session, regular_user):
    """A local user provisioned with a federated actor URL."""
    regular_user.actor_url = f"https://{DOMAIN}/users/{regular_user.username}"
    await db_session.flush()
    return regular_user


@pytest.mark.asyncio
async def test_inbox_mention_creates_record(db_session, alice):
    """An incoming public mention archives an ``activitypub`` record."""
    from songhive.federation.notifications import create_inbox_notifications

    await create_inbox_notifications(
        db_session,
        activity=_create_note_activity(
            "https://remote.example/notes/1",
            tag=_mention_tag(alice.actor_url),
            to=["https://www.w3.org/ns/activitystreams#Public"],
        ),
        recipient=alice,
        instance_domain=DOMAIN,
    )

    record = await _record_for(db_session, alice, "https://remote.example/notes/1")
    assert record is not None
    assert record.source == MentionSource.ACTIVITYPUB.value
    assert record.actor_url == BOB
    assert record.visibility == "public"
    assert record.payload["object_content"] == "<p>hi</p>"


@pytest.mark.asyncio
async def test_inbox_mention_visibility_classification(db_session, alice):
    """Non-public audiences classify as followers or mentioned."""
    from songhive.federation.notifications import create_inbox_notifications

    await create_inbox_notifications(
        db_session,
        activity=_create_note_activity(
            "https://remote.example/notes/followers",
            tag=_mention_tag(alice.actor_url),
            to=[f"{BOB}/followers"],
        ),
        recipient=alice,
        instance_domain=DOMAIN,
    )
    await create_inbox_notifications(
        db_session,
        activity=_create_note_activity(
            "https://remote.example/notes/limited",
            tag=_mention_tag(alice.actor_url),
            to=[alice.actor_url],
        ),
        recipient=alice,
        instance_domain=DOMAIN,
    )

    followers = await _record_for(db_session, alice, "https://remote.example/notes/followers")
    limited = await _record_for(db_session, alice, "https://remote.example/notes/limited")
    assert followers.visibility == "followers"
    assert limited.visibility == "mentioned"


@pytest.mark.asyncio
async def test_inbox_mention_recorded_when_notification_suppressed(db_session, alice):
    """A reply/quote notification covering the note still archives the mention."""
    from songhive.federation.notifications import create_inbox_notifications

    # alice's own local post the remote note replies to.
    original = Activity(
        entity_type="user",
        entity_id=str(alice.id),
        activity_type="create",
        source_type="local",
        source_actor=alice.actor_url,
        source_id=f"{alice.actor_url}/objects/original",
        owner_user_id=alice.id,
        visibility="public",
    )
    db_session.add(original)
    await db_session.flush()

    await create_inbox_notifications(
        db_session,
        activity=_create_note_activity(
            "https://remote.example/notes/reply",
            tag=_mention_tag(alice.actor_url),
            to=[alice.actor_url],
            inReplyTo=original.source_id,
        ),
        recipient=alice,
        instance_domain=DOMAIN,
    )

    # The reply notification suppressed a redundant ``mention`` notification…
    types = (
        (await db_session.execute(select(Notification.type).where(Notification.user_id == alice.id))).scalars().all()
    )
    assert types == ["reply"]
    # …but the archive still records the mention.
    record = await _record_for(db_session, alice, "https://remote.example/notes/reply")
    assert record is not None
    assert record.source == MentionSource.ACTIVITYPUB.value


@pytest.mark.asyncio
async def test_inbox_mention_recorded_when_delivery_disabled(db_session, alice):
    """Delivery preferences that drop in-app rows still leave the archive."""
    from songhive.federation.notifications import create_inbox_notifications
    from songhive.models.notification import NotificationPreference

    db_session.add(
        NotificationPreference(
            user_id=alice.id,
            type="mention",
            in_app=False,
            email=False,
            email_digest=False,
        )
    )
    await db_session.flush()

    await create_inbox_notifications(
        db_session,
        activity=_create_note_activity(
            "https://remote.example/notes/2",
            tag=_mention_tag(alice.actor_url),
            to=[alice.actor_url],
        ),
        recipient=alice,
        instance_domain=DOMAIN,
    )

    assert (
        await db_session.execute(select(Notification).where(Notification.user_id == alice.id))
    ).scalars().all() == []
    record = await _record_for(db_session, alice, "https://remote.example/notes/2")
    assert record is not None


@pytest.mark.asyncio
async def test_inbox_delete_removes_record(db_session, alice):
    """A ``Delete`` of the note removes the archived record."""
    from songhive.federation.notifications import (
        create_inbox_notifications,
        retract_inbox_notifications,
    )

    note_id = "https://remote.example/notes/3"
    await create_inbox_notifications(
        db_session,
        activity=_create_note_activity(note_id, tag=_mention_tag(alice.actor_url)),
        recipient=alice,
        instance_domain=DOMAIN,
    )
    assert await _record_for(db_session, alice, note_id) is not None

    await retract_inbox_notifications(
        db_session,
        activity={"type": "Delete", "actor": BOB, "object": note_id},
        recipient=alice,
    )
    assert await _record_for(db_session, alice, note_id) is None


@pytest.mark.asyncio
async def test_inbox_actor_delete_removes_records(db_session, alice):
    """Deleting the actor removes every record it produced for the user."""
    from songhive.federation.notifications import (
        create_inbox_notifications,
        retract_inbox_notifications,
    )

    for note_id in ("https://remote.example/notes/a", "https://remote.example/notes/b"):
        await create_inbox_notifications(
            db_session,
            activity=_create_note_activity(note_id, tag=_mention_tag(alice.actor_url)),
            recipient=alice,
            instance_domain=DOMAIN,
        )
    assert len(await _records_for(db_session, alice)) == 2

    await retract_inbox_notifications(
        db_session,
        activity={"type": "Delete", "actor": BOB, "object": BOB},
        recipient=alice,
    )
    assert await _records_for(db_session, alice) == []


@pytest.mark.asyncio
async def test_inbox_update_syncs_record(db_session, alice):
    """An ``Update`` merges into the record or removes it when unmentioned."""
    from songhive.federation.notifications import (
        create_inbox_notifications,
        update_inbox_notifications,
    )

    note_id = "https://remote.example/notes/4"
    await create_inbox_notifications(
        db_session,
        activity=_create_note_activity(note_id, tag=_mention_tag(alice.actor_url)),
        recipient=alice,
        instance_domain=DOMAIN,
    )

    # Still mentioned: the snapshot merges.
    await update_inbox_notifications(
        db_session,
        activity={
            "type": "Update",
            "actor": BOB,
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>edited</p>",
                "tag": _mention_tag(alice.actor_url),
                "to": [alice.actor_url],
            },
        },
        recipient=alice,
        instance_domain=DOMAIN,
    )
    record = await _record_for(db_session, alice, note_id)
    assert record is not None
    assert record.payload["object_content"] == "<p>edited</p>"
    assert record.visibility == "mentioned"

    # No longer mentioned: the record is dropped.
    await update_inbox_notifications(
        db_session,
        activity={
            "type": "Update",
            "actor": BOB,
            "object": {
                "type": "Note",
                "id": note_id,
                "content": "<p>edited again</p>",
                "tag": [],
            },
        },
        recipient=alice,
        instance_domain=DOMAIN,
    )
    assert await _record_for(db_session, alice, note_id) is None


# ---------------------------------------------------------------------------
# Webmention pipeline
# ---------------------------------------------------------------------------


async def _make_track(db_session, owner: User):
    from songhive.models.artist import Artist
    from songhive.models.track import Track

    artist = Artist(name="Test Artist")
    db_session.add(artist)
    await db_session.flush()
    track = Track(title="T", artist_id=artist.id, owner_id=owner.id, visibility=Visibility.PUBLIC.value)
    db_session.add(track)
    await db_session.flush()
    return track


def _webmention(source: str, target: str, **kwargs) -> Webmention:
    return Webmention(
        source=source,
        target=target,
        direction=WebmentionDirection.IN,
        mention_type=WebmentionType.MENTION,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_materialize_webmention_creates_record(db_session, config, regular_user):
    """A materialized Webmention archives a ``webmention`` record."""
    track = await _make_track(db_session, regular_user)
    mention = _webmention(
        "https://blog.example/posts/1",
        f"https://{DOMAIN}/tracks/{track.id}",
        excerpt="Nice track!",
        author_name="Alice",
        author_url="https://blog.example/alice",
    )

    activity = await materialize_webmention(db_session, mention, config)
    assert activity is not None

    record = await _record_for(db_session, regular_user, activity.source_id)
    assert record is not None
    assert record.source == MentionSource.WEBMENTION.value
    assert record.activity_id == str(activity.id)
    assert record.actor_url == "https://blog.example/alice"
    assert record.payload["object_url"] == mention.source
    assert record.payload["webmention_type"] == "mention"


@pytest.mark.asyncio
async def test_retract_webmention_removes_record(db_session, config, regular_user):
    """Retracting the Webmention removes the archived record with it."""
    track = await _make_track(db_session, regular_user)
    mention = _webmention(
        "https://blog.example/posts/1",
        f"https://{DOMAIN}/tracks/{track.id}",
    )

    activity = await materialize_webmention(db_session, mention, config)
    assert await _record_for(db_session, regular_user, activity.source_id) is not None

    await retract_webmention(db_session, mention)
    assert await _record_for(db_session, regular_user, activity.source_id) is None


@pytest.mark.asyncio
async def test_webmention_record_updates_on_resend(db_session, config, regular_user):
    """A re-sent Webmention refreshes the record instead of duplicating it."""
    track = await _make_track(db_session, regular_user)
    mention = _webmention(
        "https://blog.example/posts/1",
        f"https://{DOMAIN}/tracks/{track.id}",
        excerpt="v1",
    )
    await materialize_webmention(db_session, mention, config)
    mention.excerpt = "v2"
    activity = await materialize_webmention(db_session, mention, config)

    records = await _records_for(db_session, regular_user)
    assert len(records) == 1
    assert records[0].activity_id == str(activity.id)
    assert records[0].payload["object_content"] == "v2"


# ---------------------------------------------------------------------------
# User deletion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_deletion_removes_records(db_session, regular_user, other_user):
    """Deleting a user removes their archive and records they produced."""
    await mention_records.upsert_mention(db_session, user_id=regular_user.id, source="local", source_url="mine")
    await mention_records.upsert_mention(
        db_session,
        user_id=other_user.id,
        source="local",
        source_url="theirs",
        actor_url=regular_user.actor_url or f"/users/{regular_user.username}",
    )

    from songhive.users.manager import _remove_user_references

    await _remove_user_references(db_session, regular_user)

    assert await _records_for(db_session, regular_user) == []
    assert await _records_for(db_session, other_user) == []
