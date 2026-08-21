"""
Music service: CRUD operations for artists, albums, tracks, playlists,
libraries, and radios.
"""

from typing import List, Optional, cast

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.album import Album
from ..models.artist import Artist
from ..models.library import Library
from ..models.library_track import LibraryTrack
from ..models.playlist import Playlist
from ..models.radio import Radio
from ..models.track import Track
from ..models.user import User
from .acl import apply_access_filter


async def list_artists(
    session: AsyncSession,
    query: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Artist]:
    """List artists with optional search."""
    stmt = select(Artist).options(selectinload(Artist.image_file))
    if query:
        stmt = stmt.where(Artist.name.ilike(f"%{query}%"))
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_artist(session: AsyncSession, artist_id: str) -> Optional[Artist]:
    """Get an artist by ID."""
    result = await session.execute(
        select(Artist).options(selectinload(Artist.image_file)).where(Artist.id == artist_id)
    )
    return cast(Optional[Artist], result.scalar_one_or_none())


async def list_albums(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Album]:
    """List albums with optional filters, honouring the requester's ACL."""
    stmt = select(Album).options(selectinload(Album.artist), selectinload(Album.cover_file))
    if query:
        stmt = stmt.where(Album.title.ilike(f"%{query}%"))
    if artist_id:
        stmt = stmt.where(Album.artist_id == artist_id)
    if year_from is not None:
        stmt = stmt.where(Album.release_year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Album.release_year <= year_to)
    stmt = apply_access_filter(stmt, Album, user, "album")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_album(session: AsyncSession, album_id: str) -> Optional[Album]:
    """Get an album by ID."""
    result = await session.execute(
        select(Album).options(selectinload(Album.artist), selectinload(Album.cover_file)).where(Album.id == album_id)
    )
    return cast(Optional[Album], result.scalar_one_or_none())


async def list_tracks(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    genre: Optional[str] = None,
    year_from: Optional[int] = None,
    year_to: Optional[int] = None,
    library_id: Optional[str] = None,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Track]:
    """List tracks with optional filters, honouring the requester's ACL."""
    stmt = select(Track).options(
        selectinload(Track.artist),
        selectinload(Track.album),
        selectinload(Track.audio_file),
    )
    if artist_id:
        stmt = stmt.where(Track.artist_id == artist_id)
    if album_id:
        stmt = stmt.where(Track.album_id == album_id)
    if genre:
        stmt = stmt.where(Track.genre == genre)

    if year_from is not None or year_to is not None:
        stmt = stmt.outerjoin(Album, Track.album_id == Album.id)
        range_conditions = []
        if year_from is not None:
            range_conditions.append(Album.release_year >= year_from)
        if year_to is not None:
            range_conditions.append(Album.release_year <= year_to)
        stmt = stmt.where(or_(Album.release_year.is_(None), and_(*range_conditions)))

    if library_id:
        stmt = stmt.join(LibraryTrack, LibraryTrack.track_id == Track.id).where(LibraryTrack.library_id == library_id)

    if query:
        dialect = getattr(getattr(session, "bind", None), "dialect", None)
        if dialect is not None and getattr(dialect, "name", None) == "postgresql" and hasattr(Track, "search_vector"):
            ts_query = func.plainto_tsquery("english", query)
            track_search_vector = Track.search_vector  # type: ignore
            stmt = stmt.where(track_search_vector.op("@@")(ts_query))
            stmt = stmt.order_by(func.ts_rank(track_search_vector, ts_query).desc())
        else:
            stmt = stmt.join(Artist, Track.artist_id == Artist.id)
            if year_from is None and year_to is None:
                stmt = stmt.outerjoin(Album, Track.album_id == Album.id)
            pattern = f"%{query}%"
            stmt = stmt.where(
                or_(
                    Track.title.ilike(pattern),
                    Artist.name.ilike(pattern),
                    Album.title.ilike(pattern),
                )
            )

    stmt = apply_access_filter(stmt, Track, user, "track")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_track(session: AsyncSession, track_id: str) -> Optional[Track]:
    """Get a track by ID."""
    result = await session.execute(
        select(Track)
        .options(
            selectinload(Track.artist),
            selectinload(Track.album),
            selectinload(Track.audio_file),
        )
        .where(Track.id == track_id)
    )
    return cast(Optional[Track], result.scalar_one_or_none())


async def list_library_tracks(
    session: AsyncSession,
    library_id: str,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Track]:
    """List tracks that are members of ``library_id``."""
    stmt = (
        select(Track)
        .options(
            selectinload(Track.artist),
            selectinload(Track.album),
            selectinload(Track.audio_file),
        )
        .join(LibraryTrack, LibraryTrack.track_id == Track.id)
        .where(LibraryTrack.library_id == library_id)
    )
    stmt = apply_access_filter(stmt, Track, user, "track")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_playlists(
    session: AsyncSession,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Playlist]:
    """List playlists visible to ``user``."""
    stmt = select(Playlist)
    stmt = apply_access_filter(stmt, Playlist, user, "playlist")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_playlist(session: AsyncSession, playlist_id: str) -> Optional[Playlist]:
    """Get a playlist by ID."""
    return cast(Optional[Playlist], await session.get(Playlist, playlist_id))


async def list_libraries(
    session: AsyncSession,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Library]:
    """List libraries visible to ``user``."""
    stmt = select(Library)
    stmt = apply_access_filter(stmt, Library, user, "library")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_library(session: AsyncSession, library_id: str) -> Optional[Library]:
    """Get a library by ID."""
    return cast(Optional[Library], await session.get(Library, library_id))


async def list_radios(
    session: AsyncSession,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Radio]:
    """List radios visible to ``user``."""
    stmt = select(Radio)
    stmt = apply_access_filter(stmt, Radio, user, "radio")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_radio(session: AsyncSession, radio_id: str) -> Optional[Radio]:
    """Get a radio by ID."""
    return cast(Optional[Radio], await session.get(Radio, radio_id))
