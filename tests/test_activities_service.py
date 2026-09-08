"""
Activity service tests - entity resolution, local activity creation,
visibility enforcement, and the deletion cascade.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from songhive.config.schema import StorageConfig
from songhive.models._enums import Visibility
from songhive.models.activity import Activity, ActivityMention, ActivityTarget
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.playlist import Playlist
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import deletion
from songhive.services.activities import (
    ActivityCreateParams,
    create_local_activity,
    resolve_entity,
)
from songhive.services.storage import StorageService
from songhive.storage import get_storage


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


async def _make_album(session, owner: User | None, visibility: str = Visibility.PUBLIC.value) -> Album:
    """Create and persist a test album."""
    artist = await _make_artist(session)
    album = Album(
        title="Test Album",
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(album)
    await session.flush()
    return album


async def _make_playlist(session, owner: User | None, visibility: str = Visibility.PUBLIC.value) -> Playlist:
    """Create and persist a test playlist."""
    playlist = Playlist(
        name="Test Playlist",
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(playlist)
    await session.flush()
    return playlist


async def _make_library(session, owner: User | None, visibility: str = Visibility.PUBLIC.value) -> Library:
    """Create and persist a test library."""
    library = Library(
        name="Test Library",
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(library)
    await session.flush()
    return library


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


@pytest.fixture
def storage_service(tmp_path):
    """Create a StorageService backed by a local temp directory."""
    config = StorageConfig(backend="local", local_path=tmp_path / "media")
    backend = get_storage(config)
    return StorageService(backend, config)


@pytest.mark.asyncio
async def test_resolve_entity_all_types(db_session, regular_user):
    """resolve_entity resolves every supported entity type."""
    track = await _make_track(db_session, regular_user)
    album = await _make_album(db_session, regular_user)
    artist = await _make_artist(db_session)
    playlist = await _make_playlist(db_session, regular_user)
    library = await _make_library(db_session, regular_user)

    assert await resolve_entity(db_session, "track", track.id) == track
    assert await resolve_entity(db_session, "album", album.id) == album
    assert await resolve_entity(db_session, "artist", artist.id) == artist
    assert await resolve_entity(db_session, "playlist", playlist.id) == playlist
    assert await resolve_entity(db_session, "library", library.id) == library


@pytest.mark.asyncio
async def test_resolve_entity_invalid_type(db_session):
    """resolve_entity returns None for unsupported entity types."""
    assert await resolve_entity(db_session, "station", "whatever") is None


@pytest.mark.asyncio
async def test_resolve_entity_missing_id(db_session):
    """resolve_entity returns None for a valid type with a missing id."""
    assert await resolve_entity(db_session, "track", "missing-id") is None


@pytest.mark.asyncio
async def test_create_local_activity(db_session, regular_user):
    """A local activity records the author, source identity, and content."""
    regular_user.actor_url = "https://local.example/users/regular"
    track = await _make_track(db_session, regular_user)

    activity = await create_local_activity(
        db_session,
        entity_type="track",
        entity_id=track.id,
        activity_type="create",
        author=regular_user,
        visibility=Visibility.PUBLIC,
        content="<p>hello</p>",
    )

    assert activity.id is not None
    assert activity.entity_type == "track"
    assert activity.entity_id == str(track.id)
    assert activity.activity_type == "create"
    assert activity.source_type == "local"
    assert activity.source_actor == "https://local.example/users/regular"
    assert activity.source_id == f"{regular_user.actor_url}/objects/{activity.local_object_id}"
    assert activity.local_object_id is not None
    assert activity.owner_user_id == regular_user.id
    assert activity.visibility == Visibility.PUBLIC.value
    assert activity.content == "<p>hello</p>"
    assert activity.content_type == "text/plain"
    assert activity.published_at is not None


@pytest.mark.asyncio
async def test_create_local_activity_with_content_source(db_session, regular_user):
    """content_source marks the activity as markdown and fills content."""
    track = await _make_track(db_session, regular_user)

    activity = await create_local_activity(
        db_session,
        entity_type="track",
        entity_id=track.id,
        activity_type="reply",
        author=regular_user,
        visibility="public",
        content_source="hi **there**",
    )

    assert activity.content == "hi **there**"
    assert activity.content_source == "hi **there**"
    assert activity.content_type == "text/markdown"


@pytest.mark.asyncio
async def test_create_local_activity_without_actor_url(db_session, regular_user):
    """Authors without a provisioned actor URL get a local URN identity."""
    track = await _make_track(db_session, regular_user)

    activity = await create_local_activity(
        db_session,
        entity_type="track",
        entity_id=track.id,
        activity_type="create",
        author=regular_user,
        visibility=Visibility.PUBLIC,
    )

    assert activity.source_actor == f"urn:songhive:user:{regular_user.username}"
    assert activity.source_id.startswith(f"{activity.source_actor}/objects/")


@pytest.mark.asyncio
async def test_create_local_activity_persists_mentions(db_session, regular_user, other_user):
    """Pre-resolved mentions are persisted as ActivityMention rows."""
    track = await _make_track(db_session, regular_user)

    activity = await create_local_activity(
        db_session,
        entity_type="track",
        entity_id=track.id,
        activity_type="mention",
        author=regular_user,
        visibility=Visibility.PUBLIC,
        content="hi @other",
        mentions=[
            {"handle": "@other", "user_id": other_user.id},
            {"handle": "@bob@remote.example", "actor_url": "https://remote.example/users/bob"},
        ],
    )

    result = await db_session.execute(select(ActivityMention).where(ActivityMention.activity_id == activity.id))
    mentions = {m.handle: m for m in result.scalars().all()}
    assert mentions["@other"].user_id == other_user.id
    assert mentions["@bob@remote.example"].actor_url == "https://remote.example/users/bob"


@pytest.mark.asyncio
async def test_create_local_activity_entity_not_found(db_session, regular_user):
    """Missing entities produce a 404."""
    with pytest.raises(HTTPException) as excinfo:
        await create_local_activity(
            db_session,
            entity_type="track",
            entity_id="missing-id",
            activity_type="create",
            author=regular_user,
            visibility=Visibility.PUBLIC,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_create_local_activity_invalid_entity_type(db_session, regular_user):
    """Unsupported entity types resolve to None and produce a 404."""
    with pytest.raises(HTTPException) as excinfo:
        await create_local_activity(
            db_session,
            entity_type="station",
            entity_id="whatever",
            activity_type="create",
            author=regular_user,
            visibility=Visibility.PUBLIC,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_create_local_activity_invalid_activity_type(db_session, regular_user):
    """Invalid activity types are rejected with a 422."""
    track = await _make_track(db_session, regular_user)

    with pytest.raises(HTTPException) as excinfo:
        await create_local_activity(
            db_session,
            entity_type="track",
            entity_id=track.id,
            activity_type="boost-really",
            author=regular_user,
            visibility=Visibility.PUBLIC,
        )
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_create_local_activity_visibility_error(db_session, regular_user):
    """An activity cannot be more visible than its containing entity."""
    track = await _make_track(db_session, regular_user, visibility=Visibility.PRIVATE.value)

    with pytest.raises(HTTPException) as excinfo:
        await create_local_activity(
            db_session,
            entity_type="track",
            entity_id=track.id,
            activity_type="create",
            author=regular_user,
            visibility=Visibility.PUBLIC,
        )
    assert excinfo.value.status_code == 422


@pytest.mark.asyncio
async def test_create_local_activity_visibility_allowed(db_session, regular_user):
    """A less-visible activity is allowed inside a more-visible entity."""
    track = await _make_track(db_session, regular_user, visibility=Visibility.PUBLIC.value)

    activity = await create_local_activity(
        db_session,
        entity_type="track",
        entity_id=track.id,
        activity_type="create",
        author=regular_user,
        visibility=Visibility.FOLLOWERS,
    )
    assert activity.visibility == Visibility.FOLLOWERS.value


@pytest.mark.asyncio
async def test_create_local_activity_not_authorized(db_session, regular_user, other_user):
    """Users who cannot manage the entity cannot create activities for it."""
    track = await _make_track(db_session, other_user)

    with pytest.raises(HTTPException) as excinfo:
        await create_local_activity(
            db_session,
            entity_type="track",
            entity_id=track.id,
            activity_type="create",
            author=regular_user,
            visibility=Visibility.PUBLIC,
        )
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_create_local_activity_admin(db_session, regular_user, admin_user):
    """Admins can create activities for entities they do not own."""
    track = await _make_track(db_session, regular_user)

    activity = await create_local_activity(
        db_session,
        entity_type="track",
        entity_id=track.id,
        activity_type="create",
        author=admin_user,
        visibility=Visibility.PUBLIC,
    )
    assert activity.owner_user_id == admin_user.id


@pytest.mark.asyncio
async def test_create_local_activity_on_artist(db_session, admin_user):
    """Artists have no visibility column and act as public containers."""
    artist = await _make_artist(db_session)

    activity = await create_local_activity(
        db_session,
        entity_type="artist",
        entity_id=artist.id,
        activity_type="create",
        author=admin_user,
        visibility=Visibility.PUBLIC,
    )
    assert activity.entity_type == "artist"

    # A less-visible activity is also allowed inside the public container.
    private = await create_local_activity(
        db_session,
        entity_type="artist",
        entity_id=artist.id,
        activity_type="create",
        author=admin_user,
        visibility=Visibility.PRIVATE,
    )
    assert private.visibility == Visibility.PRIVATE.value


@pytest.mark.asyncio
async def test_create_local_activity_reply(db_session, regular_user):
    """Replies link to their parent activity."""
    track = await _make_track(db_session, regular_user)
    parent = await create_local_activity(
        db_session,
        entity_type="track",
        entity_id=track.id,
        activity_type="create",
        author=regular_user,
        visibility=Visibility.PUBLIC,
    )

    reply = await create_local_activity(
        db_session,
        entity_type="track",
        entity_id=track.id,
        activity_type="reply",
        author=regular_user,
        visibility=Visibility.PUBLIC,
        in_reply_to_activity_id=parent.id,
    )
    assert reply.in_reply_to_activity_id == parent.id


def test_activity_create_params_valid():
    """ActivityCreateParams validates a well-formed payload."""
    params = ActivityCreateParams(
        entity_type="track",
        entity_id="t1",
        activity_type="create",
        source_type="local",
        source_actor="https://local.example/users/alice",
        source_id="https://local.example/users/alice/objects/1",
        visibility="public",
    )
    assert params.visibility == Visibility.PUBLIC
    assert params.content_type == "text/plain"


def test_activity_create_params_invalid_entity_type():
    """ActivityCreateParams rejects unknown entity types."""
    with pytest.raises(ValidationError):
        ActivityCreateParams(
            entity_type="station",
            entity_id="t1",
            activity_type="create",
            source_type="local",
            source_actor="actor",
            source_id="src",
            visibility="public",
        )


def test_activity_create_params_invalid_activity_type():
    """ActivityCreateParams rejects unknown activity types."""
    with pytest.raises(ValidationError):
        ActivityCreateParams(
            entity_type="track",
            entity_id="t1",
            activity_type="bogus",
            source_type="local",
            source_actor="actor",
            source_id="src",
            visibility="public",
        )


@pytest.mark.asyncio
async def test_get_activity_unpublish_info_uses_sent_targets(db_session):
    """Only successfully delivered inboxes are returned for fan-out."""
    activity = _make_activity("track", "track-1")
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    activity.targets.append(ActivityTarget(inbox_url="https://b.example/inbox", state="pending"))
    activity.targets.append(ActivityTarget(inbox_url="https://c.example/inbox", state="failed"))
    activity.targets.append(ActivityTarget(inbox_url="https://d.example/inbox", state="skipped"))
    activity.targets.append(ActivityTarget(inbox_url="https://e.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    info = await deletion.get_activity_unpublish_info(db_session, activity)

    assert info.activity_id == str(activity.id)
    assert info.source_id == activity.source_id
    assert info.actor_url == activity.source_actor
    assert sorted(info.inboxes) == ["https://a.example/inbox", "https://e.example/inbox"]


@pytest.mark.asyncio
async def test_cascade_delete_entity_local_soft_deletes_and_federates(db_session, regular_user, monkeypatch):
    """Local activities are retracted and a Tombstone is sent to sent inboxes."""
    regular_user.actor_url = "https://local.example/users/regular"
    regular_user.private_key_pem = "private-key"
    track = await _make_track(db_session, regular_user)

    activity = _make_activity(
        "track",
        track.id,
        source_actor=regular_user.actor_url,
        owner_user_id=regular_user.id,
    )
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    activity.targets.append(ActivityTarget(inbox_url="https://b.example/inbox", state="sent"))
    activity.targets.append(ActivityTarget(inbox_url="https://c.example/inbox", state="failed"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    retracted = await deletion.cascade_delete_entity(db_session, "track", track.id)
    await db_session.flush()

    assert len(retracted) == 1
    assert retracted[0].activity_id == str(activity.id)
    assert activity.deleted_at is not None

    # The track row still exists; only the activity was processed.
    assert await db_session.get(Track, track.id) is not None

    assert deliver.delay.call_count == 2
    for call in deliver.delay.call_args_list:
        payload, inbox, key_id, key = call.args
        assert payload["type"] == "Delete"
        assert payload["actor"] == regular_user.actor_url
        assert payload["object"]["type"] == "Tombstone"
        assert payload["object"]["id"] == activity.source_id
        assert inbox in {"https://a.example/inbox", "https://b.example/inbox"}
        assert key_id == f"{regular_user.actor_url}#main-key"
        assert key == "private-key"


@pytest.mark.asyncio
async def test_cascade_delete_entity_remote_hard_deletes(db_session, monkeypatch):
    """Remote activities are removed outright without fan-out."""
    track = await _make_track(db_session, None)

    activity = _make_activity(
        "track",
        track.id,
        source_type="remote",
        source_actor="https://remote.example/users/bob",
        source_id="https://remote.example/users/bob/objects/9",
    )
    activity.mentions.append(ActivityMention(handle="@alice"))
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()
    activity_id = activity.id

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    retracted = await deletion.cascade_delete_entity(db_session, "track", track.id)
    await db_session.flush()

    assert retracted == []
    deliver.delay.assert_not_called()
    assert await db_session.get(Activity, activity_id) is None
    assert (
        await db_session.scalar(
            select(func.count(ActivityMention.id)).where(ActivityMention.activity_id == activity_id)
        )
        == 0
    )
    assert (
        await db_session.scalar(select(func.count(ActivityTarget.id)).where(ActivityTarget.activity_id == activity_id))
        == 0
    )


@pytest.mark.asyncio
async def test_cascade_delete_entity_skips_already_retracted(db_session, regular_user, monkeypatch):
    """Already soft-deleted local activities are not processed again."""
    track = await _make_track(db_session, regular_user)
    activity = _make_activity("track", track.id, owner_user_id=regular_user.id)
    db_session.add(activity)
    await db_session.flush()
    activity.deleted_at = func.now()
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    retracted = await deletion.cascade_delete_entity(db_session, "track", track.id)

    assert retracted == []
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_cascade_delete_entity_without_owner_key_still_retracts(db_session, monkeypatch):
    """Local activities are retracted even when delivery cannot be signed."""
    track = await _make_track(db_session, None)

    activity = _make_activity("track", track.id, owner_user_id=None)
    activity.targets.append(ActivityTarget(inbox_url="https://a.example/inbox", state="sent"))
    db_session.add(activity)
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    retracted = await deletion.cascade_delete_entity(db_session, "track", track.id)
    await db_session.flush()

    assert len(retracted) == 1
    assert activity.deleted_at is not None
    deliver.delay.assert_not_called()


@pytest.mark.asyncio
async def test_delete_track_cascades_to_activities(db_session, regular_user, storage_service, monkeypatch):
    """Deleting a track retracts its local activities and drops remote ones."""
    track = await _make_track(db_session, regular_user)

    local = _make_activity(
        "track",
        track.id,
        source_actor="https://local.example/users/regular",
        owner_user_id=regular_user.id,
    )
    remote = _make_activity(
        "track",
        track.id,
        source_type="remote",
        source_actor="https://remote.example/users/bob",
        source_id="https://remote.example/users/bob/objects/9",
    )
    other = _make_activity(
        "track",
        "other-track",
        source_id="https://local.example/users/alice/objects/2",
    )
    db_session.add_all([local, remote, other])
    await db_session.flush()

    deliver = MagicMock()
    monkeypatch.setattr("songhive.tasks.federation.deliver_activity", deliver)

    await deletion.delete_track(db_session, storage_service, str(track.id))
    await db_session.flush()

    assert await db_session.get(Track, track.id) is None
    assert local.deleted_at is not None
    assert await db_session.get(Activity, remote.id) is None
    # Activities for other entities are untouched.
    assert other.deleted_at is None
