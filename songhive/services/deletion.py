"""
Cascade deletion service for media entities.

This module provides reusable helpers for deleting tracks, file uploads,
albums, artists, playlists, and libraries while cleaning up dependent rows,
storage files, and share/report metadata.  It does not perform HTTP or
permission checks; callers are responsible for those at the route layer.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models._enums import Visibility
from ..models.album import Album
from ..models.artist import Artist
from ..models.favorite import Favorite
from ..models.history import ListeningHistory
from ..models.library import Library
from ..models.library_track import LibraryTrack
from ..models.playlist import Playlist, PlaylistTrack
from ..models.report import Report
from ..models.share_grant import ShareGrant
from ..models.share_token import ShareToken
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.transcoded_file import TranscodedFile
from ..models.upload import Upload
from ..models.user import User
from ..services.acl import can_manage
from ..services.auth import get_user_by_id
from ..services.storage import StorageService

logger = logging.getLogger(__name__)


class DeletionError(ValueError):
    """Raised when a deletion cannot be completed."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class UnpublishInfo:
    """Information needed to enqueue a federation unpublish for a deleted track."""

    track_id: str
    title: str
    artist_id: Optional[str]
    owner_id: Optional[str]
    federation_object_id: Optional[str]
    track: Track
    artist: Optional[Artist]
    owner: Optional[User]


@dataclass
class DeletionResult:
    """Result of a cascade delete operation."""

    unpublish: List[UnpublishInfo] = field(default_factory=list)
    deleted_track_ids: List[str] = field(default_factory=list)
    deleted_file_ids: List[str] = field(default_factory=list)
    deleted_album_ids: List[str] = field(default_factory=list)
    deleted_artist_ids: List[str] = field(default_factory=list)
    deleted_playlist_ids: List[str] = field(default_factory=list)
    deleted_library_ids: List[str] = field(default_factory=list)


async def _count_stored_file_references(session: AsyncSession, stored_file_id: str) -> int:
    """Return the number of rows referencing ``stored_file_id``."""
    audio_refs = await session.scalar(select(func.count(Track.id)).where(Track.audio_file_id == stored_file_id))
    upload_refs = await session.scalar(select(func.count(Upload.id)).where(Upload.stored_file_id == stored_file_id))
    album_refs = await session.scalar(select(func.count(Album.id)).where(Album.cover_file_id == stored_file_id))
    artist_refs = await session.scalar(select(func.count(Artist.id)).where(Artist.image_file_id == stored_file_id))
    transcoded_refs = await session.scalar(
        select(func.count(TranscodedFile.id)).where(TranscodedFile.stored_file_id == stored_file_id)
    )
    return sum([audio_refs or 0, upload_refs or 0, album_refs or 0, artist_refs or 0, transcoded_refs or 0])


async def _maybe_delete_stored_file(
    session: AsyncSession,
    storage: StorageService,
    stored_file: Optional[StoredFile],
) -> bool:
    """Delete a stored file's backing object and row if it is no longer referenced.

    The row is re-loaded with ``SELECT ... FOR UPDATE`` so concurrent writers
    (e.g. an audio import creating a new reference) block until the delete
    commits, preventing a race between the reference count and storage removal.

    This assumes duplicate uploads still create a new ``StoredFile`` row rather
    than sharing an existing one, which keeps reference counting tied to a
    single row's lifecycle.
    """
    if stored_file is None:
        return False

    result = await session.execute(select(StoredFile).where(StoredFile.id == stored_file.id).with_for_update())
    fresh = result.scalar_one_or_none()
    if fresh is None:
        return False

    refs = await _count_stored_file_references(session, str(fresh.id))
    if refs > 0:
        return False

    try:
        await storage.delete_file(fresh)
    except Exception:
        logger.exception(
            "Failed to delete stored file %s from %s backend (path: %s); keeping database row for retry",
            fresh.id,
            fresh.storage_backend,
            fresh.storage_path,
        )
        return False

    await session.execute(delete(StoredFile).where(StoredFile.id == fresh.id))
    return True


async def _delete_track_dependents(
    session: AsyncSession,
    storage: StorageService,
    track: Track,
    delete_audio_file: bool,
) -> Optional[UnpublishInfo]:
    """Delete all rows that depend on ``track`` and optionally its audio file."""
    artist = track.artist
    unpublish: Optional[UnpublishInfo] = None
    if track.visibility == Visibility.PUBLIC.value and artist is not None and track.owner_id:
        owner = await get_user_by_id(session, track.owner_id)
        if owner is not None:
            unpublish = UnpublishInfo(
                track_id=str(track.id),
                title=track.title,
                artist_id=track.artist_id,
                owner_id=track.owner_id,
                federation_object_id=track.federation_object_id,
                track=track,
                artist=artist,
                owner=owner,
            )

    # Transcoded variants reference both the track and their own stored files.
    transcoded_result = await session.execute(
        select(TranscodedFile)
        .options(selectinload(TranscodedFile.stored_file))
        .where(TranscodedFile.track_id == track.id)
    )
    for tf in transcoded_result.scalars().all():
        await session.execute(delete(TranscodedFile).where(TranscodedFile.id == tf.id))
        if tf.stored_file is not None:
            await _maybe_delete_stored_file(session, storage, tf.stored_file)

    await session.execute(delete(Upload).where(Upload.track_id == track.id))
    await session.execute(delete(PlaylistTrack).where(PlaylistTrack.track_id == track.id))
    await session.execute(delete(LibraryTrack).where(LibraryTrack.track_id == track.id))
    await session.execute(delete(Favorite).where(Favorite.track_id == track.id))
    await session.execute(delete(ListeningHistory).where(ListeningHistory.track_id == track.id))
    await session.execute(delete(ShareGrant).where(ShareGrant.item_type == "track", ShareGrant.item_id == track.id))
    await session.execute(delete(ShareToken).where(ShareToken.item_type == "track", ShareToken.item_id == track.id))
    await session.execute(delete(Report).where(Report.target_type == "track", Report.target_id == track.id))

    await session.execute(delete(Track).where(Track.id == track.id))

    if delete_audio_file and track.audio_file is not None:
        await _maybe_delete_stored_file(session, storage, track.audio_file)

    return unpublish


async def delete_track(
    session: AsyncSession,
    storage: StorageService,
    track_id: str,
    *,
    delete_audio_file: bool = True,
) -> Optional[UnpublishInfo]:
    """Delete a single track and its associated uploads/entries."""
    result = await session.execute(
        select(Track).options(selectinload(Track.artist), selectinload(Track.audio_file)).where(Track.id == track_id)
    )
    track = result.scalar_one_or_none()
    if track is None:
        raise DeletionError("Track not found", 404)

    return await _delete_track_dependents(session, storage, track, delete_audio_file=delete_audio_file)


async def delete_tracks_bulk(
    session: AsyncSession,
    storage: StorageService,
    track_ids: List[str],
    *,
    user: Optional[User] = None,
) -> tuple[List[UnpublishInfo], List[str]]:
    """Delete several tracks after pre-checking existence and manageability.

    This prevents partial deletes: if any track does not exist or cannot be
    managed by the caller, no rows are changed and a ``DeletionError`` is
    raised before the first track is removed.
    """
    result = await session.execute(
        select(Track).options(selectinload(Track.artist), selectinload(Track.audio_file)).where(Track.id.in_(track_ids))
    )
    track_map = {str(track.id): track for track in result.scalars().all()}

    tracks: List[Track] = []
    for track_id in track_ids:
        track = track_map.get(track_id)
        if track is None:
            raise DeletionError("Track not found", 404)
        if not await can_manage(session, user, "track", track_id):
            raise DeletionError("Cannot delete track", 403)
        tracks.append(track)

    unpublish: List[UnpublishInfo] = []
    deleted: List[str] = []
    for track in tracks:
        info = await _delete_track_dependents(session, storage, track, delete_audio_file=True)
        deleted.append(str(track.id))
        if info is not None:
            unpublish.append(info)

    return unpublish, deleted


async def delete_stored_file(
    session: AsyncSession,
    storage: StorageService,
    file_id: str,
) -> List[UnpublishInfo]:
    """Delete a stored file and any tracks/uploads that depend on it."""
    stored = await session.get(StoredFile, file_id)
    if stored is None:
        raise DeletionError("File not found", 404)

    unpublish: List[UnpublishInfo] = []

    # Tracks that use this file as their audio source must be removed first.
    track_result = await session.execute(select(Track.id).where(Track.audio_file_id == file_id))
    for row in track_result.scalars().all():
        info = await delete_track(session, storage, str(row), delete_audio_file=False)
        if info is not None:
            unpublish.append(info)

    # Orphan uploads that point directly at this file but have no track.
    await session.execute(delete(Upload).where(Upload.stored_file_id == file_id))

    # Remove non-audio uses of the file (cover art, artist images).
    await session.execute(update(Album).where(Album.cover_file_id == file_id).values(cover_file_id=None))
    await session.execute(update(Artist).where(Artist.image_file_id == file_id).values(image_file_id=None))

    await session.execute(delete(ShareGrant).where(ShareGrant.item_type == "file", ShareGrant.item_id == file_id))
    await session.execute(delete(ShareToken).where(ShareToken.item_type == "file", ShareToken.item_id == file_id))
    await session.execute(delete(Report).where(Report.target_type == "file", Report.target_id == file_id))

    await _maybe_delete_stored_file(session, storage, stored)
    return unpublish


async def delete_album(
    session: AsyncSession,
    storage: StorageService,
    album_id: str,
    *,
    recursive: bool = True,
    user: Optional[User] = None,
    is_admin: bool = False,
) -> DeletionResult:
    """Delete an album, optionally removing the tracks that belong to it."""
    result = await session.execute(
        select(Album).options(selectinload(Album.artist), selectinload(Album.cover_file)).where(Album.id == album_id)
    )
    album = result.scalar_one_or_none()
    if album is None:
        raise DeletionError("Album not found", 404)

    deletion = DeletionResult()
    deletion.deleted_album_ids.append(str(album.id))

    track_rows = list((await session.execute(select(Track.id, Track.owner_id).where(Track.album_id == album_id))).all())
    track_ids = [str(row[0]) for row in track_rows]

    if recursive:
        to_delete = []
        to_nullify = []
        for track_id, owner_id in track_rows:
            if is_admin or (user is not None and user.id == owner_id):
                to_delete.append(str(track_id))
            else:
                to_nullify.append(str(track_id))

        if to_nullify:
            await session.execute(update(Track).where(Track.id.in_(to_nullify)).values(album_id=None))

        for track_id in to_delete:
            info = await delete_track(session, storage, track_id)
            deletion.deleted_track_ids.append(track_id)
            if info is not None:
                deletion.unpublish.append(info)
    else:
        # Keep the tracks but remove the album reference.
        if track_ids:
            await session.execute(update(Track).where(Track.id.in_(track_ids)).values(album_id=None))

    await session.execute(delete(ShareGrant).where(ShareGrant.item_type == "album", ShareGrant.item_id == album.id))
    await session.execute(delete(ShareToken).where(ShareToken.item_type == "album", ShareToken.item_id == album.id))
    await session.execute(delete(Report).where(Report.target_type == "album", Report.target_id == album.id))
    await session.execute(delete(Album).where(Album.id == album.id))

    if album.cover_file is not None:
        await _maybe_delete_stored_file(session, storage, album.cover_file)

    return deletion


async def delete_artist(
    session: AsyncSession,
    storage: StorageService,
    artist_id: str,
    *,
    recursive: bool = False,
    user: Optional[User] = None,
    is_admin: bool = False,
) -> DeletionResult:
    """Delete an artist.  Non-recursive deletion only succeeds for empty artists."""
    result = await session.execute(
        select(Artist).options(selectinload(Artist.image_file)).where(Artist.id == artist_id)
    )
    artist = result.scalar_one_or_none()
    if artist is None:
        raise DeletionError("Artist not found", 404)

    track_count = await session.scalar(select(func.count(Track.id)).where(Track.artist_id == artist_id))
    album_count = await session.scalar(select(func.count(Album.id)).where(Album.artist_id == artist_id))
    if not recursive and (track_count or album_count):
        raise DeletionError("Artist has associated tracks or albums", 409)

    deletion = DeletionResult()
    deletion.deleted_artist_ids.append(str(artist.id))

    if recursive:
        # Pre-check all tracks are manageable before deleting anything.  This
        # prevents a partial cascade where some tracks are deleted before the
        # loop raises on a track owned by another user.
        if not is_admin:
            if user is None:
                raise DeletionError("Cannot delete artist without a managing user", 403)

            all_track_rows = list(
                (await session.execute(select(Track.id, Track.owner_id).where(Track.artist_id == artist_id))).all()
            )
            for _, owner_id in all_track_rows:
                if str(owner_id) != str(user.id):
                    raise DeletionError("Cannot delete artist with tracks owned by another user", 403)

        # Albums must be deleted first because they hold a non-nullable artist FK.
        album_ids = list((await session.execute(select(Album.id).where(Album.artist_id == artist_id))).scalars().all())
        for album_id in album_ids:
            album_deletion = await delete_album(
                session,
                storage,
                str(album_id),
                recursive=True,
                user=user,
                is_admin=is_admin,
            )
            deletion.deleted_album_ids.extend(album_deletion.deleted_album_ids)
            deletion.deleted_track_ids.extend(album_deletion.deleted_track_ids)
            deletion.unpublish.extend(album_deletion.unpublish)

        # Any remaining tracks that were not part of an album.
        track_rows = list(
            (await session.execute(select(Track.id, Track.owner_id).where(Track.artist_id == artist_id))).all()
        )
        for track_id, owner_id in track_rows:
            if is_admin or (user is not None and str(user.id) == str(owner_id)):
                info = await delete_track(session, storage, str(track_id))
                deletion.deleted_track_ids.append(str(track_id))
                if info is not None:
                    deletion.unpublish.append(info)
            else:
                # Defensive guard: the pre-check above should prevent this path.
                raise DeletionError("Cannot delete track owned by another user", 403)

    await session.execute(delete(ShareGrant).where(ShareGrant.item_type == "artist", ShareGrant.item_id == artist.id))
    await session.execute(delete(ShareToken).where(ShareToken.item_type == "artist", ShareToken.item_id == artist.id))
    await session.execute(delete(Report).where(Report.target_type == "artist", Report.target_id == artist.id))
    await session.execute(delete(Artist).where(Artist.id == artist.id))

    if artist.image_file is not None:
        await _maybe_delete_stored_file(session, storage, artist.image_file)

    return deletion


async def delete_playlist(
    session: AsyncSession,
    storage: StorageService,
    playlist_id: str,
    *,
    recursive: bool = False,
    user: Optional[User] = None,
    is_admin: bool = False,
) -> DeletionResult:
    """Delete a playlist and, optionally, the tracks it contains."""
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None:
        raise DeletionError("Playlist not found", 404)

    deletion = DeletionResult()
    deletion.deleted_playlist_ids.append(str(playlist.id))

    if recursive:
        track_ids = list(
            (
                await session.execute(
                    select(PlaylistTrack.track_id).distinct().where(PlaylistTrack.playlist_id == playlist_id)
                )
            )
            .scalars()
            .all()
        )
        for track_id in track_ids:
            track = await session.get(Track, track_id)
            if track is None:
                continue
            if is_admin or (user is not None and user.id == track.owner_id):
                info = await delete_track(session, storage, str(track_id))
                deletion.deleted_track_ids.append(str(track_id))
                if info is not None:
                    deletion.unpublish.append(info)
            # Unmanageable tracks are simply removed from the playlist below.

    await session.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
    await session.execute(
        delete(ShareGrant).where(ShareGrant.item_type == "playlist", ShareGrant.item_id == playlist.id)
    )
    await session.execute(
        delete(ShareToken).where(ShareToken.item_type == "playlist", ShareToken.item_id == playlist.id)
    )
    await session.execute(delete(Report).where(Report.target_type == "playlist", Report.target_id == playlist.id))
    await session.execute(delete(Playlist).where(Playlist.id == playlist.id))

    return deletion


async def delete_library(
    session: AsyncSession,
    storage: StorageService,
    library_id: str,
    *,
    recursive: bool = False,
    user: Optional[User] = None,
    is_admin: bool = False,
) -> DeletionResult:
    """Delete a library and, optionally, all tracks it contains."""
    library = await session.get(Library, library_id)
    if library is None:
        raise DeletionError("Library not found", 404)

    deletion = DeletionResult()
    deletion.deleted_library_ids.append(str(library.id))

    if recursive:
        track_ids = list(
            (
                await session.execute(
                    select(LibraryTrack.track_id).distinct().where(LibraryTrack.library_id == library_id)
                )
            )
            .scalars()
            .all()
        )
        for track_id in track_ids:
            track = await session.get(Track, track_id)
            if track is None:
                continue
            if is_admin or (user is not None and user.id == track.owner_id):
                info = await delete_track(session, storage, str(track_id))
                deletion.deleted_track_ids.append(str(track_id))
                if info is not None:
                    deletion.unpublish.append(info)

    await session.execute(delete(LibraryTrack).where(LibraryTrack.library_id == library_id))
    await session.execute(delete(ShareGrant).where(ShareGrant.item_type == "library", ShareGrant.item_id == library.id))
    await session.execute(delete(ShareToken).where(ShareToken.item_type == "library", ShareToken.item_id == library.id))
    await session.execute(delete(Report).where(Report.target_type == "library", Report.target_id == library.id))
    await session.execute(delete(Library).where(Library.id == library.id))

    return deletion
