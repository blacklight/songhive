"""
Sample derivatives: artist-chosen audio excerpts for ``sample``-policy sales.

A :class:`SampleDerivative` is a private ``StoredFile`` produced by an ffmpeg
cut (``-ss start -t length``) from the track's current audio. Derivatives are
keyed on ``(track_id, source_sha256, start, length)`` so replacing the audio
or editing the window invalidates the old sample automatically — a stale
sample is never served against new bytes.

Materialization runs in the ``materialize_track_sample`` Celery task behind a
per-track Redis lock (mirroring ``sync_track_tags``); the API enqueues it and
the samples endpoint 404s until the derivative is ``ready``.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models._enums import Visibility
from ...models.payments import Sale, SampleDerivative
from ...models.stored_file import StoredFile
from ...models.track import Track
from ...streaming.transcoder import Transcoder, TranscoderError
from ..streaming import resolve_track_file
from .errors import PaymentError

logger = logging.getLogger(__name__)

SAMPLE_FORMAT = "mp3"
SAMPLE_BITRATE = "128k"
_LOCK_KEY = "songhive:payments:sample-lock:{track_id}"


def sample_window(sale: Sale, track: Track, config: SonghiveConfig) -> tuple[int, int]:
    """
    Return the effective ``(start_seconds, length_seconds)`` for a sale sample.

    The configured window is clamped to the track duration — a sale whose
    start lands beyond the audio yields a sample of whatever tail exists.
    """
    start = max(0, int(sale.sample_start_seconds or 0))
    length = sale.sample_length_seconds or config.payments.default_sample_seconds
    length = min(int(length), config.payments.sample_max_seconds)
    duration = int(track.duration or 0)
    if duration > 0 and start >= duration:
        start = max(0, duration - length)
    if duration > 0:
        length = max(1, min(length, duration - start))
    return start, length


async def get_ready_sample(
    session: AsyncSession, track: Track, sale: Sale, config: SonghiveConfig
) -> Optional[SampleDerivative]:
    """Return the ready derivative for the track's current source + window."""
    stored_file = await resolve_track_file(session, str(track.id))
    if stored_file is None:
        return None
    start, length = sample_window(sale, track, config)
    result = await session.execute(
        select(SampleDerivative).where(
            SampleDerivative.track_id == track.id,
            SampleDerivative.source_sha256 == stored_file.sha256,
            SampleDerivative.start_seconds == start,
            SampleDerivative.length_seconds == length,
            SampleDerivative.status == "ready",
        )
    )
    return result.scalar_one_or_none()


async def ensure_sample(
    session: AsyncSession,
    track: Track,
    sale: Sale,
    config: SonghiveConfig,
    *,
    enqueue: bool = True,
) -> SampleDerivative:
    """
    Return the matching derivative row, creating a ``pending`` one if needed.

    With ``enqueue`` a ``materialize_track_sample`` task is queued; callers
    without a broker should pass ``enqueue=False`` and drive the build
    themselves.
    """
    stored_file = await resolve_track_file(session, str(track.id))
    if stored_file is None:
        raise PaymentError("Track has no audio file to sample", status_code=422)

    start, length = sample_window(sale, track, config)
    result = await session.execute(
        select(SampleDerivative).where(
            SampleDerivative.track_id == track.id,
            SampleDerivative.source_sha256 == stored_file.sha256,
            SampleDerivative.start_seconds == start,
            SampleDerivative.length_seconds == length,
        )
    )
    derivative = result.scalar_one_or_none()
    if derivative is not None:
        return derivative

    derivative = SampleDerivative(
        track_id=track.id,
        source_sha256=stored_file.sha256,
        start_seconds=start,
        length_seconds=length,
    )
    session.add(derivative)
    await session.flush()
    if enqueue:
        enqueue_sample_materialization(str(track.id))
    return derivative


def enqueue_sample_materialization(track_id: str) -> bool:
    """Queue ``materialize_track_sample`` for a track; False if broker down."""
    from ...tasks.payments import materialize_track_sample

    try:
        materialize_track_sample.delay(track_id)  # type: ignore[attr-defined]
        return True
    except Exception as exc:  # broker errors must not fail the request
        logger.warning("Could not enqueue sample build for track %s: %s", track_id, exc)
        return False


async def mark_stale_samples(session: AsyncSession, track: Track, *, current_sha256: Optional[str] = None) -> int:
    """
    Mark a track's derivatives whose source hash no longer matches as failed.

    Called when a track's audio file is replaced; ready-but-stale samples are
    never served (``get_ready_sample`` filters on the current sha), and the
    failed state lets cleanup reclaim their storage.
    """
    if current_sha256 is None:
        stored_file = await resolve_track_file(session, str(track.id))
        current_sha256 = stored_file.sha256 if stored_file else ""
    result = await session.execute(
        select(SampleDerivative).where(
            SampleDerivative.track_id == track.id,
            SampleDerivative.status.in_(("pending", "ready")),
            SampleDerivative.source_sha256 != current_sha256,
        )
    )
    stale = list(result.scalars().all())
    for derivative in stale:
        derivative.status = "failed"
    return len(stale)


async def _materialize_derivative(
    session: AsyncSession,
    derivative: SampleDerivative,
    storage,
    config: SonghiveConfig,
) -> bool:
    """Render the sample with ffmpeg and store it; flip the row to ready."""
    track = await session.get(Track, derivative.track_id)
    if track is None:
        derivative.status = "failed"
        return False
    stored_file = await resolve_track_file(session, str(track.id))
    if stored_file is None or stored_file.sha256 != derivative.source_sha256:
        # Source gone or already replaced — a new derivative row handles it.
        derivative.status = "failed"
        return False

    transcoder = Transcoder(config.streaming.ffmpeg_path)
    fmt = Transcoder.FORMAT_MAP[SAMPLE_FORMAT]

    try:
        source_path = await storage.backend.retrieve(stored_file.storage_path)
    except ValueError:
        source_path = None
    if source_path is None:
        derivative.status = "failed"
        return False

    # Non-local backends (S3) hand us a temp download; only those may be
    # removed afterwards.
    from ...storage.local import LocalStorage

    source_temp = not isinstance(storage.backend, LocalStorage)

    output_dir = Path(tempfile.mkdtemp(prefix="songhive-sample-"))
    output_path = output_dir / f"sample.{fmt['ext']}"
    try:
        cmd = [
            transcoder.ffmpeg_path,
            "-ss",
            str(derivative.start_seconds),
            "-t",
            str(derivative.length_seconds),
            "-i",
            str(source_path),
            "-c:a",
            fmt["codec"],
            "-b:a",
            SAMPLE_BITRATE,
            "-y",
            str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not output_path.exists():
            raise TranscoderError(f"ffmpeg exited with code {proc.returncode}: {stderr.decode().strip()}")

        with open(output_path, "rb") as handle:
            stored = await storage.store_file(
                session,
                handle,
                fmt["mimetype"],
                prefix="samples",
                owner_id=track.owner_id,
                visibility=Visibility.PRIVATE.value,
                original_filename=f"sample-{track.title}.{fmt['ext']}",
            )
        derivative.stored_file_id = stored.id
        derivative.status = "ready"
        return True
    except (OSError, TranscoderError) as exc:
        logger.warning("Sample materialization failed for track %s: %s", track.id, exc)
        derivative.status = "failed"
        return False
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
        if source_temp:
            try:
                source_path.unlink()
            except OSError:
                pass


async def materialize_track_samples(
    session: AsyncSession,
    track_id: str,
    storage,
    config: SonghiveConfig,
) -> int:
    """
    Render all ``pending`` derivatives for a track.

    Returns the number flipped to ``ready``. When every pending derivative of
    every active ``sample``-policy sale covering the track is done, the sale
    is activated (published).
    """
    result = await session.execute(
        select(SampleDerivative).where(
            SampleDerivative.track_id == track_id,
            SampleDerivative.status == "pending",
        )
    )
    pending = list(result.scalars().all())
    built = 0
    for derivative in pending:
        if await _materialize_derivative(session, derivative, storage, config):
            built += 1
    if built:
        await session.flush()
        await maybe_activate_sales(session, track_id, config)
    return built


async def maybe_activate_sales(session: AsyncSession, track_id: str, config: SonghiveConfig) -> None:
    """
    Flip draft ``sample``-policy sales to ``active`` once their derivatives exist.

    A track sale activates when its own derivative is ready; an album sale
    activates when every member track's derivative is ready.
    """
    track = await session.get(Track, track_id)
    if track is None:
        return
    candidates = (
        (
            await session.execute(
                select(Sale).where(
                    Sale.status == "draft",
                    Sale.unpaid_policy == "sample",
                    Sale.sample_render_policy == "materialized",
                )
            )
        )
        .scalars()
        .all()
    )
    for sale in candidates:
        covers = sale.track_id == track.id or (
            sale.entity_type == "album" and track.album_id is not None and sale.album_id == track.album_id
        )
        if not covers:
            continue
        await _activate_if_ready(session, sale, config)


async def _activate_if_ready(session: AsyncSession, sale: Sale, config: SonghiveConfig) -> bool:
    """Activate ``sale`` when all covered tracks have a ready sample."""
    if sale.entity_type == "track":
        tracks = [await session.get(Track, sale.track_id)]
    else:
        result = await session.execute(select(Track).where(Track.album_id == sale.album_id))
        tracks = list(result.scalars().all())
    for track in tracks:
        if track is None:
            return False
        if await get_ready_sample(session, track, sale, config) is None:
            return False
    sale.status = "active"

    from ...models.notification import NotificationType
    from ..notifications import create_notification

    await create_notification(
        session,
        user_id=sale.owner_id,
        type=NotificationType.PURCHASE,
        source_url="/settings?tab=billing",
        payload={"event": "sale_activated", "sale_id": str(sale.id)},
    )
    return True


async def sample_stored_file(
    session: AsyncSession,
    track: Track,
    sale: Sale,
    config: SonghiveConfig,
    *,
    storage=None,
    redis=None,
    lock_ttl: int = 60,
) -> Optional[StoredFile]:
    """
    Return the ready derivative's ``StoredFile``, or ``None`` when unavailable.

    For ``on_demand`` sales this renders the derivative inline under the
    per-track lock when ``storage`` is supplied — the encode is bounded by
    ``sample_max_seconds``. Otherwise a missing derivative is (re)enqueued
    and ``None`` is returned so the caller can answer "not ready".
    """
    derivative = await get_ready_sample(session, track, sale, config)
    if derivative is not None and derivative.stored_file_id is not None:
        return await session.get(StoredFile, derivative.stored_file_id)

    if storage is not None and sale.sample_render_policy == "on_demand":
        locked = redis is not None and await acquire_sample_lock(redis, str(track.id), lock_ttl)
        try:
            if locked or redis is None:
                try:
                    await ensure_sample(session, track, sale, config, enqueue=False)
                    await materialize_track_samples(session, str(track.id), storage, config)
                except PaymentError:
                    return None
        finally:
            if locked:
                await release_sample_lock(redis, str(track.id))
        derivative = await get_ready_sample(session, track, sale, config)
        if derivative is not None and derivative.stored_file_id is not None:
            return await session.get(StoredFile, derivative.stored_file_id)
        return None

    enqueue_sample_materialization(str(track.id))
    return None


async def acquire_sample_lock(redis_client, track_id: str, ttl: int) -> bool:
    """Take the per-track materialization lock."""
    return bool(await redis_client.set(_LOCK_KEY.format(track_id=track_id), "1", nx=True, ex=ttl))


async def release_sample_lock(redis_client, track_id: str) -> None:
    """Release the per-track materialization lock."""
    try:
        await redis_client.delete(_LOCK_KEY.format(track_id=track_id))
    except Exception:  # noqa: BLE001 — lock expiry covers failure
        pass
