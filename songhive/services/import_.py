"""
Import service: handle file uploads, extract metadata, create library entries.
"""

from typing import BinaryIO

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.artist import Artist
from ..models.track import Track
from ..models.upload import Upload


async def import_audio_file(
    session: AsyncSession,
    file: BinaryIO,
    filename: str,
    library_id: str,
    storage_backend: str,
    storage_path: str,
    mimetype: str,
    size: int,
) -> Upload:
    """
    Import an audio file: extract metadata, create artist/album/track
    records as needed, and register the upload.

    This is a skeleton - full implementation will use mutagen for metadata
    extraction and the storage backend for file persistence.
    """
    # TODO: extract metadata from file using mutagen
    # TODO: look up or create Artist
    # TODO: look up or create Album
    # TODO: create Track
    # TODO: store file via storage backend
    # TODO: create Upload record

    # Placeholder implementation
    artist = Artist(name="Unknown Artist")
    session.add(artist)
    await session.flush()

    track = Track(title=filename, artist_id=artist.id)
    session.add(track)
    await session.flush()

    upload = Upload(
        track_id=track.id,
        library_id=library_id,
        storage_path=storage_path,
        storage_backend=storage_backend,
        mimetype=mimetype,
        size=size,
    )
    session.add(upload)
    await session.flush()

    return upload
