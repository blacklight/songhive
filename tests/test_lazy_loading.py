"""
Regression tests for relationship lazy-loading strategy.

Large collection relationships (``Library.tracks``, ``Playlist.tracks``,
``Album.tracks``, ``Artist.tracks``/``albums``, ``ExternalLibrary.library``
and friends) use ``lazy="raise"`` so plain entity queries never pull an
unbounded object graph — the pathological cascade that made
``select(ExternalLibrary)`` materialise every track in the library (and each
track's ~10 eager relationships) on the 5-second watchdog poll.  Call sites
that actually need the data opt in with ``selectinload`` or
``session.refresh(obj, ["rel"])``.
"""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import selectinload

from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.external_item import ExternalItem
from songhive.models.external_library import ExternalLibrary
from songhive.models.external_track import ExternalTrack
from songhive.models.genre import Genre, GenreTrack
from songhive.models.library import Library
from songhive.models.library_track import LibraryTrack
from songhive.models.playlist import Playlist, PlaylistTrack
from songhive.models.podcast import Podcast
from songhive.models.tag import Tag, TagTrack
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.genres import delete_genre_globally
from songhive.services.tags import delete_tag_globally


@pytest.fixture
async def library_with_tracks(db_session):
    """A library, an artist, and tracks linked to the library."""
    user = User(username="lazyuser", email="lazy@example.com", password_hash="x")
    artist = Artist(name="Lazy Artist")
    library = Library(name="Lazy Library", owner_id=user.id)
    db_session.add_all([user, artist, library])
    await db_session.flush()

    tracks = [Track(title=f"Track {i}", artist_id=artist.id, duration=10) for i in range(5)]
    db_session.add_all(tracks)
    await db_session.flush()
    db_session.add_all([LibraryTrack(library_id=library.id, track_id=t.id) for t in tracks])
    await db_session.commit()
    return library, artist, tracks


@pytest.mark.asyncio
async def test_library_tracks_not_eagerly_loaded(db_session, library_with_tracks):
    """select(Library) must not pull the track collection."""
    library, _, tracks = library_with_tracks
    result = await db_session.execute(select(Library).where(Library.id == library.id))
    lib = result.scalar_one()

    assert "tracks" in inspect(lib).unloaded
    with pytest.raises(InvalidRequestError):
        _ = lib.tracks

    # Explicit refresh still loads the relationship on demand.
    await db_session.refresh(lib, ["tracks"])
    assert len(lib.tracks) == len(tracks)


@pytest.mark.asyncio
async def test_external_library_loads_no_relationships(db_session, library_with_tracks):
    """The watchdog scenario: select(ExternalLibrary) stays a single-row load."""
    library, _, _ = library_with_tracks
    ext = ExternalLibrary(
        library_id=library.id,
        provider_type="local",
        enabled=True,
        config="{}",
        scope="admin",
        created_by_id=library.owner_id,
    )
    db_session.add(ext)
    await db_session.commit()

    result = await db_session.execute(select(ExternalLibrary).where(ExternalLibrary.id == ext.id))
    row = result.scalar_one()

    assert "library" in inspect(row).unloaded
    assert "external_items" in inspect(row).unloaded
    assert "external_tracks" in inspect(row).unloaded
    with pytest.raises(InvalidRequestError):
        _ = row.library


@pytest.mark.asyncio
async def test_external_library_explicit_selectinload(db_session, library_with_tracks):
    """Call sites that need the parent library opt in with selectinload."""
    library, _, _ = library_with_tracks
    ext = ExternalLibrary(
        library_id=library.id,
        provider_type="local",
        enabled=True,
        config="{}",
        scope="admin",
        created_by_id=library.owner_id,
    )
    db_session.add(ext)
    await db_session.commit()

    result = await db_session.execute(
        select(ExternalLibrary).options(selectinload(ExternalLibrary.library)).where(ExternalLibrary.id == ext.id)
    )
    row = result.scalar_one()
    assert row.library is not None
    assert row.library.id == library.id
    # The parent library's own large collections stay unloaded.
    assert "tracks" in inspect(row.library).unloaded


@pytest.mark.asyncio
async def test_playlist_tracks_not_eagerly_loaded(db_session, library_with_tracks):
    """select(Playlist) leaves its track list unloaded."""
    library, _, tracks = library_with_tracks
    playlist = Playlist(name="Lazy Playlist", owner_id=library.owner_id)
    db_session.add(playlist)
    await db_session.flush()
    for i, t in enumerate(tracks):
        db_session.add(PlaylistTrack(playlist_id=playlist.id, track_id=t.id, position=i))
    await db_session.commit()

    result = await db_session.execute(select(Playlist).where(Playlist.id == playlist.id))
    row = result.scalar_one()
    assert "tracks" in inspect(row).unloaded
    with pytest.raises(InvalidRequestError):
        _ = row.tracks


@pytest.mark.asyncio
async def test_artist_and_album_collections_not_eagerly_loaded(db_session, library_with_tracks):
    """Artist.albums/tracks and Album.tracks stay unloaded on plain selects."""
    _, artist, tracks = library_with_tracks
    album = Album(title="Lazy Album", artist_id=artist.id)
    db_session.add(album)
    await db_session.flush()
    tracks[0].album_id = album.id
    await db_session.commit()

    result = await db_session.execute(select(Artist).where(Artist.id == artist.id))
    art = result.scalar_one()
    assert "albums" in inspect(art).unloaded
    assert "tracks" in inspect(art).unloaded

    result = await db_session.execute(select(Album).where(Album.id == album.id))
    alb = result.scalar_one()
    assert "tracks" in inspect(alb).unloaded
    with pytest.raises(InvalidRequestError):
        _ = alb.tracks


@pytest.mark.asyncio
async def test_tag_genre_collections_not_eagerly_loaded(db_session, library_with_tracks):
    """Tag and Genre association collections stay unloaded."""
    _, _, tracks = library_with_tracks
    tag = Tag(name="lazytag")
    genre = Genre(name="lazygenre")
    db_session.add_all([tag, genre])
    await db_session.flush()
    db_session.add(TagTrack(tag_id=tag.id, track_id=tracks[0].id))
    db_session.add(GenreTrack(genre_id=genre.id, track_id=tracks[0].id))
    await db_session.commit()

    tag_row = (await db_session.execute(select(Tag).where(Tag.id == tag.id))).scalar_one()
    assert "tracks" in inspect(tag_row).unloaded

    genre_row = (await db_session.execute(select(Genre).where(Genre.id == genre.id))).scalar_one()
    assert "tracks" in inspect(genre_row).unloaded
    assert "albums" in inspect(genre_row).unloaded


@pytest.mark.asyncio
async def test_podcast_collections_not_eagerly_loaded(db_session):
    """Podcast.episodes/subscriptions stay unloaded on plain selects."""
    podcast = Podcast(feed_url="https://example.com/feed.xml", title="Lazy Pod")
    db_session.add(podcast)
    await db_session.commit()

    row = (await db_session.execute(select(Podcast).where(Podcast.id == podcast.id))).scalar_one()
    assert "episodes" in inspect(row).unloaded
    assert "subscriptions" in inspect(row).unloaded


@pytest.mark.asyncio
async def test_delete_tag_removes_associations(db_session, library_with_tracks):
    """delete_tag_globally cleans association rows even without DB FK cascade."""
    _, _, tracks = library_with_tracks
    tag = Tag(name="deletetag")
    db_session.add(tag)
    await db_session.flush()
    db_session.add(TagTrack(tag_id=tag.id, track_id=tracks[0].id))
    await db_session.commit()

    deleted = await delete_tag_globally(db_session, "deletetag")
    await db_session.commit()
    assert deleted is not None

    remaining = await db_session.execute(select(TagTrack).where(TagTrack.tag_id == tag.id))
    assert remaining.scalars().all() == []
    assert (await db_session.execute(select(Tag).where(Tag.id == tag.id))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_genre_removes_associations(db_session, library_with_tracks):
    """delete_genre_globally cleans association rows even without DB FK cascade."""
    _, _, tracks = library_with_tracks
    genre = Genre(name="deletegenre")
    db_session.add(genre)
    await db_session.flush()
    db_session.add(GenreTrack(genre_id=genre.id, track_id=tracks[0].id))
    await db_session.commit()

    deleted = await delete_genre_globally(db_session, "deletegenre")
    await db_session.commit()
    assert deleted is not None

    remaining = await db_session.execute(select(GenreTrack).where(GenreTrack.genre_id == genre.id))
    assert remaining.scalars().all() == []
    assert (await db_session.execute(select(Genre).where(Genre.id == genre.id))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_external_library_orm_delete_skips_child_load(db_session, library_with_tracks):
    """session.delete(ExternalLibrary) must not load the de-eagered collections.

    Child rows are removed by ``ON DELETE CASCADE`` (or explicit bulk deletes
    on backends without FK enforcement, as in ``services.deletion``); the
    ``passive_deletes`` backrefs keep the unit of work from touching
    ``lazy="raise"`` collections.
    """
    library, _, tracks = library_with_tracks
    ext = ExternalLibrary(
        library_id=library.id,
        provider_type="local",
        enabled=True,
        config="{}",
        scope="admin",
        created_by_id=library.owner_id,
    )
    db_session.add(ext)
    await db_session.flush()
    db_session.add(
        ExternalTrack(
            external_library_id=str(ext.id),
            track_id=tracks[0].id,
            provider_key="k1",
        )
    )
    db_session.add(
        ExternalItem(
            external_library_id=str(ext.id),
            kind="track",
            provider_key="k1",
            track_id=tracks[0].id,
        )
    )
    await db_session.commit()

    await db_session.delete(ext)
    await db_session.commit()

    gone = await db_session.execute(select(ExternalLibrary).where(ExternalLibrary.id == ext.id))
    assert gone.scalar_one_or_none() is None
