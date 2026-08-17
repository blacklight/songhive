"""
Music file importer: processes uploaded audio files.
"""

from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.album import Album
from ..models.artist import Artist
from ..models.track import Track
from ..models.upload import Upload
from ..services.metadata import extract_metadata
from ..storage.base import StorageBackend


async def import_file(
    session: AsyncSession,
    file_path: Path,
    library_id: str,
    storage: StorageBackend,
    storage_backend: str,
) -> Upload:
    """
    Import an audio file into the library.

    1. Extract metadata from the file
    2. Find or create Artist
    3. Find or create Album
    4. Create Track
    5. Store the file
    6. Create Upload record

    :param storage_backend: Configured storage backend identifier (e.g. ``local`` or ``s3``).
    """
    metadata = extract_metadata(file_path)

    artist = await _find_or_create_artist(session, metadata.artist or "Unknown Artist")
    album = None
    if metadata.album:
        album = await _find_or_create_album(session, metadata.album, artist.id, metadata.year)

    track = Track(
        title=metadata.title or file_path.stem,
        artist_id=artist.id,
        album_id=album.id if album else None,
        track_number=metadata.track_number,
        disc_number=metadata.disc_number,
        duration=metadata.duration,
        genre=metadata.genre,
    )
    session.add(track)
    await session.flush()

    # Store the file
    storage_path = f"tracks/{track.id}/{file_path.name}"
    with open(file_path, "rb") as f:
        await storage.store(f, storage_path, content_type=metadata.mimetype)

    upload = Upload(
        track_id=track.id,
        library_id=library_id,
        storage_path=storage_path,
        storage_backend=storage_backend,
        mimetype=metadata.mimetype or "application/octet-stream",
        size=file_path.stat().st_size,
        bitrate=metadata.bitrate,
    )
    session.add(upload)
    await session.flush()

    return upload


async def _find_or_create_artist(session: AsyncSession, name: str) -> Artist:
    """Find an artist by name, or create one."""
    result = await session.execute(select(Artist).where(Artist.name == name).limit(1))
    artist = result.scalar_one_or_none()
    if artist:
        return artist

    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _find_or_create_album(
    session: AsyncSession,
    title: str,
    artist_id: str,
    year: Optional[int] = None,
) -> Album:
    """Find an album by title+artist, or create one."""
    result = await session.execute(select(Album).where(Album.title == title, Album.artist_id == artist_id).limit(1))
    album = result.scalar_one_or_none()
    if album:
        return album

    album = Album(title=title, artist_id=artist_id, release_year=year)
    session.add(album)
    await session.flush()
    return album
