"""
Tests for the music service list/count/fetch helpers.
"""

from typing import List

import pytest

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.radio import Radio
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services import music
from songhive.services.genres import set_genres_for_entity, split_genre_string


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
    release_year: int | None = 2020,
    owner: User | None = None,
    visibility: str = Visibility.PUBLIC.value,
) -> Album:
    """Create and persist a test album."""
    album = Album(
        title=title,
        artist_id=artist.id,
        release_year=release_year,
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
    genre: str | None = None,
    owner: User | None = None,
    visibility: str = Visibility.PRIVATE.value,
) -> Track:
    """Create and persist a test track."""
    track = Track(
        title=title,
        artist_id=artist.id,
        album_id=album.id if album is not None else None,
        owner_id=owner.id if owner is not None else None,
        visibility=visibility,
        genre=genre,
    )
    session.add(track)
    await session.flush()

    if genre:
        genre_names = split_genre_string(genre)
        if genre_names:
            await set_genres_for_entity(session, "track", str(track.id), genre_names)

    return track


async def _make_library(session, owner: User, visibility: str = Visibility.PUBLIC.value) -> Library:
    """Create and persist a test library."""
    library = Library(name="Test Library", owner_id=owner.id, visibility=visibility)
    session.add(library)
    await session.flush()
    return library


async def _add_to_library(session, library: Library, track: Track, user: User | None = None):
    """Add a track to a library."""
    link = LibraryTrack(
        library_id=library.id,
        track_id=track.id,
        added_by_id=user.id if user is not None else None,
    )
    session.add(link)
    await session.flush()


@pytest.mark.asyncio
async def test_get_artist(db_session):
    """get_artist returns the artist by ID."""
    artist = await _make_artist(db_session, "Unique Artist")
    result = await music.get_artist(db_session, artist.id)
    assert result is not None
    assert result.id == artist.id
    assert result.name == artist.name


@pytest.mark.asyncio
async def test_get_artist_missing(db_session):
    """get_artist returns None for a missing ID."""
    assert await music.get_artist(db_session, "missing-id") is None


@pytest.mark.asyncio
async def test_list_and_count_artists_with_query(db_session):
    """list_artists and count_artists honour the query filter."""
    await _make_artist(db_session, "Alpha Artist")
    await _make_artist(db_session, "Beta Band")

    all_artists = await music.list_artists(db_session)
    assert len(all_artists) == 2
    assert await music.count_artists(db_session) == 2

    filtered = await music.list_artists(db_session, query="alpha")
    assert len(filtered) == 1
    assert filtered[0].name == "Alpha Artist"
    assert await music.count_artists(db_session, query="alpha") == 1


@pytest.mark.asyncio
async def test_list_and_count_albums_with_filters(db_session, regular_user):
    """list_albums and count_albums honour query, artist and year filters."""
    artist_a = await _make_artist(db_session, "Artist A")
    artist_b = await _make_artist(db_session, "Artist B")

    album_a = await _make_album(db_session, artist_a, "Alpha Album", 2020, owner=regular_user)
    await _make_album(db_session, artist_a, "Another Album", 2015, owner=regular_user)
    await _make_album(db_session, artist_b, "Beta Album", 2022, owner=regular_user)

    assert len(await music.list_albums(db_session, user=regular_user)) == 3

    by_query = await music.list_albums(db_session, query="alpha", user=regular_user)
    assert len(by_query) == 1
    assert await music.count_albums(db_session, query="alpha", user=regular_user) == 1

    by_artist = await music.list_albums(db_session, artist_id=artist_a.id, user=regular_user)
    assert len(by_artist) == 2

    by_year = await music.list_albums(
        db_session,
        year_from=2018,
        year_to=2021,
        user=regular_user,
    )
    assert len(by_year) == 1
    assert by_year[0].id == album_a.id

    combined = await music.list_albums(
        db_session,
        artist_id=artist_a.id,
        year_from=2016,
        year_to=2025,
        user=regular_user,
    )
    assert len(combined) == 1
    assert combined[0].id == album_a.id


@pytest.mark.asyncio
async def test_list_and_count_tracks_with_filters(db_session, regular_user):
    """list_tracks and count_tracks honour all filter combinations."""
    artist = await _make_artist(db_session, "Search Artist")
    album = await _make_album(db_session, artist, "Search Album", 2021, owner=regular_user)
    library = await _make_library(db_session, regular_user)

    track1 = await _make_track(
        db_session,
        artist,
        album,
        title="Alpha Song",
        genre="rock",
        owner=regular_user,
    )
    await _make_track(
        db_session,
        artist,
        None,
        title="Beta Song",
        genre="pop",
        owner=regular_user,
    )
    await _add_to_library(db_session, library, track1, regular_user)

    # Search query (non-PostgreSQL ILIKE branch)
    by_query, _ = await music.list_tracks(db_session, query="alpha", user=regular_user)
    assert len(by_query) == 1
    assert by_query[0].id == track1.id
    assert await music.count_tracks(db_session, query="alpha", user=regular_user) == 1

    by_artist, _ = await music.list_tracks(db_session, artist_id=artist.id, user=regular_user)
    assert len(by_artist) == 2

    by_album, _ = await music.list_tracks(db_session, album_id=album.id, user=regular_user)
    assert len(by_album) == 1
    assert by_album[0].id == track1.id

    by_genre, _ = await music.list_tracks(db_session, genre="rock", user=regular_user)
    assert len(by_genre) == 1
    assert by_genre[0].id == track1.id

    by_library, _ = await music.list_tracks(db_session, library_id=library.id, user=regular_user)
    assert len(by_library) == 1
    assert by_library[0].id == track1.id
    assert await music.count_tracks(db_session, library_id=library.id, user=regular_user) == 1

    # Year range covering the album and the track with no album
    by_year, _ = await music.list_tracks(
        db_session,
        year_from=2020,
        year_to=2022,
        user=regular_user,
    )
    assert len(by_year) == 2

    # Search query combined with year range
    by_query_year, _ = await music.list_tracks(
        db_session,
        query="song",
        year_from=2020,
        year_to=2022,
        user=regular_user,
    )
    assert len(by_query_year) == 2


@pytest.mark.asyncio
async def test_list_tracks_by_genre_uses_associations(db_session, regular_user):
    """list_tracks filters by the normalised GenreTrack associations, not the
    raw free-text ``track.genre`` column."""
    artist = await _make_artist(db_session, "Genre Artist")

    # Track genre is stored as a raw string; the genre association is the
    # normalised source of truth for filtering.
    track = await _make_track(
        db_session,
        artist,
        title="Genre Song",
        genre="Rock, Pop",
        owner=regular_user,
        visibility=Visibility.PUBLIC.value,
    )

    by_lower, _ = await music.list_tracks(db_session, genre="rock", user=regular_user)
    assert len(by_lower) == 1
    assert by_lower[0].id == track.id

    by_upper, _ = await music.list_tracks(db_session, genre="Rock", user=regular_user)
    assert len(by_upper) == 1
    assert by_upper[0].id == track.id

    by_other, _ = await music.list_tracks(db_session, genre="pop", user=regular_user)
    assert len(by_other) == 1
    assert by_other[0].id == track.id

    by_missing, _ = await music.list_tracks(db_session, genre="jazz", user=regular_user)
    assert by_missing == []
    assert await music.count_tracks(db_session, genre="rock", user=regular_user) == 1


@pytest.mark.asyncio
async def test_get_track(db_session, regular_user):
    """get_track returns a track by ID."""
    artist = await _make_artist(db_session)
    track = await _make_track(db_session, artist, title="Fetch Me", owner=regular_user)
    result = await music.get_track(db_session, track.id)
    assert result is not None
    assert result.id == track.id
    assert result.title == "Fetch Me"


@pytest.mark.asyncio
async def test_get_track_missing(db_session):
    """get_track returns None for a missing ID."""
    assert await music.get_track(db_session, "missing-id") is None


@pytest.mark.asyncio
async def test_list_and_count_library_tracks(db_session, regular_user, make_user):
    """list_library_tracks and count_library_tracks return a library's tracks."""
    owner = await make_user("owner", email_verified=True)
    library = await _make_library(db_session, owner)
    artist = await _make_artist(db_session)

    track1 = await _make_track(db_session, artist, title="Lib One", owner=owner)
    track2 = await _make_track(db_session, artist, title="Lib Two", owner=owner)
    await _make_track(db_session, artist, title="Lib Three", owner=owner)
    await _add_to_library(db_session, library, track1, owner)
    await _add_to_library(db_session, library, track2, owner)

    # Owner sees all
    rows = await music.list_library_tracks(db_session, library.id, user=owner)
    assert len(rows) == 2
    assert await music.count_library_tracks(db_session, library.id, user=owner) == 2

    # Pagination
    paged = await music.list_library_tracks(db_session, library.id, user=owner, limit=1, offset=0)
    assert len(paged) == 1

    # Admin bypass
    admin = await make_user("lib_admin", role="admin", email_verified=True)
    admin_rows = await music.list_library_tracks(db_session, library.id, user=admin)
    assert len(admin_rows) == 2

    # Unrelated user cannot see the private track
    other = await make_user("lib_other", email_verified=True)
    other_rows = await music.list_library_tracks(db_session, library.id, user=other)
    assert len(other_rows) == 0
    assert await music.count_library_tracks(db_session, library.id, user=other) == 0

    # Adding a third track not in the library does not affect results
    not_added_rows = await music.list_library_tracks(
        db_session,
        library.id,
        user=owner,
        limit=100,
        offset=0,
    )
    assert len(not_added_rows) == 2


@pytest.mark.asyncio
async def test_get_playlist_and_radio(db_session, regular_user):
    """get_playlist and get_radio return the requested rows."""
    playlist = Playlist(name="My Playlist", owner_id=regular_user.id)
    radio = Radio(name="My Radio", owner_id=regular_user.id)
    db_session.add_all([playlist, radio])
    await db_session.flush()

    assert (await music.get_playlist(db_session, playlist.id)) is not None
    assert (await music.get_playlist(db_session, "missing")) is None

    assert (await music.get_radio(db_session, radio.id)) is not None
    assert (await music.get_radio(db_session, "missing")) is None


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


async def _add_playlist_tracks(
    session,
    playlist: Playlist,
    tracks: List[Track],
) -> None:
    """Add tracks to a playlist with explicit positions."""
    for i, track in enumerate(tracks, start=1):
        session.add(
            PlaylistTrack(
                playlist_id=playlist.id,
                track_id=track.id,
                position=i,
            )
        )
    await session.flush()


async def _playlist_track_ids(
    session,
    playlist: Playlist,
) -> List[str]:
    """Return track IDs for a playlist ordered by position."""
    from sqlalchemy import select

    result = await session.execute(
        select(PlaylistTrack.track_id).where(PlaylistTrack.playlist_id == playlist.id).order_by(PlaylistTrack.position)
    )
    return [str(row) for row in result.scalars().all()]


async def _playlist_positions(
    session,
    playlist: Playlist,
) -> List[int]:
    """Return positions for a playlist ordered by position."""
    from sqlalchemy import select

    result = await session.execute(
        select(PlaylistTrack.position).where(PlaylistTrack.playlist_id == playlist.id).order_by(PlaylistTrack.position)
    )
    return [int(row) for row in result.scalars().all()]


@pytest.mark.asyncio
async def test_remove_playlist_tracks_renormalizes_positions(db_session, regular_user):
    """Removing a track reassigns contiguous positions to the survivors."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 6)]
    playlist = await _make_playlist(db_session, regular_user)
    await _add_playlist_tracks(db_session, playlist, tracks)

    removed, _ = await music.remove_playlist_tracks(db_session, playlist.id, [tracks[2].id])
    assert removed == 1

    positions = await _playlist_positions(db_session, playlist)
    assert positions == [1, 2, 3, 4]

    order = await _playlist_track_ids(db_session, playlist)
    expected = [tracks[0].id, tracks[1].id, tracks[3].id, tracks[4].id]
    assert order == expected


@pytest.mark.asyncio
async def test_renormalize_is_idempotent(db_session, regular_user):
    """Renormalizing an already-contiguous playlist leaves positions unchanged."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 4)]
    playlist = await _make_playlist(db_session, regular_user)
    await _add_playlist_tracks(db_session, playlist, tracks)

    await music.renormalize_playlist_track_positions(db_session, playlist.id)
    assert await _playlist_positions(db_session, playlist) == [1, 2, 3]

    await music.renormalize_playlist_track_positions(db_session, playlist.id)
    assert await _playlist_positions(db_session, playlist) == [1, 2, 3]


@pytest.mark.asyncio
async def test_reorder_single_move_to_position(db_session, regular_user):
    """Moving a single track lands it at the requested 1-based rank."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 6)]
    playlist = await _make_playlist(db_session, regular_user)
    await _add_playlist_tracks(db_session, playlist, tracks)

    moved = await music.reorder_playlist_tracks(db_session, playlist.id, [tracks[3].id], 2)
    assert moved == [tracks[3].id]

    order = await _playlist_track_ids(db_session, playlist)
    expected = [tracks[0].id, tracks[3].id, tracks[1].id, tracks[2].id, tracks[4].id]
    assert order == expected
    assert await _playlist_positions(db_session, playlist) == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_reorder_split_move_preserves_relative_order(db_session, regular_user):
    """A multi-track move preserves the moved block's current relative order."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 6)]
    playlist = await _make_playlist(db_session, regular_user)
    await _add_playlist_tracks(db_session, playlist, tracks)

    moved = await music.reorder_playlist_tracks(
        db_session,
        playlist.id,
        [tracks[2].id, tracks[0].id],
        2,
    )
    assert moved == [tracks[2].id, tracks[0].id]

    order = await _playlist_track_ids(db_session, playlist)
    expected = [tracks[1].id, tracks[0].id, tracks[2].id, tracks[3].id, tracks[4].id]
    assert order == expected
    assert await _playlist_positions(db_session, playlist) == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_reorder_move_to_end(db_session, regular_user):
    """Omitting position moves the block to the end of the playlist."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 6)]
    playlist = await _make_playlist(db_session, regular_user)
    await _add_playlist_tracks(db_session, playlist, tracks)

    moved = await music.reorder_playlist_tracks(db_session, playlist.id, [tracks[1].id])
    assert moved == [tracks[1].id]

    order = await _playlist_track_ids(db_session, playlist)
    expected = [tracks[0].id, tracks[2].id, tracks[3].id, tracks[4].id, tracks[1].id]
    assert order == expected


@pytest.mark.asyncio
async def test_reorder_unknown_track_id_raises(db_session, regular_user):
    """Unknown track IDs raise ValueError."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title="Track 1", owner=regular_user)]
    playlist = await _make_playlist(db_session, regular_user)
    await _add_playlist_tracks(db_session, playlist, tracks)

    with pytest.raises(ValueError):
        await music.reorder_playlist_tracks(db_session, playlist.id, ["missing-id"], 1)


@pytest.mark.asyncio
async def test_reorder_leaves_contiguous_positions(db_session, regular_user):
    """Reordering always leaves contiguous 1-based positions."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 6)]
    playlist = await _make_playlist(db_session, regular_user)
    await _add_playlist_tracks(db_session, playlist, tracks)

    await music.reorder_playlist_tracks(db_session, playlist.id, [tracks[4].id, tracks[0].id], 1)
    positions = await _playlist_positions(db_session, playlist)
    assert positions == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_reorder_duplicate_track_occurrences(db_session, regular_user):
    """All occurrences of a duplicated track move together."""
    artist = await _make_artist(db_session)
    track_one = await _make_track(db_session, artist, title="Track One", owner=regular_user)
    track_two = await _make_track(db_session, artist, title="Track Two", owner=regular_user)
    track_three = await _make_track(db_session, artist, title="Track Three", owner=regular_user)
    playlist = await _make_playlist(db_session, regular_user)

    for i, track_id in enumerate([track_one.id, track_two.id, track_one.id, track_three.id], start=1):
        db_session.add(PlaylistTrack(playlist_id=playlist.id, track_id=track_id, position=i))
    await db_session.flush()

    moved = await music.reorder_playlist_tracks(db_session, playlist.id, [track_one.id], 2)
    assert moved == [track_one.id]

    order = await _playlist_track_ids(db_session, playlist)
    expected = [track_two.id, track_one.id, track_one.id, track_three.id]
    assert order == expected
    assert await _playlist_positions(db_session, playlist) == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_remove_playlist_tracks_renormalizes_first_and_last(db_session, regular_user):
    """Removing the first and last tracks renormalizes the remaining rows."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 5)]
    playlist = await _make_playlist(db_session, regular_user)
    await _add_playlist_tracks(db_session, playlist, tracks)

    await music.remove_playlist_tracks(db_session, playlist.id, [tracks[0].id])
    assert await _playlist_positions(db_session, playlist) == [1, 2, 3]
    assert await _playlist_track_ids(db_session, playlist) == [tracks[1].id, tracks[2].id, tracks[3].id]

    await music.remove_playlist_tracks(db_session, playlist.id, [tracks[3].id])
    assert await _playlist_positions(db_session, playlist) == [1, 2]
    assert await _playlist_track_ids(db_session, playlist) == [tracks[1].id, tracks[2].id]


@pytest.mark.asyncio
async def test_reorder_idempotent_when_already_in_place(db_session, regular_user):
    """Moving a track to its current position is a no-op."""
    artist = await _make_artist(db_session)
    tracks = [await _make_track(db_session, artist, title=f"Track {i}", owner=regular_user) for i in range(1, 4)]
    playlist = await _make_playlist(db_session, regular_user)
    await _add_playlist_tracks(db_session, playlist, tracks)

    moved = await music.reorder_playlist_tracks(db_session, playlist.id, [tracks[0].id], 1)
    assert moved == [tracks[0].id]
    assert await _playlist_track_ids(db_session, playlist) == [tracks[0].id, tracks[1].id, tracks[2].id]
    assert await _playlist_positions(db_session, playlist) == [1, 2, 3]
