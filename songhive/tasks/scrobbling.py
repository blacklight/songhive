"""
Scrobbling tasks: submit plays to Audioscrobbler-compatible services.

``scrobble_now_playing`` fires on every stream start (Tornado handler,
Subsonic ``scrobble.view?submission=false``); ``scrobble_track`` fires from
``services.streaming.record_listen`` once a play crosses the user's
thresholds. Both deduplicate through Redis: the same play can reach
``record_listen`` twice (the web player's history report and the stream
handler's byte threshold land within seconds of each other), and a stream
start can fire repeatedly (range requests, seeks).
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import load_config
from ..config.schema import SonghiveConfig
from ..models.album import Album
from ..models.base import dispose_and_reset, get_session, init_db
from ..models.scrobble import ScrobbleConfig
from ..models.track import Track
from ..models.user import User
from ..services.scrobbler import ScrobblerError, ScrobblerTemporaryError
from .celery import celery_app

logger = logging.getLogger(__name__)

#: Suppress repeat now-playing submissions for the same user+track.
NOW_PLAYING_DEDUP_SECONDS = 45

#: Floor for the scrobble dedup window — the double ``record_listen`` calls
#: for one play land within a couple of seconds of each other.
_SCROBBLE_DEDUP_FLOOR_SECONDS = 5


def _dedup_key(*parts: str) -> str:
    return "songhive:scrobble:" + ":".join(parts)


def _recently_submitted(config: SonghiveConfig, key: str) -> bool:
    """Return True when ``key`` was marked recently. Fails open on Redis errors."""
    from ..services.redis import get_sync_redis_client

    try:
        return bool(get_sync_redis_client(config).get(key))
    except Exception:
        logger.debug("Scrobble dedup check failed for %s", key, exc_info=True)
        return False


def _mark_submitted(config: SonghiveConfig, key: str, ttl_seconds: int) -> None:
    from ..services.redis import get_sync_redis_client

    try:
        get_sync_redis_client(config).setex(key, ttl_seconds, "1")
    except Exception:
        logger.debug("Scrobble dedup mark failed for %s", key, exc_info=True)


def _scrobble_dedup_ttl(row: ScrobbleConfig, duration: Optional[float]) -> int:
    """
    Return the dedup window for ``track.scrobble`` submissions.

    Duplicate ``record_listen`` calls for the same play arrive within seconds;
    a genuine replay cannot re-cross the threshold faster than the threshold
    itself, so the window stays safely below it.
    """
    threshold = float(row.min_seconds)
    if duration:
        threshold = min(threshold, duration * row.min_percent / 100.0)
    return max(_SCROBBLE_DEDUP_FLOOR_SECONDS, int(threshold) - 5)


async def _load_track(session: AsyncSession, track_id: str) -> Optional[Track]:
    """Load a track with the relationships needed to build scrobble fields."""
    result = await session.execute(
        select(Track)
        .where(Track.id == track_id)
        .options(
            selectinload(Track.artist),
            selectinload(Track.album).selectinload(Album.artist),
        )
    )
    return result.scalar_one_or_none()


async def _load_active_config(session: AsyncSession, config: SonghiveConfig, user: User) -> Optional[ScrobbleConfig]:
    """Return the user's scrobble config when it can actually submit."""
    from ..services import scrobbler

    row = await scrobbler.get_config_row(session, user)
    if row is None or not row.enabled or not row.session_key:
        return None
    if not scrobbler.service_available(config, row.service):
        return None
    return row


@celery_app.task(
    bind=True,
    name="songhive.tasks.scrobbling.now_playing",
    max_retries=3,
    default_retry_delay=10,
    autoretry_for=(ScrobblerTemporaryError,),
)
def scrobble_now_playing(self, user_id: str, track_id: str) -> Optional[str]:
    """Submit ``track.updateNowPlaying`` for a play that just started."""
    from ..services import scrobbler

    config = load_config([])
    if not config.scrobbling.enabled:
        return None
    init_db(config.database.url)

    async def _run() -> str:
        try:
            async with get_session() as session:
                user = await session.get(User, user_id)
                if user is None:
                    return "missing-user"
                track = await _load_track(session, track_id)
                if track is None:
                    return "missing-track"
                row = await _load_active_config(session, config, user)
                if row is None:
                    return "disabled"

                dedup_key = _dedup_key("np", user_id, track_id)
                if _recently_submitted(config, dedup_key):
                    return "duplicate"

                try:
                    await scrobbler.submit_now_playing(config, row, track)
                except ScrobblerError:
                    # Persist last_error before propagating/returning.
                    await session.commit()
                    raise
                await session.commit()
                _mark_submitted(config, dedup_key, NOW_PLAYING_DEDUP_SECONDS)
                return "done"
        finally:
            await dispose_and_reset()

    try:
        result = asyncio.run(_run())
    except ScrobblerError as exc:
        logger.info("Now-playing scrobble failed for user %s track %s: %s", user_id, track_id, exc)
        raise
    if result in ("missing-user", "missing-track"):
        if self.request.retries < self.max_retries:
            logger.info("Scrobble rows for user %s track %s not committed yet; retrying", user_id, track_id)
            raise self.retry(countdown=5 * 2**self.request.retries)
        logger.warning("Dropping now-playing scrobble for unknown user %s track %s", user_id, track_id)
        return None
    return result


@celery_app.task(
    bind=True,
    name="songhive.tasks.scrobbling.scrobble",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(ScrobblerTemporaryError,),
)
def scrobble_track(self, user_id: str, track_id: str, played_at: int) -> Optional[str]:
    """Submit ``track.scrobble`` for a play that crossed the listen threshold."""
    from ..services import scrobbler

    config = load_config([])
    if not config.scrobbling.enabled:
        return None
    init_db(config.database.url)

    async def _run() -> str:
        try:
            async with get_session() as session:
                user = await session.get(User, user_id)
                if user is None:
                    return "missing-user"
                track = await _load_track(session, track_id)
                if track is None:
                    return "missing-track"
                row = await _load_active_config(session, config, user)
                if row is None:
                    return "disabled"

                dedup_key = _dedup_key("sub", user_id, track_id)
                if _recently_submitted(config, dedup_key):
                    return "duplicate"

                try:
                    await scrobbler.submit_scrobble(config, row, track, played_at)
                except ScrobblerError:
                    await session.commit()
                    raise
                await session.commit()
                _mark_submitted(config, dedup_key, _scrobble_dedup_ttl(row, track.duration))
                return "done"
        finally:
            await dispose_and_reset()

    try:
        result = asyncio.run(_run())
    except ScrobblerError as exc:
        logger.info("Scrobble failed for user %s track %s: %s", user_id, track_id, exc)
        raise
    if result in ("missing-user", "missing-track"):
        if self.request.retries < self.max_retries:
            logger.info("Scrobble rows for user %s track %s not committed yet; retrying", user_id, track_id)
            raise self.retry(countdown=5 * 2**self.request.retries)
        logger.warning("Dropping scrobble for unknown user %s track %s", user_id, track_id)
        return None
    return result
