"""
Bulk download archive service.

Resolves a download request (explicit track/remote-object/episode ids or an
album/artist/playlist/library container) into a snapshot of archive items,
then materializes each item into a working directory and writes a ZIP into
the storage backend as a private ``StoredFile``.

Item kinds:

- ``track`` — a local ``Track`` row; resolves to its ``StoredFile`` or, for
  externally mounted tracks, through the external-library adapter
  (``download``/``open_stream`` + ``collect_external_stream``).
- ``remote`` — a cached ``RemoteObject``; its playable media URL is fetched
  through ``guarded_download`` (SSRF + domain moderation on every hop).
- ``episode`` — a ``PodcastEpisode``; its enclosure URL is fetched through
  ``guarded_download`` (SSRF only — podcast enclosures are public feed data
  and not subject to fediverse domain moderation).

Transient fetches (external streams and remote URLs) are retried with
exponential backoff driven by ``config.downloads``.
"""

import asyncio
import logging
import mimetypes
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Awaitable, Callable, Optional, Sequence, Tuple, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config.schema import SonghiveConfig
from ..external.errors import ExternalItemNotFound, UnsupportedExternalOperation
from ..federation.fetch import FetchError, FetchNotFound, guarded_download
from ..models.album import Album
from ..models.artist import Artist
from ..models.download import DownloadArchive
from ..models.library import Library
from ..models.playlist import Playlist
from ..models.podcast import PodcastEpisode
from ..models.remote_object import RemoteObject
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.user import User
from . import acl, music, remote_content
from .storage import StorageService
from .streaming import (
    collect_external_stream,
    resolve_external_download_stream,
    resolve_track_file,
)

logger = logging.getLogger(__name__)

ITEM_KIND_TRACK = "track"
ITEM_KIND_REMOTE = "remote"
ITEM_KIND_EPISODE = "episode"

_FILENAME_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
_ARCNAME_MAX = 120

_T = TypeVar("_T")


class ArchiveRequestError(ValueError):
    """A download request could not be resolved (bad input or denied access)."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ArchiveItem:
    """A resolved archive member."""

    kind: str
    ref: str
    title: str
    artist: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "title": self.title, "artist": self.artist}

    @staticmethod
    def from_dict(payload: dict) -> "ArchiveItem":
        return ArchiveItem(
            kind=str(payload.get("kind") or ""),
            ref=str(payload.get("ref") or ""),
            title=str(payload.get("title") or ""),
            artist=str(payload.get("artist") or ""),
        )


def _track_item(track: Track) -> ArchiveItem:
    artist = track.artist.name if track.artist else ""
    return ArchiveItem(kind=ITEM_KIND_TRACK, ref=track.id, title=track.title, artist=artist)


def _remote_item(row: RemoteObject) -> ArchiveItem:
    return ArchiveItem(
        kind=ITEM_KIND_REMOTE,
        ref=row.id,
        title=row.name or row.canonical_url,
        artist="",
    )


def _episode_item(episode: PodcastEpisode) -> ArchiveItem:
    artist = episode.podcast.title if episode.podcast else ""
    return ArchiveItem(kind=ITEM_KIND_EPISODE, ref=episode.id, title=episode.title, artist=artist)


async def _require_container_access(
    session: AsyncSession,
    user: User,
    item_type: str,
    item_id: str,
) -> None:
    """Raise ``ArchiveRequestError`` unless ``user`` can access the container."""
    item = await acl.get_item(session, item_type, item_id)
    if item is None:
        raise ArchiveRequestError("Not found", status_code=404)
    if not await acl.can_access(session, user, item_type, item_id):
        raise ArchiveRequestError("Access denied", status_code=403)


async def resolve_archive_items(
    session: AsyncSession,
    user: User,
    config: SonghiveConfig,
    *,
    track_ids: Optional[Sequence[str]] = None,
    remote_object_ids: Optional[Sequence[str]] = None,
    episode_ids: Optional[Sequence[str]] = None,
    album_id: Optional[str] = None,
    artist_id: Optional[str] = None,
    playlist_id: Optional[str] = None,
    library_id: Optional[str] = None,
) -> Tuple[list[ArchiveItem], int]:
    """
    Resolve an archive request into ordered items, honouring the requester ACL.

    Returns ``(items, skipped)`` where ``skipped`` counts explicitly requested
    ids that were dropped because they do not exist or are not accessible.
    Container requests raise ``ArchiveRequestError`` when the container is
    missing or inaccessible.
    """
    max_items = config.downloads.max_items
    items: list[ArchiveItem] = []
    skipped = 0

    if track_ids:
        accessible = await acl.filter_accessible_track_ids(session, user, list(track_ids))
        skipped += len(track_ids) - len(accessible)
        wanted = [track_id for track_id in track_ids if track_id in accessible]
        if wanted:
            result = await session.execute(
                select(Track).where(Track.id.in_(wanted)).options(selectinload(Track.artist))
            )
            by_id = {track.id: track for track in result.scalars().all()}
            items.extend(_track_item(by_id[track_id]) for track_id in wanted if track_id in by_id)

    if remote_object_ids:
        for remote_id in remote_object_ids:
            if await acl.can_access(session, user, "remote", remote_id):
                row = await session.get(RemoteObject, remote_id)
                if row is not None:
                    items.append(_remote_item(row))
                    continue
            skipped += 1

    if episode_ids:
        episode_result = await session.execute(
            select(PodcastEpisode)
            .where(PodcastEpisode.id.in_(episode_ids))
            .options(selectinload(PodcastEpisode.podcast))
        )
        episodes_by_id = {episode.id: episode for episode in episode_result.scalars().all()}
        for episode_id in episode_ids:
            episode = episodes_by_id.get(episode_id)
            if episode is None:
                skipped += 1
            else:
                items.append(_episode_item(episode))

    if album_id is not None:
        await _require_container_access(session, user, "album", album_id)
        tracks, _ = await music.list_tracks(session, album_id=album_id, user=user, limit=max_items, include={"artist"})
        items.extend(_track_item(track) for track in tracks)

    if artist_id is not None:
        await _require_container_access(session, user, "artist", artist_id)
        tracks, _ = await music.list_tracks(
            session,
            artist_id=artist_id,
            user=user,
            limit=max_items,
            include={"artist"},
            sort_by="title",
            sort_dir="asc",
        )
        items.extend(_track_item(track) for track in tracks)

    if library_id is not None:
        await _require_container_access(session, user, "library", library_id)
        tracks = await music.list_library_tracks(session, library_id, user=user, limit=max_items, include={"artist"})
        items.extend(_track_item(track) for track in tracks)

    if playlist_id is not None:
        await _require_container_access(session, user, "playlist", playlist_id)
        entries = await music.list_playlist_items(session, playlist_id, user=user, limit=max_items, include={"artist"})
        for entry in entries:
            if entry.track is not None:
                items.append(_track_item(entry.track))
            elif entry.episode is not None:
                items.append(_episode_item(entry.episode))
            elif entry.remote_object is not None:
                items.append(_remote_item(entry.remote_object))

    return items[:max_items], skipped


async def derive_label(
    session: AsyncSession,
    *,
    item_count: int,
    album_id: Optional[str] = None,
    artist_id: Optional[str] = None,
    playlist_id: Optional[str] = None,
    library_id: Optional[str] = None,
) -> str:
    """Derive a human-readable archive label from the requested container."""
    if album_id is not None:
        album = await session.get(Album, album_id)
        if album is not None:
            return album.title
    if artist_id is not None:
        artist = await session.get(Artist, artist_id)
        if artist is not None:
            return artist.name
    if playlist_id is not None:
        playlist = await session.get(Playlist, playlist_id)
        if playlist is not None:
            return playlist.name
    if library_id is not None:
        library = await session.get(Library, library_id)
        if library is not None:
            return library.name
    return f"{item_count} tracks"


def sanitize_member_name(name: str) -> str:
    """Make ``name`` safe as a single ZIP path segment."""
    name = _FILENAME_BAD_CHARS.sub("_", name).strip().strip(".")
    if len(name) > _ARCNAME_MAX:
        name = name[:_ARCNAME_MAX].rstrip()
    return name or "item"


def _extension_for(content_type: Optional[str], *candidates: Optional[str]) -> str:
    """Pick a file extension from a MIME type or candidate filenames/URLs."""
    for candidate in candidates:
        if not candidate:
            continue
        suffix = PurePosixPath(str(candidate).split("?", 1)[0]).suffix
        if suffix and len(suffix) <= 6:
            return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed
    return ""


async def _with_retries(
    config: SonghiveConfig,
    fn: Callable[[], Awaitable[_T]],
    *,
    retryable: Callable[[Exception], bool],
) -> _T:
    """Await ``fn`` with exponential backoff while ``retryable`` says so."""
    attempts = config.downloads.fetch_attempts
    delay = config.downloads.fetch_backoff_seconds
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            if not retryable(exc) or attempt + 1 >= attempts:
                raise
            await asyncio.sleep(delay * (2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover


def _remote_retryable(exc: Exception) -> bool:
    """Only transient remote-fetch failures are worth retrying."""
    if isinstance(exc, FetchNotFound):
        return False
    if isinstance(exc, FetchError):
        # Policy/validation rejections (4xx) never succeed on retry.
        return exc.status_code >= 500
    return True


def _external_retryable(exc: Exception) -> bool:
    return not isinstance(exc, (ExternalItemNotFound, UnsupportedExternalOperation))


async def _materialize_track(
    item: ArchiveItem,
    workdir: Path,
    storage: StorageService,
    config: SonghiveConfig,
) -> tuple[Path, str, bool]:
    """
    Materialize a local/external track.

    Returns ``(path, extension, temporary)`` — ``temporary`` marks paths
    outside ``workdir`` that must be deleted after the archive is written.
    """
    from ..models.base import get_session
    from ..storage.local import LocalStorage

    async with get_session() as session:
        stored = await resolve_track_file(session, item.ref)
    if stored is not None:
        path = await storage.backend.retrieve(stored.storage_path)
        if path is None:
            raise FileNotFoundError(f"Stored file missing for track {item.ref}")
        ext = _extension_for(stored.content_type, stored.original_filename)
        # Local backends return the real storage path (never delete it);
        # remote backends return a fresh temp file the caller must remove.
        return path, ext, not isinstance(storage.backend, LocalStorage)

    async def _fetch_external() -> tuple[Path, Optional[str]]:
        async with get_session() as session:
            stream = await resolve_external_download_stream(session, item.ref)
        if stream is None:
            raise FileNotFoundError(f"No backing file for track {item.ref}")
        if stream.kind == "url" and stream.url:
            dest = workdir / f"{item.ref}.bin"
            await asyncio.to_thread(
                guarded_download,
                stream.url,
                dest,
                headers=dict(stream.headers) if stream.headers else None,
                timeout=config.downloads.fetch_timeout_seconds,
                max_bytes=config.downloads.max_item_bytes,
            )
            return dest, stream.content_type
        payload = await collect_external_stream(stream, config)
        if isinstance(payload, Path):
            if stream.temporary or stream.kind != "path":
                dest = workdir / f"{item.ref}{payload.suffix or '.bin'}"
                await asyncio.to_thread(shutil.move, str(payload), dest)
                return dest, stream.content_type
            return payload, stream.content_type
        dest = workdir / f"{item.ref}.bin"
        await asyncio.to_thread(dest.write_bytes, payload)
        return dest, stream.content_type

    # Extension hints: the provider's own filename/key beats a MIME guess;
    # the track's recorded mime is the last fallback.
    async with get_session() as session:
        track = await session.get(
            Track,
            item.ref,
            options=[
                selectinload(Track.external_track),
                selectinload(Track.external_item),
            ],
        )
    external_ref = None
    if track is not None:
        external_ref = track.external_track or track.external_item
    name_hint = external_ref.provider_key if external_ref is not None else None
    mime_hint = track.audio_mime_type if track else None

    external_path, content_type = await _with_retries(config, _fetch_external, retryable=_external_retryable)
    return external_path, _extension_for(content_type or mime_hint, name_hint), False


async def _materialize_remote(
    item: ArchiveItem,
    workdir: Path,
    config: SonghiveConfig,
) -> tuple[Path, str]:
    """Fetch a remote object's playable media URL into ``workdir``."""
    from ..models.base import get_session

    async with get_session() as session:
        row = await session.get(RemoteObject, item.ref)
        if row is None:
            raise FileNotFoundError(f"Remote object {item.ref} not found")
        media_url = await remote_content.resolve_media_url(session, row)
    if not media_url:
        raise FileNotFoundError(f"Remote object {item.ref} has no playable media")

    suffix = _extension_for(None, media_url)
    dest = workdir / f"{item.ref}{suffix or '.bin'}"

    def _guard(url: str) -> None:
        remote_content.require_remote_domain(url, config)

    async def _fetch() -> object:
        assert media_url  # for mypy
        return await asyncio.to_thread(
            guarded_download,
            media_url,
            dest,
            timeout=config.downloads.fetch_timeout_seconds,
            max_bytes=config.downloads.max_item_bytes,
            check_url=_guard,
        )

    await _with_retries(config, _fetch, retryable=_remote_retryable)
    return dest, suffix


async def _materialize_episode(
    item: ArchiveItem,
    workdir: Path,
    config: SonghiveConfig,
) -> tuple[Path, str]:
    """Fetch a podcast episode enclosure into ``workdir`` (SSRF guard only)."""
    from ..models.base import get_session

    async with get_session() as session:
        episode = await session.get(PodcastEpisode, item.ref)
        if episode is None:
            raise FileNotFoundError(f"Podcast episode {item.ref} not found")
        audio_url = episode.audio_url
        audio_type = episode.audio_type

    ext = _extension_for(audio_type, audio_url)
    dest = workdir / f"{item.ref}{ext or '.bin'}"

    async def _fetch() -> object:
        return await asyncio.to_thread(
            guarded_download,
            audio_url,
            dest,
            timeout=config.downloads.fetch_timeout_seconds,
            max_bytes=config.downloads.max_item_bytes,
        )

    await _with_retries(config, _fetch, retryable=_remote_retryable)
    return dest, ext


def _write_zip(entries: Sequence[tuple[Path, str]], zip_path: Path) -> None:
    """Write ``(source_path, arcname)`` entries into a ZIP archive."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, arcname in entries:
            archive.write(source, arcname)


async def materialize_archive(
    storage: StorageService,
    config: SonghiveConfig,
    items: Sequence[dict],
    workdir: Path,
) -> tuple[Path, list[dict]]:
    """
    Materialize every item under ``workdir`` and return ``(zip_path, item_errors)``.

    Each failed item is recorded in ``item_errors`` as ``{ref, title, error}``
    and skipped; a completely empty archive raises ``ArchiveRequestError``.
    The caller owns ``workdir`` — the ZIP must be consumed before the
    directory is removed.
    """
    entries: list[tuple[Path, str]] = []
    temporary_paths: list[Path] = []
    item_errors: list[dict] = []
    used_names: set[str] = set()

    for position, raw in enumerate(items, start=1):
        item = ArchiveItem.from_dict(raw)
        try:
            if item.kind == ITEM_KIND_TRACK:
                path, ext, temporary = await _materialize_track(item, workdir, storage, config)
                if temporary:
                    temporary_paths.append(path)
            elif item.kind == ITEM_KIND_REMOTE:
                path, ext = await _materialize_remote(item, workdir, config)
            elif item.kind == ITEM_KIND_EPISODE:
                path, ext = await _materialize_episode(item, workdir, config)
            else:
                raise ValueError(f"Unknown archive item kind: {item.kind}")
        except Exception as exc:  # noqa: BLE001 — per-item failure recorded
            logger.warning("Archive item %s (%s) failed: %s", item.ref, item.kind, exc)
            item_errors.append({"ref": item.ref, "title": item.title, "error": str(exc)})
            continue

        if item.artist:
            base = sanitize_member_name(f"{position:02d} - {item.artist} - {item.title}")
        else:
            base = sanitize_member_name(f"{position:02d} - {item.title}")
        arcname = f"{base}{ext}"
        counter = 2
        while arcname in used_names:
            arcname = f"{base} ({counter}){ext}"
            counter += 1
        used_names.add(arcname)
        entries.append((path, arcname))

    if not entries:
        raise ArchiveRequestError("No items could be materialized for the archive")

    zip_path = workdir / "archive.zip"
    try:
        await asyncio.to_thread(_write_zip, entries, zip_path)
    finally:
        for path in temporary_paths:
            try:
                os.unlink(path)
            except OSError:
                pass

    return zip_path, item_errors


async def delete_archive_payload(
    session: AsyncSession,
    storage: StorageService,
    archive: DownloadArchive,
) -> None:
    """Delete an archive's backing ``StoredFile`` once nothing references it."""
    file_id = archive.archive_file_id
    if file_id is None:
        return
    remaining = await session.scalar(
        select(func.count())
        .select_from(DownloadArchive)
        .where(DownloadArchive.archive_file_id == file_id, DownloadArchive.id != archive.id)
    )
    if remaining:
        return
    stored = await session.get(StoredFile, file_id)
    if stored is None:
        return
    try:
        await storage.delete_file(stored)
    except Exception:
        logger.exception("Failed to delete archive backing file %s", stored.storage_path)
    await session.delete(stored)
