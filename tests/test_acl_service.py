"""
ACL service unit tests.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import sharing
from songhive.services.acl import can_access, can_manage, filter_accessible_track_ids


async def _make_artist(session, name: str = "Test Artist") -> Artist:
    """Create and persist a test artist."""
    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _make_file(
    session,
    owner: User | None = None,
    visibility: str = Visibility.PRIVATE.value,
    seed: bytes | None = None,
) -> StoredFile:
    """Create and persist a test stored file."""
    if seed is None:
        seed = secrets.token_bytes(16)
    sha = hashlib.sha256(seed).hexdigest()
    stored_file = StoredFile(
        storage_path=f"files/{sha[:2]}/{sha[2:4]}/{sha}",
        storage_backend="local",
        content_type="audio/mpeg",
        size=len(seed),
        sha256=sha,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(stored_file)
    await session.flush()
    return stored_file


async def _make_track(
    session,
    owner: User | None,
    visibility: str = Visibility.PRIVATE.value,
    audio_file: StoredFile | None = None,
) -> Track:
    """Create and persist a test track."""
    artist = await _make_artist(session)
    track = Track(
        title="Test Track",
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
        audio_file_id=audio_file.id if audio_file is not None else None,
    )
    session.add(track)
    await session.flush()
    return track


async def _make_album(
    session,
    owner: User | None,
    visibility: str = Visibility.PRIVATE.value,
    cover_file: StoredFile | None = None,
) -> Album:
    """Create and persist a test album."""
    artist = await _make_artist(session)
    album = Album(
        title="Test Album",
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
        cover_file_id=cover_file.id if cover_file is not None else None,
    )
    session.add(album)
    await session.flush()
    return album


@pytest.mark.asyncio
async def test_can_access_public_track_anonymous(db_session, regular_user):
    """Anonymous users can access public tracks."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PUBLIC.value)
    assert await can_access(db_session, None, "track", track.id) is True


@pytest.mark.asyncio
async def test_can_access_public_track_authenticated(db_session, regular_user):
    """Authenticated users can access public tracks."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PUBLIC.value)
    assert await can_access(db_session, regular_user, "track", track.id) is True


@pytest.mark.asyncio
async def test_can_access_private_track_owner(db_session, regular_user):
    """Owners can access their private tracks."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    assert await can_access(db_session, regular_user, "track", track.id) is True


@pytest.mark.asyncio
async def test_can_access_private_track_other_user(db_session, regular_user, make_user):
    """Other authenticated users cannot access a private track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    other_user = await make_user("other", email_verified=True)
    assert await can_access(db_session, other_user, "track", track.id) is False


@pytest.mark.asyncio
async def test_can_access_private_track_anonymous(db_session, regular_user):
    """Anonymous users cannot access a private track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    assert await can_access(db_session, None, "track", track.id) is False


@pytest.mark.asyncio
async def test_can_access_local_track_authenticated(db_session, regular_user, make_user):
    """Any authenticated user can access a local track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.LOCAL.value)
    other_user = await make_user("other", email_verified=True)
    assert await can_access(db_session, other_user, "track", track.id) is True


@pytest.mark.asyncio
async def test_can_access_local_track_anonymous(db_session, regular_user):
    """Anonymous users cannot access a local track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.LOCAL.value)
    assert await can_access(db_session, None, "track", track.id) is False


@pytest.mark.asyncio
async def test_can_access_admin_override(db_session, regular_user, admin_user):
    """Admins can access any track, including private ones."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    assert await can_access(db_session, admin_user, "track", track.id) is True


@pytest.mark.asyncio
async def test_can_access_ownerless_local_track(db_session, make_user):
    """Ownerless local items are visible to authenticated users but not anonymous."""
    track = await _make_track(db_session, owner=None, visibility=Visibility.LOCAL.value)
    user = await make_user("authenticated", email_verified=True)
    assert await can_access(db_session, user, "track", track.id) is True
    assert await can_access(db_session, None, "track", track.id) is False


@pytest.mark.asyncio
async def test_can_access_share_grant(db_session, regular_user, make_user):
    """A user with a share grant can access a private item."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    other_user = await make_user("shared", email_verified=True)

    assert await can_access(db_session, other_user, "track", track.id) is False

    await sharing.create_share_grant(db_session, "track", track.id, other_user.id, created_by=regular_user.id)
    assert await can_access(db_session, other_user, "track", track.id) is True


@pytest.mark.asyncio
async def test_can_access_share_token(db_session, regular_user):
    """A valid share token grants anonymous access to a private item."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    token, raw = await sharing.create_share_token(db_session, "track", track.id, created_by=regular_user.id)

    assert await can_access(db_session, None, "track", track.id) is False
    assert await can_access(db_session, None, "track", track.id, share_token=raw) is True

    await sharing.revoke_share_token(db_session, token.id)
    assert await can_access(db_session, None, "track", track.id, share_token=raw) is False


@pytest.mark.asyncio
async def test_can_access_expired_share_token(db_session, regular_user):
    """An expired share token does not grant access."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    _, raw = await sharing.create_share_token(
        db_session, "track", track.id, created_by=regular_user.id, expires_at=past
    )

    assert await can_access(db_session, None, "track", track.id, share_token=raw) is False


@pytest.mark.asyncio
async def test_can_access_wrong_share_token(db_session, regular_user):
    """A random token does not grant access."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    assert await can_access(db_session, None, "track", track.id, share_token="not-a-token") is False


@pytest.mark.asyncio
async def test_can_access_derived_file_public_track(db_session, regular_user):
    """An anonymous user can access a private file through a public track."""
    file = await _make_file(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    await _make_track(
        db_session,
        owner=regular_user,
        visibility=Visibility.PUBLIC.value,
        audio_file=file,
    )

    assert await can_access(db_session, None, "file", file.id) is True


@pytest.mark.asyncio
async def test_can_access_derived_file_private_track(db_session, regular_user):
    """A private file is inaccessible when its owning track is private."""
    file = await _make_file(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    await _make_track(
        db_session,
        owner=regular_user,
        visibility=Visibility.PRIVATE.value,
        audio_file=file,
    )

    assert await can_access(db_session, None, "file", file.id) is False


@pytest.mark.asyncio
async def test_can_access_derived_file_via_token(db_session, regular_user):
    """A token for a track also grants access to the track's audio file."""
    file = await _make_file(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    track = await _make_track(
        db_session,
        owner=regular_user,
        visibility=Visibility.PRIVATE.value,
        audio_file=file,
    )
    _, raw = await sharing.create_share_token(db_session, "track", track.id, created_by=regular_user.id)

    assert await can_access(db_session, None, "file", file.id, share_token=raw) is True
    assert await can_access(db_session, None, "file", file.id) is False


@pytest.mark.asyncio
async def test_can_access_derived_file_public_album(db_session, regular_user):
    """An anonymous user can access a private file through a public album cover."""
    file = await _make_file(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    await _make_album(
        db_session,
        owner=regular_user,
        visibility=Visibility.PUBLIC.value,
        cover_file=file,
    )

    assert await can_access(db_session, None, "file", file.id) is True


@pytest.mark.asyncio
async def test_can_access_derived_file_private_album(db_session, regular_user):
    """A private file is inaccessible when its owning album is private."""
    file = await _make_file(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    await _make_album(
        db_session,
        owner=regular_user,
        visibility=Visibility.PRIVATE.value,
        cover_file=file,
    )

    assert await can_access(db_session, None, "file", file.id) is False


@pytest.mark.asyncio
async def test_can_manage_owner(db_session, regular_user):
    """An owner can manage their track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    assert await can_manage(db_session, regular_user, "track", track.id) is True


@pytest.mark.asyncio
async def test_can_manage_admin(db_session, regular_user, admin_user):
    """An admin can manage another user's track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    assert await can_manage(db_session, admin_user, "track", track.id) is True


@pytest.mark.asyncio
async def test_can_manage_other_user(db_session, regular_user, make_user):
    """A non-owner cannot manage a private track."""
    track = await _make_track(db_session, owner=regular_user, visibility=Visibility.PRIVATE.value)
    other_user = await make_user("other", email_verified=True)
    assert await can_manage(db_session, other_user, "track", track.id) is False


@pytest.mark.asyncio
async def test_can_manage_ownerless(db_session, regular_user):
    """An ownerless item is not manageable."""
    track = await _make_track(db_session, owner=None, visibility=Visibility.PRIVATE.value)
    assert await can_manage(db_session, regular_user, "track", track.id) is False


@pytest.mark.asyncio
async def test_can_manage_nonexistent(db_session, regular_user):
    """Managing a missing item returns False."""
    assert await can_manage(db_session, regular_user, "track", "missing-id") is False


@pytest.mark.asyncio
async def test_can_access_unknown_item_type(db_session, regular_user):
    """Unknown item types raise ValueError."""
    with pytest.raises(ValueError):
        await can_access(db_session, regular_user, "unknown", "some-id")


@pytest.mark.asyncio
async def test_get_item_plural(db_session):
    """get_item_plural returns the plural path or None for unknown types."""
    from songhive.services.acl import get_item_plural

    assert get_item_plural("track") == "tracks"
    assert get_item_plural("library") == "libraries"
    assert get_item_plural("unknown") is None


@pytest.mark.asyncio
async def test_get_item_unknown_type(db_session):
    """get_item raises ValueError for unknown item types."""
    from songhive.services.acl import get_item

    with pytest.raises(ValueError):
        await get_item(db_session, "unknown", "some-id")


@pytest.mark.asyncio
async def test_apply_access_filter_admin_bypass(db_session, admin_user):
    """Admins bypass the ACL list filter."""
    from sqlalchemy import select

    from songhive.models.track import Track
    from songhive.services.acl import apply_access_filter

    stmt = select(Track)
    result = apply_access_filter(stmt, Track, admin_user, "track")
    assert result is stmt


@pytest.mark.asyncio
async def test_can_access_missing_item(db_session, regular_user):
    """can_access returns False for a missing item."""
    assert await can_access(db_session, regular_user, "track", "missing-id") is False


@pytest.mark.asyncio
async def test_can_manage_user_none(db_session):
    """None users cannot manage anything."""
    assert await can_manage(db_session, None, "track", "any-id") is False


@pytest.mark.asyncio
async def test_can_manage_unknown_item_type(db_session, regular_user):
    """can_manage raises ValueError for unknown item types."""
    with pytest.raises(ValueError):
        await can_manage(db_session, regular_user, "unknown", "some-id")


@pytest.mark.asyncio
async def test_audit_ownerless_private(db_session, regular_user, caplog):
    """audit_ownerless_private counts ownerless private stored files."""
    from songhive.models._enums import Visibility
    from songhive.models.stored_file import StoredFile
    from songhive.services.acl import audit_ownerless_private

    file = StoredFile(
        storage_path="files/aa/bb/cc",
        storage_backend="local",
        content_type="audio/mpeg",
        size=1,
        sha256="d" * 64,
        owner_id=None,
        visibility=Visibility.PRIVATE.value,
    )
    db_session.add(file)
    await db_session.flush()

    with caplog.at_level(logging.WARNING, logger="songhive.services.acl"):
        count = await audit_ownerless_private(db_session)
    assert count == 1
    assert "ownerless" in caplog.text.lower()


@pytest.mark.asyncio
async def test_filter_accessible_track_ids_honors_visibility(db_session, regular_user, other_user):
    """filter_accessible_track_ids returns only tracks the user may access."""
    public_track = await _make_track(db_session, regular_user, visibility=Visibility.PUBLIC.value)
    local_track = await _make_track(db_session, other_user, visibility=Visibility.LOCAL.value)
    private_track = await _make_track(db_session, other_user, visibility=Visibility.PRIVATE.value)

    track_ids = [str(t.id) for t in (public_track, local_track, private_track)]
    accessible = await filter_accessible_track_ids(db_session, regular_user, track_ids)
    assert accessible == {str(public_track.id), str(local_track.id)}


@pytest.mark.asyncio
async def test_filter_accessible_track_ids_empty_input(db_session, regular_user):
    """filter_accessible_track_ids returns an empty set for an empty input list."""
    assert await filter_accessible_track_ids(db_session, regular_user, []) == set()


@pytest.mark.asyncio
async def test_filter_accessible_track_ids_batches_queries(db_session, regular_user):
    """filter_accessible_track_ids splits large inputs into batched IN queries."""
    tracks = [await _make_track(db_session, regular_user, visibility=Visibility.PUBLIC.value) for _ in range(3)]
    track_ids = [str(t.id) for t in tracks]

    accessible = await filter_accessible_track_ids(db_session, regular_user, track_ids, batch_size=2)
    assert accessible == set(track_ids)
