"""
External-library sync service.

Orchestrates per-library indexing, metadata conflict resolution,
hash-collision shadowing, tombstone preservation, and missing-item detection.
"""

import dataclasses
import hashlib
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from redis.asyncio import Redis
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.loader import load_config
from ..models.album import Album
from ..models.artist import Artist
from ..models.external_item import ExternalItem
from ..models.external_library import ExternalLibrary
from ..models.external_sync_run import ExternalSyncRun
from ..models.external_track import ExternalTrack
from ..models.library_track import LibraryTrack
from ..models.playlist import Playlist, PlaylistTrack
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..services.genres import set_genres_for_entity
from ..services.import_ import _find_or_create_album, _find_or_create_artist
from ..services.redis import get_redis_client
from ..services.secrets import decrypt_json
from .errors import ExternalLibraryError
from .registry import get_external_adapter
from .types import (
    ExternalAlbumMetadata,
    ExternalArtistMetadata,
    ExternalItemRef,
    ExternalLibraryCapabilities,
    ExternalPlaylistMetadata,
    ExternalTrackMetadata,
)

logger = logging.getLogger(__name__)


class MetadataDecision(str, Enum):
    """Outcome of applying provider metadata to a Songhive track."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    CONFLICT_WRITE_BACK = "conflict_write_back"


class RunCounters:
    """Mutable counters for a single sync run."""

    def __init__(self) -> None:
        self.items_seen: int = 0
        self.tracks_created: int = 0
        self.tracks_updated: int = 0
        self.tracks_shadowed: int = 0
        self.tracks_tombstoned: int = 0
        self.tracks_missing: int = 0
        self.tracks_failed: int = 0
        self.enrich_queue: set[str] = set()
        # Entity-import counters (entity-backed providers only); persisted in
        # ``ExternalSyncRun.details["entities"]`` rather than new columns.
        self.entity_counts: dict[str, int] = {}


def _utcnow() -> datetime:
    """Return a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def _sanitize_error(exc: Any) -> str:
    """Return a short, config-free string for an exception or message."""
    if isinstance(exc, str):
        return exc[:512]
    return str(exc)[:512]


def _decrypt_config(raw: Any) -> dict:
    """Decrypt an external-library config when it is stored as a Fernet token."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return decrypt_json(raw)
        except Exception as exc:
            raise ExternalLibraryError(
                "Failed to decrypt external library config; "
                "check that auth.secret_key matches the key used to encrypt it"
            ) from exc
    return {}


def _metadata_fingerprint(metadata: ExternalTrackMetadata) -> str:
    """SHA-256 of the stable, editable tag subset of provider metadata."""
    fingerprint_payload = {
        "title": metadata.title,
        "artist": metadata.artist,
        "album": metadata.album,
        "album_artist": metadata.album_artist,
        "track_number": metadata.track_number,
        "disc_number": metadata.disc_number,
        "duration": metadata.duration,
        "release_year": metadata.release_year,
        "genre": metadata.genre,
        "musicbrainz_id": metadata.musicbrainz_id,
    }
    payload_bytes = json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload_bytes).hexdigest()


def _resolve_sha256(
    item: ExternalItemRef,
    capabilities: ExternalLibraryCapabilities,
    config: dict,
) -> Optional[str]:
    """Choose a sha256 value for an item without downloading when possible."""
    if item.sha256:
        return item.sha256

    limits = capabilities.limits or {}
    checksum_algorithm = (limits.get("checksum_algorithm") or "").lower()
    if capabilities.compute_hash and checksum_algorithm == "sha256" and item.checksum:
        return item.checksum

    if not config.get("allow_hashing", True):
        return None

    return None


def _set_item_error(
    external_track: ExternalTrack,
    exc: Any,
    counters: RunCounters,
) -> None:
    """Mark a single external track as failed."""
    external_track.state = "error"
    external_track.sync_error = _sanitize_error(exc)
    external_track.last_seen_at = _utcnow()
    external_track.last_synced_at = _utcnow()
    counters.tracks_failed += 1
    logger.warning(
        "External item %s failed: %s",
        external_track.provider_key,
        external_track.sync_error,
    )


def _item_matches_existing(item: ExternalItemRef, external_track: ExternalTrack) -> bool:
    """Return True when the listing record proves the provider item is unchanged.

    ETags are compared when both sides carry one; otherwise the stored
    mtime/size pair must match exactly. Adapters that cannot detect changes
    from listing metadata alone must not advertise ``detect_changes``.
    """
    if item.etag or external_track.provider_etag:
        return item.etag is not None and item.etag == external_track.provider_etag
    return (
        item.mtime is not None
        and item.mtime == external_track.provider_mtime
        and item.size is not None
        and item.size == external_track.provider_size
    )


async def _load_existing_track(
    session: AsyncSession,
    external_track: ExternalTrack,
) -> Optional[Track]:
    """Return the Songhive track linked by ``external_track.track_id`` if any."""
    if external_track.track_id is None:
        return None
    result = await session.execute(select(Track).where(Track.id == external_track.track_id))
    return result.scalar_one_or_none()


async def _find_or_create_library_track(
    session: AsyncSession,
    library_id: str,
    track_id: str,
    added_by_id: Optional[str],
) -> LibraryTrack:
    """Return the LibraryTrack row for the pair, creating one if absent."""
    result = await session.execute(
        select(LibraryTrack)
        .where(
            LibraryTrack.library_id == library_id,
            LibraryTrack.track_id == track_id,
        )
        .limit(1)
    )
    library_track = result.scalar_one_or_none()
    if library_track is None:
        library_track = LibraryTrack(
            library_id=library_id,
            track_id=track_id,
            added_by_id=added_by_id,
        )
        session.add(library_track)
        await session.flush()
    return library_track


async def _remove_library_track(
    session: AsyncSession,
    library_id: str,
    track_id: str,
) -> None:
    """Remove the LibraryTrack association for the given library and track."""
    await session.execute(
        delete(LibraryTrack).where(
            LibraryTrack.library_id == library_id,
            LibraryTrack.track_id == track_id,
        )
    )


async def _apply_metadata(
    session: AsyncSession,
    track: Optional[Track],
    metadata: ExternalTrackMetadata,
    external_track: ExternalTrack,
    external_library: ExternalLibrary,
    capabilities: ExternalLibraryCapabilities,
    sha256: str,
) -> MetadataDecision:
    """Create, update, or leave a Songhive track untouched based on metadata rules."""
    current_fingerprint = _metadata_fingerprint(metadata)
    stored_fingerprint = external_track.metadata_fingerprint
    fingerprint_changed = stored_fingerprint is None or current_fingerprint != stored_fingerprint

    library = external_library.library
    owner_id = library.owner_id if library is not None else None
    visibility = library.visibility if library is not None else "private"

    if track is None:
        artist = await _find_or_create_artist(session, metadata.artist or "Unknown Artist")
        album: Optional[Album] = None
        if metadata.album:
            album = await _find_or_create_album(
                session,
                title=metadata.album,
                artist_id=str(artist.id),
                year=metadata.release_year,
                owner_id=owner_id,
                visibility=visibility,
            )

        mime_type = metadata.raw_metadata.get("mimetype") if metadata.raw_metadata else None
        if mime_type is None:
            mime_type = metadata.raw_metadata.get("mime_type") if metadata.raw_metadata else None

        new_track = Track(
            title=metadata.title,
            artist_id=str(artist.id),
            album_id=str(album.id) if album else None,
            track_number=metadata.track_number,
            disc_number=metadata.disc_number,
            duration=metadata.duration,
            genre=metadata.genre,
            musicbrainz_id=metadata.musicbrainz_id,
            release_year=metadata.release_year,
            source="external",
            owner_id=owner_id,
            visibility=visibility,
            audio_file_id=None,
            audio_mime_type=mime_type,
            raw_metadata=metadata.raw_metadata,
            metadata_updated_at=None,
            external_metadata_synced_at=_utcnow(),
        )
        session.add(new_track)
        await session.flush()
        external_track.track_id = str(new_track.id)
        external_track.write_back_pending = False
        external_track.write_back_error = None
        return MetadataDecision.CREATED

    local_edited = track.metadata_updated_at is not None and (
        track.external_metadata_synced_at is None or track.metadata_updated_at > track.external_metadata_synced_at
    )

    if local_edited and fingerprint_changed:
        external_track.raw_metadata = metadata.raw_metadata
        if capabilities.write_tags:
            external_track.write_back_pending = True
            external_track.write_back_error = None
            external_track.sync_error = None
        else:
            external_track.write_back_pending = False
            external_track.sync_error = _sanitize_error(
                "Metadata conflict: provider has changed, but this adapter does not support write-back."
            )
        return MetadataDecision.CONFLICT_WRITE_BACK

    if fingerprint_changed:
        artist = await _find_or_create_artist(session, metadata.artist or "Unknown Artist")
        album = None
        if metadata.album:
            album = await _find_or_create_album(
                session,
                title=metadata.album,
                artist_id=str(artist.id),
                year=metadata.release_year,
                owner_id=track.owner_id,
                visibility=track.visibility,
            )

        track.title = metadata.title
        track.artist_id = str(artist.id)
        track.album_id = str(album.id) if album else None
        track.track_number = metadata.track_number
        track.disc_number = metadata.disc_number
        track.duration = metadata.duration
        track.genre = metadata.genre
        track.musicbrainz_id = metadata.musicbrainz_id
        track.release_year = metadata.release_year
        track.raw_metadata = metadata.raw_metadata
        track.external_metadata_synced_at = _utcnow()

        mime_type = metadata.raw_metadata.get("mimetype") if metadata.raw_metadata else None
        if mime_type is None:
            mime_type = metadata.raw_metadata.get("mime_type") if metadata.raw_metadata else None
        if mime_type:
            track.audio_mime_type = mime_type

        external_track.write_back_pending = False
        external_track.write_back_error = None
        return MetadataDecision.UPDATED

    track.external_metadata_synced_at = _utcnow()
    external_track.write_back_pending = False
    external_track.write_back_error = None
    return MetadataDecision.UNCHANGED


async def _mark_track_shadowed(
    session: AsyncSession,
    external_library: ExternalLibrary,
    external_track: ExternalTrack,
    item: ExternalItemRef,
    sha256: str,
    counters: RunCounters,
) -> None:
    """Mark an external track as shadowed by an identical StoredFile."""
    if external_track.state == "active" and external_track.track_id is not None:
        await _remove_library_track(session, str(external_library.library_id), str(external_track.track_id))
        external_track.track_id = None
    external_track.sha256 = sha256
    external_track.provider_etag = item.etag
    external_track.provider_mtime = item.mtime
    external_track.provider_size = item.size
    external_track.provider_mime_type = item.mime_type
    external_track.provider_checksum = item.checksum
    external_track.state = "shadowed"
    external_track.last_seen_at = _utcnow()
    external_track.last_synced_at = _utcnow()
    external_track.sync_error = None
    counters.tracks_shadowed += 1


async def _process_item(
    session: AsyncSession,
    external_library: ExternalLibrary,
    adapter: Any,
    capabilities: ExternalLibraryCapabilities,
    item: ExternalItemRef,
    run: ExternalSyncRun,
    counters: RunCounters,
    include_tombstones: bool,
    config: dict,
) -> None:
    """Process a single provider item inside a savepoint."""
    result = await session.execute(
        select(ExternalTrack)
        .where(
            ExternalTrack.external_library_id == str(external_library.id),
            ExternalTrack.provider_key == item.provider_key,
        )
        .limit(1)
    )
    external_track = result.scalar_one_or_none()

    if external_track is None:
        external_track = ExternalTrack(
            external_library_id=str(external_library.id),
            provider_key=item.provider_key,
        )
        session.add(external_track)

    counters.items_seen += 1

    if external_track.state == "tombstoned" and not include_tombstones:
        return

    if (
        capabilities.detect_changes
        and external_track.state == "active"
        and external_track.track_id is not None
        and external_track.sha256
        and _item_matches_existing(item, external_track)
    ):
        # The listing proves the item is unchanged: skip hashing and metadata
        # reads (which may require downloading the object) but still honour
        # shadowing when a matching StoredFile appeared since the last sync.
        stored_result = await session.execute(
            select(StoredFile).where(StoredFile.sha256 == external_track.sha256).limit(1)
        )
        if stored_result.scalar_one_or_none() is not None:
            await _mark_track_shadowed(session, external_library, external_track, item, external_track.sha256, counters)
            return
        track = await _load_existing_track(session, external_track)
        if track is not None:
            external_track.last_seen_at = _utcnow()
            external_track.last_synced_at = _utcnow()
            external_track.sync_error = None
            if track.musicbrainz_enriched_at is None:
                counters.enrich_queue.add(str(track.id))
            return
        # The linked Songhive track is gone: fall through and re-create it.

    sha256 = _resolve_sha256(item, capabilities, config)
    if not sha256:
        if capabilities.compute_hash and config.get("allow_hashing", True):
            try:
                sha256 = await adapter.compute_sha256(config, item)
            except Exception as exc:
                _set_item_error(external_track, exc, counters)
                return
        else:
            _set_item_error(external_track, "No sha256 available and hashing is disabled or unsupported", counters)
            return

    result = await session.execute(select(StoredFile).where(StoredFile.sha256 == sha256).limit(1))
    stored_file = result.scalar_one_or_none()
    if stored_file is not None:
        await _mark_track_shadowed(session, external_library, external_track, item, sha256, counters)
        return

    try:
        metadata = await adapter.read_metadata(config, item)
    except Exception as exc:
        _set_item_error(external_track, exc, counters)
        return

    track = await _load_existing_track(session, external_track)
    decision = await _apply_metadata(
        session,
        track,
        metadata,
        external_track,
        external_library,
        capabilities,
        sha256,
    )

    track = await _load_existing_track(session, external_track)
    if (
        track is not None
        and track.musicbrainz_enriched_at is None
        and decision
        in (
            MetadataDecision.CREATED,
            MetadataDecision.UPDATED,
            MetadataDecision.UNCHANGED,
        )
    ):
        counters.enrich_queue.add(str(track.id))

    if (
        decision
        in (
            MetadataDecision.CREATED,
            MetadataDecision.UPDATED,
            MetadataDecision.UNCHANGED,
        )
        and external_track.track_id is not None
    ):
        added_by_id = run.triggered_by_user_id or external_library.created_by_id
        await _find_or_create_library_track(
            session,
            str(external_library.library_id),
            str(external_track.track_id),
            added_by_id,
        )

    external_track.provider_etag = item.etag
    external_track.provider_mtime = item.mtime
    external_track.provider_size = item.size
    external_track.provider_mime_type = item.mime_type
    external_track.provider_checksum = item.checksum
    external_track.sha256 = sha256
    if decision != MetadataDecision.CONFLICT_WRITE_BACK:
        external_track.metadata_fingerprint = _metadata_fingerprint(metadata)
    external_track.raw_metadata = metadata.raw_metadata
    external_track.state = "active"
    external_track.last_seen_at = _utcnow()
    external_track.last_synced_at = _utcnow()
    if decision == MetadataDecision.CONFLICT_WRITE_BACK:
        external_track.sync_error = _sanitize_error(
            "Metadata conflict: Songhive edits are newer than the last provider sync."
        )
    else:
        external_track.sync_error = None

    if decision == MetadataDecision.CREATED:
        counters.tracks_created += 1
    elif decision == MetadataDecision.UPDATED:
        counters.tracks_updated += 1


# ---------------------------------------------------------------------------
# Entity-backed providers (Jellyfin, Spotify, Tidal, …)
#
# These providers enumerate first-class entities rather than files: there are
# no local bytes, no hashing/shadowing, and metadata arrives inline in the
# listing instead of via embedded tags. Their items are referenced by
# ``ExternalItem`` rows and materialize as local ``Track``/``Album``/
# ``Artist``/``Playlist`` rows so they behave as normal entities.
# ---------------------------------------------------------------------------


def _entity_fingerprint(payload: Optional[dict]) -> str:
    """SHA-256 of a provider entity payload, for etag-less change detection."""
    return hashlib.sha256(json.dumps(payload or {}, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _entity_unchanged(external_item: ExternalItem, etag: Optional[str], fingerprint: str) -> bool:
    """Return True when the provider listing proves the entity is unchanged."""
    if etag or external_item.provider_etag:
        return etag is not None and etag == external_item.provider_etag
    stored = (external_item.raw_metadata or {}).get("_fingerprint")
    return stored == fingerprint


async def _find_external_item(
    session: AsyncSession,
    external_library_id: str,
    kind: str,
    provider_key: str,
) -> Optional[ExternalItem]:
    result = await session.execute(
        select(ExternalItem)
        .where(
            ExternalItem.external_library_id == external_library_id,
            ExternalItem.kind == kind,
            ExternalItem.provider_key == provider_key,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _upsert_external_item(
    session: AsyncSession,
    external_library_id: str,
    kind: str,
    provider_key: str,
    *,
    entity_id: str,
    etag: Optional[str],
    mtime: Optional[datetime],
    raw_metadata: Optional[dict],
    fingerprint: str,
) -> ExternalItem:
    """Create or refresh the ``ExternalItem`` reference for an entity."""
    entity_column = {
        "track": "track_id",
        "album": "album_id",
        "artist": "artist_id",
        "playlist": "playlist_id",
    }[kind]
    item = await _find_external_item(session, external_library_id, kind, provider_key)
    if item is None:
        item = ExternalItem(
            external_library_id=external_library_id,
            kind=kind,
            provider_key=provider_key,
            **{entity_column: entity_id},
        )
        session.add(item)
    else:
        setattr(item, entity_column, entity_id)
    item.provider_etag = etag
    item.provider_mtime = mtime
    item.raw_metadata = {**(raw_metadata or {}), "_fingerprint": fingerprint}
    item.state = "active"
    item.last_seen_at = _utcnow()
    item.last_synced_at = _utcnow()
    item.sync_error = None
    await session.flush()
    return item


def _entity_bump(counters: RunCounters, key: str) -> None:
    counters.entity_counts[key] = counters.entity_counts.get(key, 0) + 1


async def _find_entity_artist(
    session: AsyncSession,
    name: str,
    *,
    musicbrainz_id: Optional[str] = None,
    match_musicbrainz: bool = False,
) -> Artist:
    """Find-or-create an artist, optionally matching on a MusicBrainz id first."""
    if match_musicbrainz and musicbrainz_id:
        result = await session.execute(select(Artist).where(Artist.musicbrainz_id == musicbrainz_id).limit(1))
        artist = result.scalar_one_or_none()
        if artist is not None:
            return artist
    return await _find_or_create_artist(session, name)


async def _find_entity_album(
    session: AsyncSession,
    *,
    title: str,
    artist_id: str,
    year: Optional[int],
    owner_id: Optional[str],
    visibility: str,
    musicbrainz_id: Optional[str] = None,
    match_musicbrainz: bool = False,
) -> Album:
    """Find-or-create an album, optionally matching on a MusicBrainz id first."""
    if match_musicbrainz and musicbrainz_id:
        result = await session.execute(select(Album).where(Album.musicbrainz_id == musicbrainz_id).limit(1))
        album = result.scalar_one_or_none()
        if album is not None:
            return album
    return await _find_or_create_album(
        session,
        title=title,
        artist_id=artist_id,
        year=year,
        owner_id=owner_id,
        visibility=visibility,
    )


async def _apply_entity_track(
    session: AsyncSession,
    external_library: ExternalLibrary,
    item: ExternalItemRef,
    run: ExternalSyncRun,
    counters: RunCounters,
    config: dict,
) -> Optional[Track]:
    """Materialize or update a local ``Track`` from an entity-provider item."""
    metadata = item.metadata
    if metadata is None:
        return None

    library = external_library.library
    owner_id = library.owner_id if library is not None else None
    visibility = library.visibility if library is not None else "private"
    match_musicbrainz = bool(config.get("dedup_musicbrainz"))

    fingerprint = _entity_fingerprint(metadata.raw_metadata)
    external_item = await _find_external_item(session, str(external_library.id), "track", item.provider_key)

    track: Optional[Track] = None
    if external_item is not None and external_item.track_id is not None:
        result = await session.execute(select(Track).where(Track.id == external_item.track_id))
        track = result.scalar_one_or_none()

    counters.items_seen += 1

    if (
        external_item is not None
        and external_item.state == "active"
        and track is not None
        and _entity_unchanged(external_item, item.etag, fingerprint)
    ):
        external_item.last_seen_at = _utcnow()
        external_item.last_synced_at = _utcnow()
        external_item.sync_error = None
        if config.get("sync_metadata") and track.musicbrainz_enriched_at is None:
            counters.enrich_queue.add(str(track.id))
        return track

    extra_artists = list(metadata.artists[1:]) if len(metadata.artists) > 1 else None
    track_genres = list(metadata.genres) or ([metadata.genre] if metadata.genre else [])
    raw_track_metadata = {
        **(metadata.raw_metadata or {}),
        "display_path": item.display_path,
    }
    if item.size is not None:
        # Normalize the stream size onto raw_metadata["Size"] — streaming reads
        # it for Range parsing, and providers nest it differently (Jellyfin
        # puts it under MediaSources).
        raw_track_metadata["Size"] = item.size

    if track is None:
        artist = await _find_entity_artist(
            session,
            metadata.artist or "Unknown Artist",
            musicbrainz_id=(metadata.provider_ids or {}).get("MusicBrainzArtist"),
            match_musicbrainz=match_musicbrainz,
        )
        album: Optional[Album] = None
        if metadata.album:
            album_artist = await _find_entity_artist(
                session,
                metadata.album_artist or metadata.artist or "Unknown Artist",
                musicbrainz_id=None,
                match_musicbrainz=False,
            )
            album = await _find_entity_album(
                session,
                title=metadata.album,
                artist_id=str(album_artist.id),
                year=metadata.release_year,
                owner_id=owner_id,
                visibility=visibility,
                musicbrainz_id=(metadata.provider_ids or {}).get("MusicBrainzAlbum"),
                match_musicbrainz=match_musicbrainz,
            )

        track = Track(
            title=metadata.title,
            artist_id=str(artist.id),
            album_id=str(album.id) if album else None,
            track_number=metadata.track_number,
            disc_number=metadata.disc_number,
            duration=metadata.duration,
            genre=metadata.genre or (track_genres[0] if track_genres else None),
            description=metadata.description,
            musicbrainz_id=metadata.musicbrainz_id,
            release_year=metadata.release_year,
            extra_artists=extra_artists,
            source="external",
            owner_id=owner_id,
            visibility=visibility,
            audio_file_id=None,
            audio_mime_type=item.mime_type,
            raw_metadata=raw_track_metadata,
            metadata_updated_at=None,
            external_metadata_synced_at=_utcnow(),
        )
        session.add(track)
        await session.flush()
        _entity_bump(counters, "tracks_created")
    else:
        local_edited = track.metadata_updated_at is not None and (
            track.external_metadata_synced_at is None or track.metadata_updated_at > track.external_metadata_synced_at
        )
        fingerprint_changed = True
        if external_item is not None:
            stored = (external_item.raw_metadata or {}).get("_fingerprint")
            fingerprint_changed = stored is None or stored != fingerprint

        if local_edited and fingerprint_changed:
            # Provider-side changed but the local copy has newer edits and
            # entity providers cannot write back: keep local edits, record
            # the conflict on the reference row.
            if external_item is not None:
                external_item.sync_error = _sanitize_error(
                    "Metadata conflict: provider has changed, but entity providers do not support write-back."
                )
                external_item.last_seen_at = _utcnow()
                external_item.last_synced_at = _utcnow()
            return track

        artist = await _find_entity_artist(
            session,
            metadata.artist or "Unknown Artist",
            musicbrainz_id=(metadata.provider_ids or {}).get("MusicBrainzArtist"),
            match_musicbrainz=match_musicbrainz,
        )
        album = None
        if metadata.album:
            album_artist = await _find_entity_artist(
                session,
                metadata.album_artist or metadata.artist or "Unknown Artist",
                musicbrainz_id=None,
                match_musicbrainz=False,
            )
            album = await _find_entity_album(
                session,
                title=metadata.album,
                artist_id=str(album_artist.id),
                year=metadata.release_year,
                owner_id=track.owner_id,
                visibility=track.visibility,
                musicbrainz_id=(metadata.provider_ids or {}).get("MusicBrainzAlbum"),
                match_musicbrainz=match_musicbrainz,
            )

        track.title = metadata.title
        track.artist_id = str(artist.id)
        track.album_id = str(album.id) if album else None
        track.track_number = metadata.track_number
        track.disc_number = metadata.disc_number
        track.duration = metadata.duration
        track.genre = metadata.genre or (track_genres[0] if track_genres else None)
        track.description = metadata.description
        track.musicbrainz_id = metadata.musicbrainz_id
        track.release_year = metadata.release_year
        track.extra_artists = extra_artists
        track.raw_metadata = raw_track_metadata
        track.external_metadata_synced_at = _utcnow()
        if item.mime_type:
            track.audio_mime_type = item.mime_type
        _entity_bump(counters, "tracks_updated")

    if track_genres:
        await set_genres_for_entity(session, "track", track.id, track_genres)

    added_by_id = run.triggered_by_user_id or external_library.created_by_id
    await _find_or_create_library_track(
        session,
        str(external_library.library_id),
        str(track.id),
        added_by_id,
    )

    await _upsert_external_item(
        session,
        str(external_library.id),
        "track",
        item.provider_key,
        entity_id=str(track.id),
        etag=item.etag,
        mtime=item.mtime,
        raw_metadata=raw_track_metadata,
        fingerprint=fingerprint,
    )

    if config.get("sync_metadata") and track.musicbrainz_enriched_at is None:
        counters.enrich_queue.add(str(track.id))

    return track


async def _apply_entity_artist(
    session: AsyncSession,
    external_library: ExternalLibrary,
    item: ExternalArtistMetadata,
    counters: RunCounters,
    config: dict,
) -> Optional[Artist]:
    """Materialize or update a local ``Artist`` from a provider artist."""
    fingerprint = _entity_fingerprint(item.raw_metadata)
    external_item = await _find_external_item(session, str(external_library.id), "artist", item.provider_key)
    counters.items_seen += 1

    artist: Optional[Artist] = None
    if external_item is not None and external_item.artist_id is not None:
        artist = await session.get(Artist, external_item.artist_id)

    if (
        external_item is not None
        and external_item.state == "active"
        and artist is not None
        and _entity_unchanged(external_item, item.etag, fingerprint)
    ):
        external_item.last_seen_at = _utcnow()
        external_item.last_synced_at = _utcnow()
        external_item.sync_error = None
        return artist

    artist = await _find_entity_artist(
        session,
        item.name or "Unknown Artist",
        musicbrainz_id=item.provider_ids.get("MusicBrainzArtist"),
        match_musicbrainz=bool(config.get("dedup_musicbrainz")),
    )
    if item.bio and not artist.bio:
        artist.bio = item.bio
    if item.image_url and not artist.image_url:
        artist.image_url = item.image_url
    if item.provider_ids.get("MusicBrainzArtist") and not artist.musicbrainz_id:
        artist.musicbrainz_id = item.provider_ids["MusicBrainzArtist"]
    _entity_bump(counters, "artists_synced")

    await _upsert_external_item(
        session,
        str(external_library.id),
        "artist",
        item.provider_key,
        entity_id=str(artist.id),
        etag=item.etag,
        mtime=item.mtime,
        raw_metadata=item.raw_metadata,
        fingerprint=fingerprint,
    )
    return artist


async def _apply_entity_album(
    session: AsyncSession,
    external_library: ExternalLibrary,
    item: ExternalAlbumMetadata,
    counters: RunCounters,
    config: dict,
) -> Optional[Album]:
    """Materialize or update a local ``Album`` from a provider album."""
    library = external_library.library
    owner_id = library.owner_id if library is not None else None
    visibility = library.visibility if library is not None else "private"
    match_musicbrainz = bool(config.get("dedup_musicbrainz"))

    fingerprint = _entity_fingerprint(item.raw_metadata)
    external_item = await _find_external_item(session, str(external_library.id), "album", item.provider_key)
    counters.items_seen += 1

    album: Optional[Album] = None
    if external_item is not None and external_item.album_id is not None:
        album = await session.get(Album, external_item.album_id)

    if (
        external_item is not None
        and external_item.state == "active"
        and album is not None
        and _entity_unchanged(external_item, item.etag, fingerprint)
    ):
        external_item.last_seen_at = _utcnow()
        external_item.last_synced_at = _utcnow()
        external_item.sync_error = None
        return album

    artist_name = item.artist_names[0] if item.artist_names else "Unknown Artist"
    artist = await _find_entity_artist(session, artist_name, match_musicbrainz=match_musicbrainz)
    album = await _find_entity_album(
        session,
        title=item.title or "Unknown Album",
        artist_id=str(artist.id),
        year=item.release_year,
        owner_id=owner_id,
        visibility=visibility,
        musicbrainz_id=item.provider_ids.get("MusicBrainzAlbum"),
        match_musicbrainz=match_musicbrainz,
    )
    if item.release_year is not None:
        album.release_year = item.release_year
    if item.cover_url and not album.cover_url:
        album.cover_url = item.cover_url
    if item.description and not album.description:
        album.description = item.description
    if item.provider_ids.get("MusicBrainzAlbum") and not album.musicbrainz_id:
        album.musicbrainz_id = item.provider_ids["MusicBrainzAlbum"]
    _entity_bump(counters, "albums_synced")

    if item.genres:
        await set_genres_for_entity(session, "album", album.id, list(item.genres))

    await _upsert_external_item(
        session,
        str(external_library.id),
        "album",
        item.provider_key,
        entity_id=str(album.id),
        etag=item.etag,
        mtime=item.mtime,
        raw_metadata=item.raw_metadata,
        fingerprint=fingerprint,
    )
    return album


async def _apply_entity_playlist(
    session: AsyncSession,
    external_library: ExternalLibrary,
    item: ExternalPlaylistMetadata,
    counters: RunCounters,
) -> Optional[Playlist]:
    """Materialize or update a local ``Playlist`` from a provider playlist."""
    library = external_library.library
    owner_id = library.owner_id if library is not None else None
    visibility = library.visibility if library is not None else "private"

    # Entries are part of the playlist state: fold them into the fingerprint
    # so a pure reorder without an etag bump still rewrites PlaylistTrack rows.
    fingerprint = _entity_fingerprint(
        {
            "raw": item.raw_metadata,
            "entries": [[e.track_provider_key, e.position] for e in item.entries],
        }
    )
    external_item = await _find_external_item(session, str(external_library.id), "playlist", item.provider_key)
    counters.items_seen += 1

    playlist: Optional[Playlist] = None
    if external_item is not None and external_item.playlist_id is not None:
        playlist = await session.get(Playlist, external_item.playlist_id)

    if (
        external_item is not None
        and external_item.state == "active"
        and playlist is not None
        and _entity_unchanged(external_item, item.etag, fingerprint)
    ):
        external_item.last_seen_at = _utcnow()
        external_item.last_synced_at = _utcnow()
        external_item.sync_error = None
        return playlist

    if playlist is None:
        playlist = Playlist(
            name=item.title or "Untitled playlist",
            owner_id=owner_id,
            visibility=visibility,
            description=item.description,
        )
        session.add(playlist)
        await session.flush()
        _entity_bump(counters, "playlists_created")
    else:
        playlist.name = item.title or playlist.name
        if item.description is not None:
            playlist.description = item.description

    # Provider playlists are authoritative: replace the ordered entries.
    await session.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == str(playlist.id)))
    skipped = 0
    for entry in item.entries:
        ref = await _find_external_item(session, str(external_library.id), "track", entry.track_provider_key)
        if ref is None or ref.track_id is None:
            skipped += 1
            continue
        session.add(
            PlaylistTrack(
                playlist_id=str(playlist.id),
                track_id=ref.track_id,
                position=entry.position,
            )
        )
    if skipped:
        logger.info(
            "Playlist %s: skipped %d provider entries with no imported track",
            item.provider_key,
            skipped,
        )
        counters.entity_counts["playlist_entries_skipped"] = (
            counters.entity_counts.get("playlist_entries_skipped", 0) + skipped
        )
    await session.flush()
    _entity_bump(counters, "playlists_synced")

    await _upsert_external_item(
        session,
        str(external_library.id),
        "playlist",
        item.provider_key,
        entity_id=str(playlist.id),
        etag=item.etag,
        mtime=item.mtime,
        raw_metadata=item.raw_metadata,
        fingerprint=fingerprint,
    )
    return playlist


async def _reconcile_missing_entities(
    session: AsyncSession,
    external_library: ExternalLibrary,
    capabilities: ExternalLibraryCapabilities,
    run: ExternalSyncRun,
    counters: RunCounters,
) -> None:
    """Mark ``ExternalItem`` rows absent from the listing as missing and
    reconcile the local entities they reference.

    Playlists are provider-owned and deleted. Albums/artists are deleted only
    when no other track/album still references them (guard against orphaning
    local content sharing the entity). Tracks keep their row — favorites,
    history and publications may reference it.

    Only kinds whose listing pass ran this sync are reconciled: a disabled
    ``include_*`` toggle must not tombstone previously imported entities.
    """
    kinds = [
        kind
        for kind, enabled in (
            ("track", capabilities.list_items),
            ("album", capabilities.list_albums),
            ("artist", capabilities.list_artists),
            ("playlist", capabilities.list_playlists),
        )
        if enabled
    ]
    if not kinds:
        return

    library_id = str(external_library.library_id)
    result = await session.execute(
        select(ExternalItem).where(
            ExternalItem.external_library_id == str(external_library.id),
            ExternalItem.kind.in_(kinds),
            ExternalItem.state == "active",
            ExternalItem.last_seen_at < run.started_at,
        )
    )
    for item in result.scalars().all():
        item.state = "missing"
        item.sync_error = None
        counters.tracks_missing += 1

        if item.kind == "track" and item.track_id is not None:
            await _remove_library_track(session, library_id, item.track_id)
        elif item.kind == "playlist" and item.playlist_id is not None:
            playlist = await session.get(Playlist, item.playlist_id)
            if playlist is not None:
                await session.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == str(playlist.id)))
                await session.delete(playlist)
            await session.delete(item)
        elif item.kind == "album" and item.album_id is not None:
            still_used = await session.scalar(
                select(func.count()).select_from(Track).where(Track.album_id == item.album_id)
            )
            if not still_used:
                album = await session.get(Album, item.album_id)
                if album is not None:
                    await session.delete(album)
                    await session.delete(item)
        elif item.kind == "artist" and item.artist_id is not None:
            track_refs = await session.scalar(
                select(func.count()).select_from(Track).where(Track.artist_id == item.artist_id)
            )
            album_refs = await session.scalar(
                select(func.count()).select_from(Album).where(Album.artist_id == item.artist_id)
            )
            if not track_refs and not album_refs:
                artist = await session.get(Artist, item.artist_id)
                if artist is not None:
                    await session.delete(artist)
                    await session.delete(item)


async def _sync_entity_library(
    session: AsyncSession,
    external_library: ExternalLibrary,
    adapter: Any,
    capabilities: ExternalLibraryCapabilities,
    run: ExternalSyncRun,
    counters: RunCounters,
    config: dict,
    since: Optional[datetime],
    scope: Optional[str],
) -> None:
    """Run the entity-import passes for an entity-backed provider."""

    async def _guard(label: str, provider_key: str, coro: Any) -> Any:
        async with session.begin_nested():
            try:
                return await coro
            except Exception:
                logger.exception("Unexpected failure processing %s %s", label, provider_key)
                counters.tracks_failed += 1
                return None

    if capabilities.list_items:
        async for item in adapter.iter_items(config, since=since, scope=scope):
            await _guard(
                "track",
                item.provider_key,
                _apply_entity_track(session, external_library, item, run, counters, config),
            )

    if capabilities.list_artists:
        async for item in adapter.iter_artists(config, since=since, scope=scope):
            await _guard(
                "artist",
                item.provider_key,
                _apply_entity_artist(session, external_library, item, counters, config),
            )

    if capabilities.list_albums:
        async for item in adapter.iter_albums(config, since=since, scope=scope):
            await _guard(
                "album",
                item.provider_key,
                _apply_entity_album(session, external_library, item, counters, config),
            )

    if capabilities.list_playlists:
        async for item in adapter.iter_playlists(config, since=since, scope=scope):
            await _guard(
                "playlist",
                item.provider_key,
                _apply_entity_playlist(session, external_library, item, counters),
            )

    if since is None:
        await _reconcile_missing_entities(session, external_library, capabilities, run, counters)


def _maybe_enqueue_musicbrainz(config: Any, track_ids: set[str]) -> None:
    """Queue MusicBrainz enrichment for tracks when enabled."""
    if not track_ids or not config.musicbrainz.enabled:
        return
    from ..tasks.musicbrainz import enrich_track

    for track_id in track_ids:
        try:
            enrich_track.delay(track_id)  # type: ignore
        except Exception:
            logger.exception("Failed to enqueue MusicBrainz enrichment for %s", track_id)


async def sync_external_library(
    session: AsyncSession,
    external_library_id: str,
    *,
    triggered_by: str,
    triggered_by_user_id: Optional[str] = None,
    include_tombstones: bool = False,
    sync_run_id: Optional[str] = None,
    since: Optional[datetime] = None,
    scope: Optional[str] = None,
    redis: Optional[Redis] = None,
) -> ExternalSyncRun:
    """Run a sync for the given external library and return the run record."""
    lock_key = f"external_sync:{external_library_id}"
    lock_acquired = False
    run: Optional[ExternalSyncRun] = None
    external_library: Optional[ExternalLibrary] = None
    run_created = False
    songhive_config = load_config([])
    counters = RunCounters()

    if redis is None:
        redis = get_redis_client(songhive_config)

    try:
        lock = await redis.set(lock_key, "1", nx=True, ex=3600)
        if not lock:
            raise ExternalLibraryError("sync already running")
        lock_acquired = True

        result = await session.execute(select(ExternalLibrary).where(ExternalLibrary.id == external_library_id))
        external_library = result.scalar_one_or_none()
        if external_library is None:
            raise ExternalLibraryError(f"External library {external_library_id} not found")
        if not external_library.enabled:
            raise ExternalLibraryError(f"External library {external_library_id} is disabled")

        raw_config = external_library.config
        config = _decrypt_config(raw_config)

        adapter_cls = get_external_adapter(external_library.provider_type)
        adapter = adapter_cls()
        capabilities = await adapter.validate_config(config)
        external_library.capabilities = dataclasses.asdict(capabilities)

        if sync_run_id is not None:
            run = await session.get(ExternalSyncRun, sync_run_id)  # type: ignore
            if run is not None and str(run.external_library_id) != external_library_id:
                logger.warning(
                    "Sync run %s belongs to a different external library; creating a new run.",
                    sync_run_id,
                )
                run = None
        if run is None:
            run = ExternalSyncRun(
                external_library_id=external_library_id,
                triggered_by=triggered_by,
                triggered_by_user_id=triggered_by_user_id,
            )
            run_created = True
        run.status = "running"
        run.started_at = _utcnow()
        run.triggered_by = triggered_by
        run.triggered_by_user_id = triggered_by_user_id
        run.error = None
        if run_created:
            session.add(run)

        assert run  # for mypy
        external_library.last_sync_started_at = run.started_at
        external_library.last_sync_status = "running"
        await session.flush()

        if (capabilities.limits or {}).get("entity_import"):
            # Entity-backed provider: tracks/albums/artists/playlists are
            # materialized as local rows referenced by ``ExternalItem``. The
            # file-track pass (hashing, shadowing, ``ExternalTrack``) does not
            # apply.
            await _sync_entity_library(
                session,
                external_library,
                adapter,
                capabilities,
                run,
                counters,
                config,
                since,
                scope,
            )
        else:
            await _sync_file_library(
                session,
                external_library,
                adapter,
                capabilities,
                run,
                counters,
                config,
                since,
                scope,
                include_tombstones,
            )

        if counters.tracks_failed:
            run.status = "partial"
            run.error = _sanitize_error(f"{counters.tracks_failed} item(s) failed")
        else:
            run.status = "success"
            run.error = None
        run.completed_at = _utcnow()
        run.items_seen = counters.items_seen
        run.tracks_created = counters.tracks_created
        run.tracks_updated = counters.tracks_updated
        run.tracks_shadowed = counters.tracks_shadowed
        run.tracks_tombstoned = counters.tracks_tombstoned
        run.tracks_missing = counters.tracks_missing
        run.tracks_failed = counters.tracks_failed
        run.details = {
            "capabilities": external_library.capabilities,
            "entities": counters.entity_counts,
        }

        external_library.last_sync_completed_at = run.completed_at
        external_library.last_sync_status = run.status
        external_library.last_sync_error = run.error

        await session.commit()
        _maybe_enqueue_musicbrainz(songhive_config, counters.enrich_queue)
        assert run  # for mypy
        return run
    except Exception as exc:
        if run is not None:
            try:
                run.status = "failed"
                run.completed_at = _utcnow()
                run.error = _sanitize_error(exc)
                if external_library is not None:
                    external_library.last_sync_status = "failed"
                    external_library.last_sync_error = run.error
                await session.flush()
                await session.commit()
                _maybe_enqueue_musicbrainz(songhive_config, counters.enrich_queue)
            except Exception:
                logger.exception("Failed to persist failed sync run for %s", external_library_id)
        if isinstance(exc, ExternalLibraryError):
            raise
        raise ExternalLibraryError(str(exc)) from exc
    finally:
        if lock_acquired:
            try:
                await redis.delete(lock_key)
            except Exception:
                logger.exception("Failed to release sync lock %s", lock_key)


async def _sync_file_library(
    session: AsyncSession,
    external_library: ExternalLibrary,
    adapter: Any,
    capabilities: ExternalLibraryCapabilities,
    run: ExternalSyncRun,
    counters: RunCounters,
    config: dict,
    since: Optional[datetime],
    scope: Optional[str],
    include_tombstones: bool,
) -> None:
    """Run the file-oriented pass: enumerate items into ``ExternalTrack``."""
    batch: list[ExternalItemRef] = []
    batch_size = 100

    async for item in adapter.iter_items(config, since=since, scope=scope):
        batch.append(item)
        if len(batch) >= batch_size:
            for batch_item in batch:
                async with session.begin_nested():
                    try:
                        await _process_item(
                            session,
                            external_library,
                            adapter,
                            capabilities,
                            batch_item,
                            run,
                            counters,
                            include_tombstones,
                            config,
                        )
                    except Exception:
                        logger.exception("Unexpected failure processing %s", batch_item.provider_key)
                        counters.tracks_failed += 1
            batch = []

    for batch_item in batch:
        async with session.begin_nested():
            try:
                await _process_item(
                    session,
                    external_library,
                    adapter,
                    capabilities,
                    batch_item,
                    run,
                    counters,
                    include_tombstones,
                    config,
                )
            except Exception:
                logger.exception("Unexpected failure processing %s", batch_item.provider_key)
                counters.tracks_failed += 1

    if capabilities.list_items and since is None:
        where_clause = [
            ExternalTrack.external_library_id == external_library.id,
            ExternalTrack.state == "active",
            ExternalTrack.last_seen_at < run.started_at,
        ]
        if scope is not None:
            scope_clean = scope.rstrip("/")
            if scope_clean:
                where_clause.append(
                    or_(
                        ExternalTrack.provider_key == scope_clean,
                        ExternalTrack.provider_key.startswith(f"{scope_clean}/", autoescape=True),
                    )
                )
        missing_result = await session.execute(select(ExternalTrack).where(*where_clause))
        for missing_track in missing_result.scalars().all():
            missing_track.state = "missing"
            missing_track.sync_error = None
            counters.tracks_missing += 1
