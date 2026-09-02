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


@pytest.mark.asyncio
async def test_add_duplicate_track_to_playlist_rejected(client, regular_user, db_session, auth_headers):
    """Adding a track already in the playlist returns 409 with duplicate track IDs."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )

    assert response.status_code == 409
    data = response.json()
    assert data["detail"] == "Tracks already in playlist"
    assert data["track_ids"] == [str(track.id)]

    result = await db_session.execute(select(PlaylistTrack.track_id).where(PlaylistTrack.playlist_id == playlist.id))
    stored = list(result.scalars().all())
    assert stored == [track.id]


@pytest.mark.asyncio
async def test_add_duplicate_track_to_playlist_allowed(client, regular_user, db_session, auth_headers):
    """Adding a duplicate track succeeds when allow_duplicates is true."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)], "allow_duplicates": True},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["added"] == 1
    assert data["track_ids"] == [str(track.id)]

    result = await db_session.execute(select(PlaylistTrack.track_id).where(PlaylistTrack.playlist_id == playlist.id))
    stored = list(result.scalars().all())
    assert stored == [track.id, track.id]


@pytest.mark.asyncio
async def test_add_album_to_playlist_rejects_existing_duplicates(client, regular_user, db_session, auth_headers):
    """Adding an album with already-present tracks returns 409 by default."""
    artist = await _make_artist(db_session)
    album = await _make_album(db_session, artist, owner=regular_user)
    track = await _make_track(db_session, artist, album, title="Track One", track_number=1, owner=regular_user)
    playlist = await _make_playlist(db_session, regular_user)

    first = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert first.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"album_id": str(album.id)},
    )

    assert response.status_code == 409
    data = response.json()
    assert data["detail"] == "Tracks already in playlist"
    assert data["track_ids"] == [str(track.id)]


@pytest.mark.asyncio
async def test_list_playlist_tracks(client, regular_user, db_session, auth_headers):
    """Tracks can be listed for a playlist in order."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.get(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == str(track.id)
    assert response.headers["x-total-count"] == "1"


@pytest.mark.asyncio
async def test_remove_track_from_playlist(client, regular_user, db_session, auth_headers):
    """A track can be removed from a playlist by its owner."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/remove",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["removed"] == 1
    assert data["track_ids"] == [str(track.id)]

    link = await db_session.scalar(
        select(PlaylistTrack).where(
            PlaylistTrack.playlist_id == playlist.id,
            PlaylistTrack.track_id == str(track.id),
        )
    )
    assert link is None


@pytest.mark.asyncio
async def test_remove_tracks_from_playlist_preserves_input_order(client, regular_user, db_session, auth_headers):
    """Removing tracks from a playlist returns distinct IDs in the caller's input order."""
    artist = await _make_artist(db_session)
    track_one = await _make_track(db_session, artist, title="Track One", owner=regular_user)
    track_two = await _make_track(db_session, artist, title="Track Two", owner=regular_user)
    track_three = await _make_track(db_session, artist, title="Track Three", owner=regular_user)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track_one.id), str(track_two.id), str(track_three.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/remove",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track_three.id), str(track_one.id), str(track_three.id)]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["removed"] == 2
    assert data["track_ids"] == [str(track_three.id), str(track_one.id)]


@pytest.mark.asyncio
async def test_remove_tracks_from_playlist_forbidden_for_non_owner(
    client, regular_user, other_user, db_session, auth_headers
):
    """A user that does not manage the playlist cannot remove tracks."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/remove",
        headers=auth_headers(other_user),
        json={"track_ids": [str(track.id)]},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_remove_tracks_from_playlist_creates_audit_log(client, regular_user, db_session, auth_headers):
    """Removing tracks writes a playlist_track.remove AuditLog row."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/remove",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )

    assert response.status_code == 200

    log = await db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "playlist_track.remove",
            AuditLog.actor_id == regular_user.id,
            AuditLog.target_id == playlist.id,
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert log is not None
    assert log.target_type == "playlist"
    assert log.details["count"] == 1
    assert log.details["track_ids"] == [str(track.id)]


def _ordered_track_ids(response):
    """Return track IDs from a playlist track listing response."""
    return [track["id"] for track in response.json()]


@pytest.mark.asyncio
async def test_reorder_tracks_single_move(client, regular_user, db_session, auth_headers):
    """A single track can be moved to a specific position."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 6)]
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(t.id) for t in tracks]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/reorder",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(tracks[3].id)], "position": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["reordered"] is True
    assert data["count"] == 1
    assert data["track_ids"] == [str(tracks[3].id)]

    listing = client.get(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
    )
    assert listing.status_code == 200
    expected = [
        str(tracks[0].id),
        str(tracks[3].id),
        str(tracks[1].id),
        str(tracks[2].id),
        str(tracks[4].id),
    ]
    assert _ordered_track_ids(listing) == expected


@pytest.mark.asyncio
async def test_reorder_tracks_split_move(client, regular_user, db_session, auth_headers):
    """A multi-track move preserves the block's relative order."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 6)]
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(t.id) for t in tracks]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/reorder",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(tracks[2].id), str(tracks[0].id)], "position": 2},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2

    listing = client.get(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
    )
    expected = [
        str(tracks[1].id),
        str(tracks[0].id),
        str(tracks[2].id),
        str(tracks[3].id),
        str(tracks[4].id),
    ]
    assert _ordered_track_ids(listing) == expected


@pytest.mark.asyncio
async def test_reorder_tracks_move_to_end(client, regular_user, db_session, auth_headers):
    """Omitting position moves the block to the end."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 6)]
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(t.id) for t in tracks]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/reorder",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(tracks[1].id)]},
    )

    assert response.status_code == 200
    assert response.json()["track_ids"] == [str(tracks[1].id)]

    listing = client.get(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
    )
    expected = [
        str(tracks[0].id),
        str(tracks[2].id),
        str(tracks[3].id),
        str(tracks[4].id),
        str(tracks[1].id),
    ]
    assert _ordered_track_ids(listing) == expected


@pytest.mark.asyncio
async def test_reorder_tracks_forbidden(client, regular_user, other_user, db_session, auth_headers):
    """A non-manager cannot reorder a playlist's tracks."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user, visibility=Visibility.PUBLIC.value)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/reorder",
        headers=auth_headers(other_user),
        json={"track_ids": [str(track.id)], "position": 1},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reorder_tracks_missing_playlist(client, regular_user, auth_headers):
    """Reordering a missing playlist returns 404."""
    response = client.post(
        "/api/v1/playlists/missing/tracks/reorder",
        headers=auth_headers(regular_user),
        json={"track_ids": ["track-1"], "position": 1},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reorder_tracks_empty_track_ids(client, regular_user, db_session, auth_headers):
    """An empty track_ids list is rejected."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/reorder",
        headers=auth_headers(regular_user),
        json={"track_ids": [], "position": 1},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reorder_tracks_unknown_track_id(client, regular_user, db_session, auth_headers):
    """Unknown track IDs are rejected."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/reorder",
        headers=auth_headers(regular_user),
        json={"track_ids": ["missing-id"], "position": 1},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reorder_tracks_non_positive_position(client, regular_user, db_session, auth_headers):
    """Non-positive positions are rejected."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, owner=regular_user)
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/reorder",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(track.id)], "position": 0},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_reorder_tracks_creates_audit_log(client, regular_user, db_session, auth_headers):
    """Reordering tracks writes a playlist_track.reorder AuditLog row."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 4)]
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(t.id) for t in tracks]},
    )
    assert add.status_code == 201

    response = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/reorder",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(tracks[2].id)], "position": 1},
    )
    assert response.status_code == 200

    log = await db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "playlist_track.reorder",
            AuditLog.actor_id == regular_user.id,
            AuditLog.target_id == playlist.id,
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert log is not None
    assert log.target_type == "playlist"
    assert log.details["count"] == 1
    assert log.details["track_ids"] == [str(tracks[2].id)]
    assert log.details["position"] == 1


@pytest.mark.asyncio
async def test_remove_tracks_from_playlist_renormalizes(client, regular_user, db_session, auth_headers):
    """Removing a middle track renormalizes the remaining positions."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 6)]
    playlist = await _make_playlist(db_session, regular_user)

    add = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(t.id) for t in tracks]},
    )
    assert add.status_code == 201

    remove = client.post(
        f"/api/v1/playlists/{playlist.id}/tracks/remove",
        headers=auth_headers(regular_user),
        json={"track_ids": [str(tracks[2].id)]},
    )
    assert remove.status_code == 200

    listing = client.get(
        f"/api/v1/playlists/{playlist.id}/tracks",
        headers=auth_headers(regular_user),
    )
    expected = [
        str(tracks[0].id),
        str(tracks[1].id),
        str(tracks[3].id),
        str(tracks[4].id),
    ]
    assert _ordered_track_ids(listing) == expected
