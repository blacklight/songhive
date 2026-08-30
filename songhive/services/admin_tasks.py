"""
Shared admin task helpers used by the CLI, API, and Celery workers.
"""

import asyncio
import logging
import os
from typing import Optional, cast

import aiofiles
import aiofiles.os
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.album import Album
from ..models.artist import Artist
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.transcoded_file import TranscodedFile
from ..models.upload import Upload
from ..models.user import User
from ..storage.s3 import S3Storage
from .federation import ensure_user_actor
from .musicbrainz import MusicBrainzService
from .storage import StorageService, audio_hash


async def merge_stored_file(
    session: AsyncSession,
    storage_service: StorageService,
    duplicate: StoredFile,
    survivor: StoredFile,
) -> None:
    """Point all known references to ``survivor`` and remove ``duplicate``."""
    duplicate_id = str(duplicate.id)
    survivor_id = str(survivor.id)

    await session.execute(update(Track).where(Track.audio_file_id == duplicate_id).values(audio_file_id=survivor_id))
    await session.execute(
        update(Upload).where(Upload.stored_file_id == duplicate_id).values(stored_file_id=survivor_id)
    )
    await session.execute(
        update(TranscodedFile).where(TranscodedFile.stored_file_id == duplicate_id).values(stored_file_id=survivor_id)
    )

    old_path = duplicate.storage_path
    await session.delete(duplicate)
    await session.flush()
    try:
        await storage_service.backend.delete(old_path)
    except Exception:
        pass


async def rehash_audio(
    session: AsyncSession,
    storage_service: StorageService,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Migrate existing audio ``StoredFile`` rows to audio-only SHA-256 hashes.

    :param session: An active async SQLAlchemy session.
    :param storage_service: Configured storage service for retrievals/uploads.
    :param dry_run: When ``True``, report the files that would migrate without
        making any changes.
    :returns: A dictionary with ``migrated``, ``merged``, ``skipped``, and
        ``failed`` counts.
    """
    migrated = 0
    merged = 0
    skipped = 0
    failed = 0

    result = await session.execute(select(StoredFile).where(StoredFile.content_type.ilike("audio/%")))
    rows = list(result.scalars().all())

    for stored_file in rows:
        local_path = await storage_service.backend.retrieve(stored_file.storage_path)
        if local_path is None:
            failed += 1
            continue

        try:
            new_hash = await audio_hash(local_path)
        except RuntimeError:
            failed += 1
            continue

        if new_hash == stored_file.sha256:
            skipped += 1
            continue

        if dry_run:
            migrated += 1
            continue

        existing = cast(
            Optional[StoredFile], await session.scalar(select(StoredFile).where(StoredFile.sha256 == new_hash))
        )

        if existing is not None:
            await merge_stored_file(session, storage_service, stored_file, existing)
            merged += 1
        else:
            prefix = stored_file.storage_path.split("/")[0] if "/" in stored_file.storage_path else "files"
            new_path = f"{prefix}/{new_hash[:2]}/{new_hash[2:4]}/{new_hash}"

            try:
                size = (await asyncio.to_thread(os.stat, local_path)).st_size
                with open(local_path, "rb") as f:
                    await storage_service.backend.store(f, new_path, content_type=stored_file.content_type)
            except Exception:
                failed += 1
                continue

            old_path = stored_file.storage_path
            stored_file.storage_path = new_path
            stored_file.sha256 = new_hash
            stored_file.size = size

            try:
                await storage_service.backend.delete(old_path)
            except Exception:
                pass

            if isinstance(storage_service.backend, S3Storage):
                try:
                    await aiofiles.os.remove(local_path)
                except Exception:
                    pass

            migrated += 1

    return {
        "migrated": migrated,
        "merged": merged,
        "skipped": skipped,
        "failed": failed,
    }


async def provision_federation_keys(
    session: AsyncSession,
    config: SonghiveConfig,
    *,
    dry_run: bool = False,
) -> int:
    """
    Back-fill ActivityPub actor URLs and keypairs for existing users.

    :param session: An active async SQLAlchemy session.
    :param config: The Songhive configuration.
    :param dry_run: When ``True``, count the users that would be provisioned
        without writing any changes.
    :returns: The number of users provisioned (or that would be provisioned).
    """
    if not config.federation.enabled or not config.federation.instance_domain:
        return 0

    batch_size = 50
    total = 0

    stmt = (
        select(User)
        .where(
            or_(
                User.actor_url.is_(None),
                User.private_key_pem.is_(None),
                User.public_key_pem.is_(None),
            )
        )
        .order_by(User.created_at)
    )
    result = await session.execute(stmt)
    users = result.scalars().all()

    for user in users:
        if dry_run:
            total += 1
            continue
        if ensure_user_actor(user, config):
            total += 1
            if total % batch_size == 0:
                await session.flush()

    return total


async def resolve_image_enrichment_targets(
    session: AsyncSession,
    *,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    all_: bool = False,
    force: bool = False,
) -> tuple[list[str], list[str]]:
    """
    Resolve artist and album IDs that should be processed for image enrichment.

    When ``all_`` is ``True``, all artists with a MusicBrainz ID and all albums
    with a MusicBrainz ID are returned, optionally limited to those that have
    not yet been enriched unless ``force`` is ``True``.

    Specific ``artist_id`` and ``album_id`` values are returned as-is when the
    entity exists and has a MusicBrainz ID.
    """
    artist_ids: list[str] = []
    album_ids: list[str] = []

    if artist_id:
        result = await session.execute(
            select(Artist.id).where(Artist.id == artist_id).where(Artist.musicbrainz_id.is_not(None))
        )
        row = result.scalar_one_or_none()
        if row is not None:
            artist_ids.append(str(row))
        return artist_ids, album_ids

    if album_id:
        result = await session.execute(
            select(Album.id).where(Album.id == album_id).where(Album.musicbrainz_id.is_not(None))
        )
        row = result.scalar_one_or_none()
        if row is not None:
            album_ids.append(str(row))
        return artist_ids, album_ids

    if all_:
        artist_stmt = select(Artist.id).where(Artist.musicbrainz_id.is_not(None))
        if not force:
            artist_stmt = artist_stmt.where(Artist.image_enriched_at.is_(None))
        result = await session.execute(artist_stmt)
        artist_ids = [str(row) for row in result.scalars().all()]

        album_stmt = select(Album.id).where(Album.musicbrainz_id.is_not(None))
        if not force:
            album_stmt = album_stmt.where(Album.cover_enriched_at.is_(None))
        result = await session.execute(album_stmt)
        album_ids = [str(row) for row in result.scalars().all()]

    return artist_ids, album_ids


async def bulk_enrich_images(
    session: AsyncSession,
    mb_service: MusicBrainzService,
    storage_service: StorageService,
    *,
    artist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    all_: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Enrich artist images and album covers for the requested scope.

    :param session: An active async SQLAlchemy session.
    :param mb_service: A configured MusicBrainz service.
    :param storage_service: A configured storage service.
    :param artist_id: Enrich a single artist by ID.
    :param album_id: Enrich a single album by ID.
    :param all_: Enrich all artists and albums that have not been processed yet.
    :param force: Re-process entities that have already been enriched.
    :param dry_run: When ``True``, report the counts without making changes.
    :returns: A dictionary with ``artists``, ``albums``, ``updated``, and
        ``failed`` counts.
    """
    artist_ids, album_ids = await resolve_image_enrichment_targets(
        session,
        artist_id=artist_id,
        album_id=album_id,
        all_=all_,
        force=force,
    )

    counts = {
        "artists": len(artist_ids),
        "albums": len(album_ids),
        "updated": 0,
        "failed": 0,
    }

    if dry_run:
        return counts

    for target_id in artist_ids:
        try:
            async with session.begin_nested():
                updated = await mb_service.enrich_artist_image_by_id(
                    session,
                    target_id,
                    storage_service,
                    force=force,
                )
                if updated:
                    counts["updated"] += 1
        except Exception:
            logger = logging.getLogger(__name__)
            logger.exception("Image enrichment failed for artist %s", target_id)
            counts["failed"] += 1

    for target_id in album_ids:
        try:
            async with session.begin_nested():
                updated = await mb_service.enrich_album_cover_by_id(
                    session,
                    target_id,
                    storage_service,
                    force=force,
                )
                if updated:
                    counts["updated"] += 1
        except Exception:
            logger = logging.getLogger(__name__)
            logger.exception("Cover enrichment failed for album %s", target_id)
            counts["failed"] += 1

    return counts
