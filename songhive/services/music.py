"""
Music service: CRUD operations for artists, albums, tracks, playlists,
libraries, and radios.
"""

from typing import List, Optional, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.album import Album
from ..models.artist import Artist
from ..models.library import Library
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
    stmt = select(Artist)
    if query:
        stmt = stmt.where(Artist.name.ilike(f"%{query}%"))
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_artist(session: AsyncSession, artist_id: str) -> Optional[Artist]:
    """Get an artist by ID."""
    return cast(Optional[Artist], await session.get(Artist, artist_id))


async def list_albums(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Album]:
    """List albums with optional filters, honouring the requester's ACL."""
    stmt = select(Album)
    if query:
        stmt = stmt.where(Album.title.ilike(f"%{query}%"))
    if artist_id:
        stmt = stmt.where(Album.artist_id == artist_id)
    stmt = apply_access_filter(stmt, Album, user, "album")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_album(session: AsyncSession, album_id: str) -> Optional[Album]:
    """Get an album by ID."""
    return cast(Optional[Album], await session.get(Album, album_id))


async def list_tracks(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    user: Optional[User] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Track]:
    """List tracks with optional filters, honouring the requester's ACL."""
    stmt = select(Track)
    if query:
        stmt = stmt.where(Track.title.ilike(f"%{query}%"))
    if artist_id:
        stmt = stmt.where(Track.artist_id == artist_id)
    if album_id:
        stmt = stmt.where(Track.album_id == album_id)
    stmt = apply_access_filter(stmt, Track, user, "track")
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_track(session: AsyncSession, track_id: str) -> Optional[Track]:
    """Get a track by ID."""
    return cast(Optional[Track], await session.get(Track, track_id))


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
