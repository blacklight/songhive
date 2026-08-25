"""
Tests for adding tracks, albums and artists to a playlist.
"""

import pytest
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.audit_log import AuditLog
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.track import Track
from songhive.models.user import User


async def _make_artist(session, name: str = "Test Artist") -> Artist:
    """Create and persist a test artist."""
    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _make_album(
    session,
    artist: Artist,
    title: str = "Test Album",
    owner: User | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Album:
    """Create and persist a test album."""
    album = Album(
        title=title,
        artist_id=artist.id,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(album)
    await session.flush()
    return album


async def _make_track(
    session,
    artist: Artist,
    album: Album | None = None,
    title: str = "Test Track",
    track_number: int | None = None,
    owner: User | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Track:
    """Create and persist a test track."""
    track = Track(
        title=title,
        artist_id=artist.id,
        album_id=album.id if album is not None else None,
        track_number=track_number,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
    )
    session.add(track)
    await session.flush()
    return track


async def _make_playlist(
    session,
    owner: User,
    visibility: str = Visibility.PUBLIC.value,
) -> Playlist:
    """Create and persist a test playlist."""
    playlist = Playlist(name="Test Playlist", owner_id=owner.id, visibility=visibility)
    session.add(playlist)
    await session.flush()
    return playlist


@pytest.mark.asyncio
async def test_add_track_to_playlist(client, regular_user, db_session, auth_headers):
    """A track can be added to a playlist by its owner."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["added"] == 1
    assert data["track_ids"] == [str(track.id)]

    link = await db_session.scalar(
        select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == playlist.id,
            PlaylistTrack.track_id == str(track.id),
        )
    )
    assert link is not None
    assert link.position == 1


@pytest.mark.asyncio
async def test_add_album_to_playlist(client, regular_user, db_session, auth_headers):
    """Adding an album resolves to all of its accessible tracks."""
    artist = await _make_artist(db_session)
    album = await _make_album(db_session, artist, owner=regular_user)
    track_one = await _make_track(db_session, artist, album, title="Track One", track_number=1, owner=regular_user)
    track_two = await _make_track(db_session, artist, album, title="Track Two", track_number=2, owner=regular_user)
    playlist = await _make_playlist(db_session, regular_user)

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"album_id": str(album.id)},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["added"] == 2
    assert set(data["track_ids"]) == {str(track_one.id), str(track_two.id)}


@pytest.mark.asyncio
async def test_add_artist_to_playlist(client, regular_user, db_session, auth_headers):
    """Adding an artist resolves to all of their accessible tracks."""
    artist = await _make_artist(db_session)
    track_one = await _make_track(db_session, artist, title="Track One", owner=regular_user)
    track_two = await _make_track(db_session, artist, title="Track Two", owner=regular_user)
    playlist = await _make_playlist(db_session, regular_user)

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"artist_id": str(artist.id)},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["added"] == 2
    assert set(data["track_ids"]) == {str(track_one.id), str(track_two.id)}


@pytest.mark.asyncio
async def test_add_tracks_to_playlist_forbidden_for_non_owner(
    client, regular_user, other_user, db_session, auth_headers
):
    """A user that does not manage the playlist receives a 403."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(other_user),
        json={"track_ids": [str(track.id)]},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_add_inaccessible_tracks_to_playlist_are_skipped(
    client, regular_user, other_user, db_session, auth_headers
):
    """Tracks the user cannot access are not added to the playlist."""
    artist = await _make_artist(db_session)
    album = await _make_album(db_session, artist, owner=regular_user)
    accessible = await _make_track(db_session, artist, album, title="Accessible", track_number=1, owner=regular_user)
    _ = await _make_track(
        db_session,
        artist,
        album,
        title="Inaccessible",
        track_number=2,
        owner=other_user,
        visibility=Visibility.PRIVATE.value,
    )
    playlist = await _make_playlist(db_session, regular_user)

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"album_id": str(album.id)},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["added"] == 1
    assert data["track_ids"] == [str(accessible.id)]

    result = await db_session.execute(select(PlaylistTrack.track_id).where(PlaylistTrack.playlist_id == playlist.id))
    stored = set(result.scalars().all())
    assert stored == {accessible.id}


@pytest.mark.asyncio
async def test_add_tracks_to_playlist_creates_audit_log(client, regular_user, db_session, auth_headers):
    """Adding tracks writes a playlist_track.add AuditLog row."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )

    assert response.status_code == 201

    log = await db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "playlist_track.add",
            AuditLog.actor_id == regular_user.id,
            AuditLog.target_id == playlist.id,
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert log is not None
    assert log.target_type == "playlist"
    assert log.details["count"] == 1
    assert log.details["track_ids"] == [str(track.id)]
