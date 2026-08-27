"""
Import service: handle file uploads, extract metadata, create library entries.
"""

import asyncio
import io
import logging
import mimetypes
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional, Tuple, cast

import aiofiles
import aiofiles.os
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models._enums import Visibility
from ..models.album import Album
from ..models.artist import Artist
from ..models.library_track import LibraryTrack
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.upload import Upload
from .metadata import AudioMetadata, extract_metadata
from .storage import StorageService, audio_hash

logger = logging.getLogger(__name__)


class DuplicateTrackError(Exception):
    """Raised when an upload matches an existing track."""

    def __init__(self, existing_track_id: str):
        super().__init__(f"Duplicate of track {existing_track_id}")
        self.existing_track_id = existing_track_id


@dataclass
class ImportResult:
    """Result of a successful audio import."""

    track: Track
    upload: Upload
    library_track: LibraryTrack
    stored_file: StoredFile
    was_duplicate: bool


async def _find_or_create_artist(
    session: AsyncSession,
    name: str,
) -> Artist:
    """Find an artist by case-insensitive name, or create one."""
    result = await session.execute(select(Artist).where(func.lower(Artist.name) == name.lower()).limit(1))
    artist = cast(Optional[Artist], result.scalar_one_or_none())
    if artist:
        return artist

    artist = Artist(name=name)
    session.add(artist)
    await session.flush()
    return artist


async def _find_or_create_album(
    session: AsyncSession,
    *,
    title: str,
    artist_id: str,
    year: Optional[int] = None,
    owner_id: Optional[str] = None,
    visibility: str = Visibility.PRIVATE.value,
) -> Album:
    """Find an album by title + artist, or create one."""
    result = await session.execute(
        select(Album)
        .where(
            func.lower(Album.title) == title.lower(),
            Album.artist_id == artist_id,
        )
        .limit(1)
    )
    album = cast(Optional[Album], result.scalar_one_or_none())
    if album:
        return album

    album = Album(
        title=title,
        artist_id=artist_id,
        release_year=year,
        owner_id=owner_id,
        visibility=visibility,
    )
    session.add(album)
    await session.flush()
    return album


async def _find_duplicate_by_bytes(
    session: AsyncSession,
    stored_file_id: str,
    owner_id: Optional[str],
) -> Optional[Track]:
    """Return a visible track that already uses the same stored file."""
    from ..models._enums import Visibility as Vis

    predicates = [Track.audio_file_id == stored_file_id]
    if owner_id is None:
        predicates.append(Track.visibility == Vis.PUBLIC.value)
    else:
        predicates.append(
            or_(
                Track.owner_id == owner_id,
                Track.visibility.in_([Vis.PUBLIC.value, Vis.LOCAL.value]),
            )
        )

    result = await session.execute(select(Track).where(*predicates).limit(1))
    return cast(Optional[Track], result.scalar_one_or_none())


async def _find_duplicate_by_metadata(
    session: AsyncSession,
    library_id: str,
    metadata: AudioMetadata,
) -> Optional[Track]:
    """Return a track in the same library matching title/artist/duration."""
    if metadata.title is None:
        return None

    artist_name = (metadata.artist or "Unknown Artist").lower()
    title = metadata.title.lower()

    stmt = (
        select(Track)
        .join(Artist, Track.artist_id == Artist.id)
        .join(LibraryTrack, LibraryTrack.track_id == Track.id)
        .where(
            LibraryTrack.library_id == library_id,
            func.lower(Artist.name) == artist_name,
            func.lower(Track.title) == title,
        )
    )

    if metadata.duration is not None:
        stmt = stmt.where(func.abs(Track.duration - metadata.duration) <= 2.0)

    result = await session.execute(stmt.limit(1))
    return cast(Optional[Track], result.scalar_one_or_none())


def _guess_content_type(filename: str) -> str:
    """Guess a MIME type from a filename, falling back to a binary default."""
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _is_audio_content_type(content_type: str) -> bool:
    """Return True for MIME types that should use audio-only hashing."""
    return content_type.startswith("audio/")


async def _store_uploaded_audio_file(
    session: AsyncSession,
    storage_service: StorageService,
    file: BinaryIO,
    content_type: str,
    filename: str,
    owner_id: Optional[str],
    visibility: str,
) -> Tuple[StoredFile, bool]:
    fd, tmp_name = tempfile.mkstemp()
    os.close(fd)
    os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
    tmp_path = Path(tmp_name)

    try:
        async with aiofiles.open(tmp_path, "wb") as dest:
            while True:
                chunk = await asyncio.to_thread(file.read, storage_service.CHUNK_SIZE)
                if not chunk:
                    break
                await dest.write(chunk)

        try:
            hash_hex = await audio_hash(tmp_path)
        except RuntimeError as exc:
            logger.warning(
                "Could not compute audio-only hash for %s: %s; falling back to full-file hash",
                filename,
                exc,
            )
            hash_hex = None

        with open(tmp_path, "rb") as f:
            return cast(
                Tuple[StoredFile, bool],
                await storage_service.store_file(
                    session,
                    f,
                    content_type,
                    original_filename=filename,
                    owner_id=owner_id,
                    visibility=visibility,
                    content_hash=hash_hex,
                    return_duplicate=True,
                ),
            )
    finally:
        if await aiofiles.os.path.exists(tmp_path):
            await aiofiles.os.remove(tmp_path)


async def _store_uploaded_file(
    session: AsyncSession,
    storage_service: StorageService,
    file: BinaryIO,
    content_type: str,
    filename: str,
    owner_id: Optional[str],
    visibility: str,
) -> Tuple[StoredFile, bool]:
    """Store the uploaded audio file and return it with a duplicate flag."""
    if _is_audio_content_type(content_type):
        return await _store_uploaded_audio_file(
            session=session,
            storage_service=storage_service,
            file=file,
            content_type=content_type,
            filename=filename,
            owner_id=owner_id,
            visibility=visibility,
        )

    return cast(
        Tuple[StoredFile, bool],
        await storage_service.store_file(
            session,
            file,
            content_type,
            original_filename=filename,
            owner_id=owner_id,
            visibility=visibility,
            return_duplicate=True,
        ),
    )


async def _extract_metadata_from_file(storage_service: StorageService, stored_file: StoredFile) -> AudioMetadata:
    """
    Materialise a local copy of the stored file and extract audio metadata.

    Temporary files returned by non-local backends are removed after extraction.
    """
    local_path = await storage_service.backend.retrieve(stored_file.storage_path)
    if local_path is None:
        raise RuntimeError(f"Could not retrieve stored file: {stored_file.storage_path}")

    should_remove = True
    base_path = getattr(storage_service.backend, "base_path", None)
    if isinstance(base_path, Path):
        try:
            if local_path.resolve().is_relative_to(base_path.resolve()):
                should_remove = False
        except (OSError, ValueError):
            pass

    try:
        return extract_metadata(Path(local_path))
    finally:
        if should_remove and os.path.exists(local_path):
            os.remove(local_path)


async def _resolve_artist_and_album(
    session: AsyncSession,
    metadata: AudioMetadata,
    owner_id: Optional[str],
    visibility: str,
) -> Tuple[Artist, Optional[Album]]:
    """Find or create the artist and album for the incoming metadata."""
    artist = await _find_or_create_artist(session, metadata.artist or "Unknown Artist")

    album = None
    if metadata.album:
        album = await _find_or_create_album(
            session,
            title=metadata.album,
            artist_id=str(artist.id),
            year=metadata.year,
            owner_id=owner_id,
            visibility=visibility,
        )

    return artist, album


async def _maybe_store_cover_art(
    session: AsyncSession,
    *,
    storage_service: StorageService,
    album: Optional[Album],
    metadata: AudioMetadata,
    owner_id: Optional[str],
    visibility: str,
) -> None:
    """Store embedded cover art and attach it to the album when appropriate."""
    if not metadata.cover_art or album is None or album.cover_file_id is not None:
        return

    cover_file, _ = cast(
        Tuple[StoredFile, bool],
        await storage_service.store_file(
            session,
            io.BytesIO(metadata.cover_art),
            metadata.cover_art_mime or "image/jpeg",
            prefix="covers",
            owner_id=owner_id,
            visibility=visibility,
            return_duplicate=True,
        ),
    )
    album.cover_file_id = str(cover_file.id)


async def _create_track_record(
    session: AsyncSession,
    *,
    metadata: AudioMetadata,
    stored_file: StoredFile,
    artist: Artist,
    album: Optional[Album],
    filename: str,
    owner_id: Optional[str],
    visibility: str,
    source: str,
    content_type: str,
) -> Track:
    """Create and persist a Track record from extracted metadata."""
    track = Track(
        title=metadata.title or Path(filename).stem,
        artist_id=str(artist.id),
        album_id=album.id if album else None,
        track_number=metadata.track_number,
        disc_number=metadata.disc_number,
        duration=metadata.duration,
        genre=metadata.genre,
        audio_file_id=str(stored_file.id),
        raw_metadata=metadata.raw_tags,
        source=source,
        owner_id=owner_id,
        visibility=visibility,
        audio_mime_type=metadata.mimetype or content_type,
    )
    session.add(track)
    await session.flush()
    return track


async def _register_upload_and_library_track(
    session: AsyncSession,
    *,
    track: Track,
    stored_file: StoredFile,
    library_id: str,
    owner_id: Optional[str],
    metadata: AudioMetadata,
    content_type: str,
) -> Tuple[Upload, LibraryTrack]:
    """Create the Upload and LibraryTrack records that link a track to a library."""
    upload = Upload(
        track_id=str(track.id),
        library_id=library_id,
        storage_path=stored_file.storage_path,
        storage_backend=stored_file.storage_backend,
        mimetype=metadata.mimetype or content_type,
        size=stored_file.size,
        bitrate=metadata.bitrate,
        checksum=stored_file.sha256,
        stored_file_id=str(stored_file.id),
    )
    session.add(upload)

    library_track = LibraryTrack(
        library_id=library_id,
        track_id=str(track.id),
        added_by_id=owner_id,
    )
    session.add(library_track)
    await session.flush()
    return upload, library_track


def _maybe_enqueue_enrichment(track: Track, enrich: bool) -> None:
    """Enqueue a MusicBrainz enrichment task when requested."""
    if not enrich:
        return

    try:
        from ..tasks.musicbrainz import enrich_track

        enrich_track.delay(str(track.id))  # type: ignore
    except Exception:
        pass


async def import_audio_file(
    session: AsyncSession,
    *,
    storage_service: StorageService,
    file: BinaryIO,
    filename: str,
    library_id: str,
    owner_id: Optional[str] = None,
    visibility: str = Visibility.PRIVATE.value,
    force: bool = False,
    enrich: bool = True,
    source: str = "upload",
    content_type: Optional[str] = None,
) -> ImportResult:
    """
    Import an audio file: store it, extract metadata, create artist/album/track
    records, and register the upload.

    :param session: Async SQLAlchemy session.
    :param storage_service: Configured storage service.
    :param file: Readable binary audio stream.
    :param filename: Original filename, used for content-type sniffing.
    :param library_id: Library to add the track to.
    :param owner_id: Optional owner for the created track/album.
    :param visibility: Visibility for created track and any new album.
    :param force: If ``True``, create a new track even if a duplicate exists.
    :param enrich: Whether to enqueue MusicBrainz enrichment.
    :param source: Track source (``upload``, ``import``, ``federation``).
    :param content_type: MIME type for the audio file; guessed from ``filename``
        when not provided.
    :returns: Import result with the created records.
    :raises DuplicateTrackError: When a duplicate is detected and ``force`` is
        ``False``.
    """
    if not content_type:
        content_type = _guess_content_type(filename)
    stored_file, was_duplicate = await _store_uploaded_file(
        session,
        storage_service,
        file,
        content_type,
        filename,
        owner_id,
        visibility,
    )

    if was_duplicate and not force:
        existing = await _find_duplicate_by_bytes(session, str(stored_file.id), owner_id)
        if existing:
            raise DuplicateTrackError(str(existing.id))

    metadata = await _extract_metadata_from_file(storage_service, stored_file)

    existing_meta = await _find_duplicate_by_metadata(session, library_id, metadata)
    if existing_meta and not force:
        raise DuplicateTrackError(str(existing_meta.id))

    artist, album = await _resolve_artist_and_album(session, metadata, owner_id, visibility)
    track = await _create_track_record(
        session,
        metadata=metadata,
        stored_file=stored_file,
        artist=artist,
        album=album,
        filename=filename,
        owner_id=owner_id,
        visibility=visibility,
        source=source,
        content_type=content_type,
    )

    await _maybe_store_cover_art(
        session,
        storage_service=storage_service,
        album=album,
        metadata=metadata,
        owner_id=owner_id,
        visibility=visibility,
    )
    upload, library_track = await _register_upload_and_library_track(
        session,
        track=track,
        stored_file=stored_file,
        library_id=library_id,
        owner_id=owner_id,
        metadata=metadata,
        content_type=content_type,
    )

    _maybe_enqueue_enrichment(track, enrich)
    return ImportResult(
        track=track,
        upload=upload,
        library_track=library_track,
        stored_file=stored_file,
        was_duplicate=was_duplicate,
    )
