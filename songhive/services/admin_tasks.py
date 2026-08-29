"""
Shared admin task helpers used by the CLI, API, and Celery workers.
"""

import asyncio
import os
from typing import Optional, cast

import aiofiles
import aiofiles.os
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import SonghiveConfig
from ..models.stored_file import StoredFile
from ..models.track import Track
from ..models.transcoded_file import TranscodedFile
from ..models.upload import Upload
from ..models.user import User
from ..storage.s3 import S3Storage
from .federation import ensure_user_actor
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
