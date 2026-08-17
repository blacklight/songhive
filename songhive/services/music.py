"""
Music service: CRUD operations for artists, albums, tracks.
"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.album import Album
from ..models.artist import Artist
from ..models.track import Track


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
    return await session.get(Artist, artist_id)


async def list_albums(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Album]:
    """List albums with optional filters."""
    stmt = select(Album)
    if query:
        stmt = stmt.where(Album.title.ilike(f"%{query}%"))
    if artist_id:
        stmt = stmt.where(Album.artist_id == artist_id)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_album(session: AsyncSession, album_id: str) -> Optional[Album]:
    """Get an album by ID."""
    return await session.get(Album, album_id)


async def list_tracks(
    session: AsyncSession,
    query: Optional[str] = None,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[Track]:
    """List tracks with optional filters."""
    stmt = select(Track)
    if query:
        stmt = stmt.where(Track.title.ilike(f"%{query}%"))
    if artist_id:
        stmt = stmt.where(Track.artist_id == artist_id)
    if album_id:
        stmt = stmt.where(Track.album_id == album_id)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_track(session: AsyncSession, track_id: str) -> Optional[Track]:
    """Get a track by ID."""
    return await session.get(Track, track_id)
