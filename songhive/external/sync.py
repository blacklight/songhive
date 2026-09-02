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
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.loader import load_config
from ..models.album import Album
from ..models.external_library import ExternalLibrary
from ..models.external_sync_run import ExternalSyncRun
from ..models.external_track import ExternalTrack
from ..models.library_track import LibraryTrack
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..services.import_ import _find_or_create_album, _find_or_create_artist
from ..services.redis import get_redis_client
from ..services.secrets import decrypt_json
from .errors import ExternalLibraryError
from .registry import get_external_adapter
from .types import ExternalItemRef, ExternalLibraryCapabilities, ExternalTrackMetadata

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

        batch: list[ExternalItemRef] = []
        batch_size = 100

        async for item in adapter.iter_items(config, since=since, scope=scope):
            batch.append(item)
            if len(batch) >= batch_size:
                for batch_item in batch:
                    async with session.begin_nested():
                        try:
                            assert run  # for mypy
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
                    assert run  # for mypy
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
                ExternalTrack.external_library_id == external_library_id,
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
        run.details = {"capabilities": external_library.capabilities}

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
