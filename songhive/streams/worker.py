"""
Stream worker: owns long-lived server-side output drivers.

A single asyncio loop discovers playback sessions with a ``stream`` output,
claims one Redis lock per output, starts the provider driver, consumes control
commands from ``songhive:playback:control:{session_id}``, and publishes
playback state back to the user's tabs.
"""

import asyncio
import json
import logging
import os
import random
import secrets
import signal
import time
from contextlib import suppress
from pathlib import Path
from typing import Optional, cast

import aiofiles
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import database_engine_kwargs, load_config
from ..config.schema import SonghiveConfig
from ..models.base import get_session, init_db
from ..models.output_stream import OutputStream
from ..models.playback_session import PlaybackSession, PlaybackSessionOutput
from ..models.track import Track
from ..services.outputs import driving_stream_session
from ..services.playback import (
    _clamp_position,
    _control_key,
    _current_track,
    _live_position_seconds,
    _now_utc,
    _track_duration,
    publish_playback_event,
    record_server_listen,
    session_state_dict,
)
from ..services.redis import create_redis_client
from ..services.secrets import decrypt_json
from ..services.streaming import resolve_external_stream, resolve_track_file
from ..storage import get_storage
from ..streams.base import listen_recording_enabled
from ..streams.driver import OutputDriver
from ..streams.live import (
    LIVE_KEY_PATTERN,
    WORKER_HEARTBEAT_TTL_SECONDS,
    LiveState,
    demuxer_for_mime,
    find_ingest_start,
    ingest_iterator,
    read_live_state,
    worker_heartbeat_key,
)
from ..streams.registry import get_output
from ..streams.types import AudioSource, TrackMeta

logger = logging.getLogger(__name__)

CONTROL_KEY = "songhive:playback:control:{session_id}"
LOCK_KEY = "songhive:stream:lock:{output_id}"


async def run_stream_worker() -> None:
    """Entry point for the ``songhive stream-worker`` command."""
    config = load_config([])
    init_db(config.database.url, **database_engine_kwargs(config.database))
    worker = StreamWorker(config)
    await worker.run()


class StreamWorker:
    """Discovers active stream sessions and spawns a ``SessionDriver`` for each."""

    def __init__(self, config: SonghiveConfig) -> None:
        self.config = config
        self.redis = create_redis_client(config)
        self._shutting_down = False
        self._tasks: dict[str, asyncio.Task] = {}
        # A short-TTL heartbeat key lets the live-ingest endpoint detect a
        # missing worker and fail fast instead of timing the broadcast out.
        self.worker_id = secrets.token_hex(8)

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    async def run(self) -> None:
        """Run the discovery loop until signalled to stop."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)

        while not self._shutting_down:
            await self._heartbeat()
            await self._scan_sessions()
            await self._scan_live()
            await self._reap_finished()
            await asyncio.sleep(self.config.streams.worker_poll_interval_seconds)

        # Graceful shutdown: stop all drivers and wait for tasks.
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        logger.info("Stream worker stopped")

    def _request_shutdown(self) -> None:
        self._shutting_down = True

    async def _scan_sessions(self) -> None:
        """Load active stream sessions and start drivers for unclaimed outputs."""
        try:
            async with get_session() as db:
                stmt = (
                    select(PlaybackSession)
                    .join(PlaybackSessionOutput)
                    .join(OutputStream)
                    .where(
                        PlaybackSessionOutput.output_kind == "stream",
                        PlaybackSessionOutput.output_stream_id == OutputStream.id,
                        OutputStream.enabled.is_(True),
                    )
                    .options(selectinload(PlaybackSession.outputs))
                )
                result = await db.execute(stmt)
                sessions = list(result.scalars().unique().all())
        except Exception:
            logger.exception("Failed to scan stream sessions")
            return

        for session in sessions:
            stream_outputs = [o for o in (session.outputs or []) if o.output_kind == "stream"]
            if not stream_outputs:
                continue

            output_stream_id = stream_outputs[0].output_stream_id
            if not output_stream_id:
                continue

            key = str(output_stream_id)
            if key in self._tasks:
                continue

            driver = SessionDriver(self, session.id, output_stream_id, session.user_id)
            lock = LOCK_KEY.format(output_id=output_stream_id)
            try:
                acquired = await self.redis.set(
                    lock,
                    driver._lock_token,
                    nx=True,
                    ex=self.config.streams.worker_lock_ttl_seconds,
                )
            except Exception:
                logger.exception("Redis lock check failed for %s", output_stream_id)
                continue

            if not acquired:
                continue

            logger.info(
                "Claimed stream output %s for session %s",
                output_stream_id,
                session.id,
            )
            self._tasks[key] = asyncio.create_task(driver.run())

    async def _heartbeat(self) -> None:
        """Refresh this worker's heartbeat key so ingest can detect us."""
        try:
            await self.redis.set(
                worker_heartbeat_key(self.worker_id),
                "1",
                ex=WORKER_HEARTBEAT_TTL_SECONDS,
            )
        except Exception:
            logger.exception("Failed to refresh worker heartbeat")

    async def _scan_live(self) -> None:
        """
        Claim outputs with a pending live broadcast and no driver yet.

        Outputs already driven by a ``SessionDriver`` are left to it — the
        driver polls the live key itself and takes over the mount.
        """
        if not self.config.streams.enabled or not self.config.streams.live_enabled:
            return
        try:
            output_ids = [
                str(key).rsplit(":", 1)[-1] async for key in self.redis.scan_iter(match=LIVE_KEY_PATTERN, count=100)
            ]
        except Exception:
            logger.exception("Failed to scan live keys")
            return

        for output_id in output_ids:
            if not output_id or output_id in self._tasks:
                continue
            try:
                state = await read_live_state(self.redis, output_id)
            except Exception:
                logger.exception("Failed to read live state for %s", output_id)
                continue
            if state is None:
                continue
            try:
                async with get_session() as db:
                    output = await db.get(OutputStream, output_id)
            except Exception:
                logger.exception("Failed to load live output %s", output_id)
                continue
            if output is None or output.provider_type != "http" or not output.enabled:
                continue

            driver = SessionDriver(self, None, output_id, output.user_id)
            lock = LOCK_KEY.format(output_id=output_id)
            try:
                acquired = await self.redis.set(
                    lock,
                    driver._lock_token,
                    nx=True,
                    ex=self.config.streams.worker_lock_ttl_seconds,
                )
            except Exception:
                logger.exception("Redis lock check failed for %s", output_id)
                continue
            if not acquired:
                continue

            logger.info("Claimed stream output %s for live broadcast", output_id)
            self._tasks[output_id] = asyncio.create_task(driver.run())

    async def _reap_finished(self) -> None:
        """Remove completed tasks from the active map and refresh their locks."""
        for output_id in list(self._tasks):
            task = self._tasks[output_id]
            if task.done():
                with suppress(Exception):
                    await task
                del self._tasks[output_id]


class SessionDriver:
    """
    Drives one server output for a single playback session.

    With ``session_id=None`` the driver runs in live-only mode: it owns the
    output's mount purely to serve a live broadcast, skips every
    session-bound mechanism (control commands, output status rows, idle
    shutdown), adopts a session that attaches mid-broadcast, and stops when
    the broadcast ends without one.
    """

    # Control-command batching: after the first envelope is drained, keep
    # polling until no new envelope arrives for _CONTROL_SETTLE_S (capped at
    # _CONTROL_BURST_MAX_S total) so multi-command UI actions sync the driver
    # once instead of restarting the decoder per envelope.
    _CONTROL_POLL_S = 0.05
    _CONTROL_SETTLE_S = 0.3
    _CONTROL_BURST_MAX_S = 1.5

    # A failed track advance (transient DB/storage/provider error) is retried
    # a few times; while retries are pending the old decoder is dead and the
    # encoder starves, so on giving up the driver is paused — the silence
    # generator keeps the mount alive instead of leaving listeners on dead
    # air until the liveness key expires.
    _ADVANCE_RETRY_DELAY_S = 1.0
    _ADVANCE_MAX_RETRIES = 5

    def __init__(
        self,
        worker: StreamWorker,
        session_id: Optional[str],
        output_stream_id: str,
        user_id: str,
    ) -> None:
        self.worker = worker
        self.session_id = session_id
        self.output_stream_id = output_stream_id
        self.user_id = user_id
        self._lock_token = secrets.token_urlsafe(24)
        self.driver: Optional[OutputDriver] = None
        self._shutting_down = False
        self._idle_since: Optional[float] = None
        self._active_track_id: Optional[str] = None
        self._record_listens = True
        self._advance_retry_at: Optional[float] = None
        self._advance_retry_event: Optional[dict] = None
        self._advance_retry_count = 0
        # Live broadcast state: the ingest id currently driving the output,
        # its decoder generation (to recognize its source_ended), and whether
        # the takeover paused a playing session that should resume after.
        self._live_ingest_id: Optional[str] = None
        self._live_generation: Optional[int] = None
        self._live_started = False
        self._resume_after_live = False
        self._live_poll_at = 0.0
        self._adopt_poll_at = 0.0
        self._output_name = ""

    @property
    def _control_key(self) -> str:
        return _control_key(self.session_id or "")

    @property
    def _lock_key(self) -> str:
        return LOCK_KEY.format(output_id=self.output_stream_id)

    async def run(self) -> None:
        """Start the driver and run the command/event loop."""
        try:
            await self._start_driver()
        except Exception:
            logger.exception("Failed to start driver for %s", self.output_stream_id)
            await self._release_lock()
            return

        if not await self._acquire_or_refresh_lock():
            logger.error(
                "Could not acquire stream lock for %s; stopping driver",
                self.output_stream_id,
            )
            await self._stop_driver()
            await self._release_lock()
            return

        try:
            if self.session_id is not None:
                await self._sync_to_session()
            await self._main_loop()
        except asyncio.CancelledError:
            logger.info("Session driver cancelled for %s", self.session_id)
        except Exception:
            logger.exception("Session driver error for %s", self.session_id)
        finally:
            await self._stop_driver()
            await self._release_lock()

    async def _start_driver(self) -> None:
        """Load the output configuration and start the provider driver."""
        async with get_session() as db:
            output_stream = await db.get(OutputStream, self.output_stream_id)
            if output_stream is None:
                raise RuntimeError("Output stream not found")
            self._output_name = output_stream.name or ""

            provider_cls = get_output(output_stream.provider_type)
            provider = provider_cls()
            config = decrypt_json(output_stream.config)
            await provider.validate_config(config)

            # A stream opted out of listen recording (e.g. a 24/7 radio mount)
            # still advances and broadcasts metadata; its plays just never
            # reach listening history, stats or scrobbles.
            self._record_listens = listen_recording_enabled(config)

            # Allow provider configs to fall back to the configured ffmpeg path.
            if output_stream.provider_type in ("icecast", "http", "snapcast") and not config.get("ffmpeg_path"):
                config["ffmpeg_path"] = (
                    self.worker.config.streams.icecast_ffmpeg_path or self.worker.config.streaming.ffmpeg_path
                )

            # Native HTTP mounts are served from the web process; the driver
            # publishes encoded audio to a Redis stream, so it needs the
            # worker's Redis client and the configured backlog size. The
            # owner id lets the driver target ``stream_update`` WebSocket
            # events at the owner of listen-token (private) mounts.
            if output_stream.provider_type == "http":
                config["_redis"] = self.worker.redis
                config["_stream_max_entries"] = self.worker.config.streams.http_stream_max_entries
                config["_owner_user_id"] = output_stream.user_id

            self.driver = provider.create_driver(config)
            await self.driver.start()
            await self._update_output_status(status="live")

    async def _stop_driver(self) -> None:
        """Stop the provider driver, if it was started."""
        self._clear_advance_retry()
        if self.driver is not None:
            try:
                await self.driver.stop()
            except Exception:
                logger.exception("Error stopping driver for %s", self.output_stream_id)
            self.driver = None

    async def _release_lock(self) -> None:
        """Release the Redis output lock if we still own it."""
        try:
            value = await self.worker.redis.get(self._lock_key)
            if value == self._lock_token:
                await self.worker.redis.delete(self._lock_key)
        except Exception:
            logger.exception("Failed to release lock for %s", self.output_stream_id)

    async def _acquire_or_refresh_lock(self) -> bool:
        """Set, claim, or refresh the Redis output lock for this driver."""
        try:
            value = await self.worker.redis.get(self._lock_key)
            if value == self._lock_token:
                await self.worker.redis.expire(
                    self._lock_key,
                    self.worker.config.streams.worker_lock_ttl_seconds,
                )
                return True
            if value is None:
                ok = await self.worker.redis.set(
                    self._lock_key,
                    self._lock_token,
                    nx=True,
                    ex=self.worker.config.streams.worker_lock_ttl_seconds,
                )
                return bool(ok)
            return False
        except Exception:
            logger.exception("Failed to refresh lock for %s", self.output_stream_id)
            return False

    async def _main_loop(self) -> None:
        """Consume commands and driver events until shutdown or idle timeout."""
        while not self.worker.shutting_down and not self._shutting_down:
            if not await self._acquire_or_refresh_lock():
                logger.error(
                    "Lost stream lock for %s; stopping driver",
                    self.output_stream_id,
                )
                break

            # Drain any pending control commands. A single user action can
            # land as a burst of envelopes (e.g. set_queue + play_at + play
            # are published as separate commands), so they are collected with
            # a short settle window and synced to the driver once — syncing
            # per envelope would restart the decoder for each command and
            # listeners would hear the track start over again.
            if self.session_id is not None:
                envelopes = await self._collect_control_envelopes()
                if envelopes:
                    await self._handle_commands(envelopes)

            # Drain any driver events, then retry a failed track advance
            # whose backoff has elapsed.
            await self._drain_events()
            await self._maybe_retry_advance()

            # Live broadcasts take over the mount: the driver polls the
            # output's live key and swaps the ingest source in and out.
            await self._poll_live()

            if self.session_id is not None:
                # Stop the worker if the session/output has been removed or disabled.
                if not await self._session_has_stream_output():
                    logger.info("Session %s no longer has a stream output", self.session_id)
                    break

                # Background idle shutdown: nothing playing and no controller/listeners.
                if await self._should_stop_on_idle():
                    logger.info("Idle timeout for session %s; stopping driver", self.session_id)
                    break
            elif not await self._live_only_tick():
                break

            await asyncio.sleep(0.1)

    async def _sync_to_session(self) -> None:
        """Synchronise the driver to the current session state on startup."""
        if self.driver is None or self.session_id is None:
            return
        try:
            async with get_session() as db:
                session = await self._load_session(db)
                if session is None:
                    return
                # Push the persisted gain first so the first decoder argv is
                # built with the right volume instead of restarting right away.
                await self.driver.set_volume(session.volume)
                if session.state == "playing":
                    source, metadata = await self._resolve_source(db, session)
                    if source is not None:
                        metadata = metadata or TrackMeta(track_id="", title="", artist="")
                        await self.driver.set_source(
                            source,
                            position=session.position_seconds,
                            metadata=metadata,
                        )
                        await self.driver.update_metadata(metadata)
                        self._active_track_id = _current_track_id(session)
                        session.position_anchor_at = _now_utc()
                        session.last_active_at = _now_utc()
                elif session.state == "paused":
                    await self.driver.pause()
                    # Keep the now-playing title visible on mounts joined
                    # while the session was already paused.
                    metadata = await self._resolve_metadata(db, session)
                    if metadata is not None:
                        await self.driver.update_metadata(metadata)
        except Exception:
            logger.exception("Failed to sync session %s on startup", self.session_id)

    async def _poll_live(self) -> None:
        """Watch the output's live key and take the mount over (or back)."""
        now = time.monotonic()
        if now - self._live_poll_at < 0.5:
            return
        self._live_poll_at = now
        if self.driver is None:
            return
        try:
            state = await read_live_state(self.worker.redis, self.output_stream_id)
        except Exception:
            # A transient Redis error must not bounce an ongoing broadcast.
            logger.exception("Failed to poll live state for %s", self.output_stream_id)
            return

        if self._live_ingest_id is None:
            if state is not None:
                await self._begin_live(state)
        elif state is None:
            await self._end_live()
        elif state.ingest_id != self._live_ingest_id:
            # A reconnecting broadcaster claimed a new ingest id: swap
            # straight to it without resuming the queue in between.
            await self._end_live(superseded=True)
            await self._begin_live(state)

    async def _begin_live(self, state: LiveState) -> None:
        """Take the mount over with a live ingest feed."""
        if self.driver is None:
            return
        self._resume_after_live = False

        # A playing session is paused at its live position so the queue
        # resumes where it stopped once the broadcast ends (radio-DJ model).
        if self.session_id is not None:
            try:
                async with get_session() as db:
                    session = await self._load_session(db)
                    if session is not None and session.state == "playing":
                        session.position_seconds = _live_position_seconds(session)
                        session.state = "paused"
                        session.position_anchor_at = None
                        session.last_active_at = _now_utc()
                        await db.flush()
                        self._resume_after_live = True
                        await self._publish_state(await session_state_dict(db, session))
            except Exception:
                logger.exception("Failed to freeze session %s for live", self.session_id)

        artist = ""
        try:
            from ..models.user import User as _User

            async with get_session() as db:
                owner = await db.get(_User, state.user_id)
            if owner is not None:
                artist = owner.display_name or owner.username or ""
        except Exception:
            logger.exception("Failed to load live owner %s", state.user_id)

        metadata = TrackMeta(
            track_id="",
            title=state.title or self._output_name or "Live",
            artist=artist,
        )
        try:
            start_id = await find_ingest_start(self.worker.redis, self.output_stream_id, state.ingest_id)
            source = AudioSource(
                kind="live",
                iterator=ingest_iterator(
                    self.worker.redis,
                    self.output_stream_id,
                    state.ingest_id,
                    start_id=start_id,
                ),
                input_format=demuxer_for_mime(state.mime),
                content_type=state.mime or None,
            )
            await self.driver.set_source(source, position=0.0, metadata=metadata)
            await self.driver.update_metadata(metadata)
            await self.driver.set_live_state(state.ingest_id)
            self._live_ingest_id = state.ingest_id
            self._live_generation = self.driver.generation
            self._live_started = True
            # The queue's active track is not what the mount plays anymore;
            # clearing it makes the post-live resync always set a source.
            self._active_track_id = None
            logger.info("Output %s is live (ingest %s)", self.output_stream_id, state.ingest_id)
        except Exception:
            logger.exception("Failed to start live source for %s", self.output_stream_id)

    async def _end_live(self, *, superseded: bool = False) -> None:
        """Hand the mount back when the live broadcast ends."""
        if self._live_ingest_id is None:
            return
        logger.info(
            "Live broadcast %s on output %s ended%s",
            self._live_ingest_id,
            self.output_stream_id,
            " (superseded)" if superseded else "",
        )
        self._live_ingest_id = None
        self._live_generation = None
        if self.driver is not None:
            try:
                await self.driver.set_live_state(None)
            except Exception:
                logger.exception("Failed to clear live state for %s", self.output_stream_id)
        if superseded:
            return

        if self.session_id is None and not await self._adopt_session():
            # Live-only driver with no session to hand the mount to.
            self._shutting_down = True
            return
        await self._resync_after_live()

    async def _resync_after_live(self) -> None:
        """Return the driver to the session's queued playback after live.

        The session is resumed only when the takeover froze a playing queue
        and the user has not touched the session state since — an explicit
        command during the broadcast clears ``_resume_after_live``.
        """
        try:
            async with get_session() as db:
                session = await self._load_session(db)
                if session is None:
                    return
                resume = self._resume_after_live and session.state == "paused" and _current_track(session)
                self._resume_after_live = False
                if resume:
                    session.state = "playing"
                    session.position_anchor_at = _now_utc()
                    session.last_active_at = _now_utc()
                    await db.flush()
                    await self._publish_state(await session_state_dict(db, session))
                source, metadata = await self._resolve_source(db, session)
        except Exception:
            logger.exception("Failed to resync session %s after live", self.session_id)
            return

        if self.driver is None:
            return

        track_id = _current_track_id(session)
        if source is not None and session.state == "playing":
            try:
                duration = metadata.duration if metadata else None
                metadata = metadata or TrackMeta(track_id="", title="", artist="")
                await self.driver.set_source(
                    source,
                    position=_clamp_position(session.position_seconds, duration),
                    metadata=metadata,
                )
                await self.driver.update_metadata(metadata)
                self._active_track_id = track_id
            except Exception:
                logger.exception("Failed to restore driver source for %s", self.session_id)
                try:
                    await self.driver.pause()
                except Exception:
                    logger.exception("Failed to pause driver for %s", self.session_id)
        else:
            try:
                await self.driver.pause()
                if metadata is not None:
                    await self.driver.update_metadata(metadata)
            except Exception:
                logger.exception("Failed to pause driver for %s", self.session_id)

    async def _adopt_session(self) -> bool:
        """Attach a live-only driver to the output's driving session."""
        try:
            async with get_session() as db:
                session = await driving_stream_session(db, self.output_stream_id)
                session_id = session.id if session is not None else None
                session_user_id = session.user_id if session is not None else None
        except Exception:
            logger.exception("Failed to look up a session for output %s", self.output_stream_id)
            return False
        if session_id is None or session_user_id is None:
            return False
        self.session_id = session_id
        self.user_id = session_user_id
        await self._update_output_status(status="live")
        logger.info("Live-only driver for %s adopted session %s", self.output_stream_id, session_id)
        return True

    async def _live_only_tick(self) -> bool:
        """Upkeep for a driver running without a session.

        Returns False to stop the driver: the output disappeared or was
        disabled, or the broadcast claim vanished before it ever went on
        air. While broadcasting, a session that attaches to the output is
        adopted so the mount keeps playing once live ends.
        """
        now = time.monotonic()
        if now - self._adopt_poll_at < 1.0:
            return True
        self._adopt_poll_at = now
        try:
            async with get_session() as db:
                output = await db.get(OutputStream, self.output_stream_id)
                if output is None or output.provider_type != "http" or not output.enabled:
                    logger.info("Live output %s gone or disabled; stopping", self.output_stream_id)
                    return False
                session = await driving_stream_session(db, self.output_stream_id)
                session_id = session.id if session is not None else None
                user_id = session.user_id if session is not None else ""
        except Exception:
            logger.exception("Live-only upkeep failed for %s", self.output_stream_id)
            return True

        if session_id is not None:
            self.session_id = session_id
            self.user_id = user_id
            await self._update_output_status(status="live")
            logger.info("Live-only driver for %s adopted session %s", self.output_stream_id, session_id)
            if self._live_ingest_id is None:
                await self._sync_to_session()
            return True

        if self._live_ingest_id is None and not self._live_started:
            try:
                state = await read_live_state(self.worker.redis, self.output_stream_id)
            except Exception:
                return True
            if state is None:
                # The claim lapsed before the broadcast started.
                logger.info("Live key for %s vanished before going on air; stopping", self.output_stream_id)
                return False
        return True

    async def _drain_control_list(self) -> list[dict]:
        """Pop every control envelope currently queued for this session."""
        envelopes: list[dict] = []
        while True:
            try:
                raw = cast(
                    Optional[str],
                    await self.worker.redis.lpop(self._control_key, count=None),  # type: ignore[misc]
                )
            except Exception:
                # A transient Redis error must not kill the driver loop —
                # commands are re-polled on the next iteration anyway.
                logger.exception("Failed to pop control envelopes for %s", self.session_id)
                return envelopes
            if raw is None:
                return envelopes
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed control envelope: %s", raw)
                continue
            if envelope:
                envelopes.append(envelope)

    async def _collect_control_envelopes(self) -> list[dict]:
        """Drain the control list, letting command bursts settle into one batch.

        A single UI action often lands as several envelopes pushed a few
        hundred milliseconds apart (each awaited API call publishes its own),
        so after the first drain the collector waits out a short quiet period
        before handing the batch to ``_handle_commands``.
        """
        envelopes = await self._drain_control_list()
        if not envelopes:
            return envelopes
        burst_deadline = time.monotonic() + self._CONTROL_BURST_MAX_S
        quiet_deadline = time.monotonic() + self._CONTROL_SETTLE_S
        while time.monotonic() < quiet_deadline and time.monotonic() < burst_deadline:
            await asyncio.sleep(self._CONTROL_POLL_S)
            more = await self._drain_control_list()
            if more:
                envelopes.extend(more)
                quiet_deadline = time.monotonic() + self._CONTROL_SETTLE_S
        return envelopes

    async def _handle_commands(self, envelopes: list[dict]) -> None:
        """Apply a batch of control commands and sync the driver once."""
        commands = [str(envelope.get("command") or "") for envelope in envelopes]

        if "reload_output" in commands:
            await self._reload_driver()
            return

        try:
            async with get_session() as db:
                session = await self._load_session(db)
                if session is None:
                    return

                for envelope in envelopes:
                    command = str(envelope.get("command") or "")
                    args = envelope.get("args") or {}
                    connection_id = envelope.get("issued_by") or args.get("connection_id")
                    await self._apply_command(session, command, args, connection_id)

                session.last_active_at = _now_utc()
                await db.flush()
                state = await session_state_dict(db, session)
                await self._publish_state(state)

                source, metadata = await self._resolve_source(db, session)
        except Exception:
            logger.exception("Commands %s failed for session %s", commands, self.session_id)
            return

        if self.driver is None:
            return

        if self._live_ingest_id is not None:
            # The broadcast owns the mount: the committed session state
            # takes effect when it ends — except explicit transport
            # commands, which cancel the pending queue resume.
            if any(command in ("play", "pause", "stop", "play_at", "next", "prev") for command in commands):
                self._resume_after_live = False
            return

        track_id = _current_track_id(session)
        if source is not None and session.state == "playing":
            # Restart the decoder only when the track changed, a command
            # explicitly repositions playback, or the driver is still paused:
            # the API commits the new session state before publishing the
            # commands, so the loaded state can't tell us whether this is a
            # resume — but a paused driver feeding silence must be swapped
            # back to the real source. Control-only commands (e.g.
            # take_control, set_repeat) leave the stream untouched so
            # listeners are not interrupted.
            needs_source = (
                track_id != self._active_track_id
                or any(command in ("play_at", "next", "prev", "seek") for command in commands)
                or self.driver.is_paused
            )
            if needs_source:
                try:
                    duration = metadata.duration if metadata else None
                    metadata = metadata or TrackMeta(track_id="", title="", artist="")
                    await self.driver.set_source(
                        source,
                        position=_clamp_position(session.position_seconds, duration),
                        metadata=metadata,
                    )
                    await self.driver.update_metadata(metadata)
                    self._active_track_id = track_id
                except Exception:
                    logger.exception("Failed to set driver source for %s", self.session_id)
                    # A failed swap leaves the decoder dead; feed silence so
                    # the mount keeps a live stream instead of dead air.
                    try:
                        await self.driver.pause()
                    except Exception:
                        logger.exception("Failed to pause driver for %s", self.session_id)
        elif session.state in ("paused", "idle"):
            try:
                if "seek" in commands and source is not None:
                    await self.driver.seek(session.position_seconds)
                else:
                    await self.driver.pause()
            except Exception:
                logger.exception("Failed to pause driver for %s", self.session_id)

        if "set_volume" in commands:
            try:
                await self.driver.set_volume(session.volume)
            except Exception:
                logger.exception("Failed to set driver volume for %s", self.session_id)

    async def _reload_driver(self) -> None:
        """Restart the provider driver after the output's config changed."""
        logger.info("Reloading driver for output %s", self.output_stream_id)
        # The fresh driver knows nothing about an ongoing broadcast; clearing
        # the flags lets _poll_live restart it on the new driver.
        self._live_ingest_id = None
        self._live_generation = None
        await self._stop_driver()
        self._active_track_id = None
        try:
            await self._start_driver()
        except Exception as exc:
            logger.exception("Failed to restart driver for %s", self.output_stream_id)
            await self._update_output_status(status="error", last_error=str(exc)[:512])
            return
        await self._sync_to_session()

    async def _apply_command(
        self,
        session: PlaybackSession,
        command: str,
        args: dict,
        connection_id: Optional[str] = None,
    ) -> None:
        """Mutate the session state for a control command."""
        now = _now_utc()

        if command == "play":
            track = _current_track(session)
            if track is not None:
                session.state = "playing"
                session.position_anchor_at = now

        elif command == "pause":
            if session.state == "playing":
                session.position_seconds = _live_position_seconds(session, now)
                session.position_anchor_at = None
                session.state = "paused"

        elif command == "seek":
            seconds = args.get("seconds", 0)
            try:
                seconds = float(seconds)
            except (TypeError, ValueError):
                seconds = 0.0
            duration = _track_duration(session)
            session.position_seconds = _clamp_position(seconds, duration)
            if session.state == "playing":
                session.position_anchor_at = now
            else:
                session.position_anchor_at = None

        # Index-changing commands (next/prev/play_at/set_queue) are already
        # persisted by the playback API before the command reaches the worker;
        # the worker only needs to sync the driver to the updated session.

        elif command == "set_repeat":
            repeat = args.get("repeat", "off")
            if repeat in ("off", "all", "one"):
                session.repeat = repeat

        elif command == "set_shuffle":
            session.shuffle = bool(args.get("shuffle", False))

        elif command == "set_volume":
            try:
                volume = float(args.get("volume", session.volume or 1.0))
            except (TypeError, ValueError):
                volume = session.volume or 1.0
            session.volume = min(max(volume, 0.0), 1.0)

        elif command == "stop":
            session.state = "idle"
            session.position_seconds = 0.0
            session.position_anchor_at = None

        elif command == "take_control":
            if connection_id:
                session.controller_connection_id = connection_id

        elif command == "release_control" and connection_id and session.controller_connection_id == connection_id:
            session.controller_connection_id = None

        session.last_active_at = now

    async def _advance_index(
        self,
        session: PlaybackSession,
        *,
        direction: str = "next",
        position_seconds: Optional[float] = None,
    ) -> None:
        """Advance the queue index, handling repeat, shuffle and queue end."""
        queue = session.queue or []
        length = len(queue)
        current = session.current_index

        if direction == "next":
            next_index: Optional[int]
            if session.repeat == "one" and 0 <= current < length:
                next_index = current
            elif session.shuffle and length > 1:
                next_index = random.choice([i for i in range(length) if i != current])
            else:
                next_index = current + 1
                if next_index >= length:
                    next_index = 0 if session.repeat == "all" else None

            if next_index is None:
                session.state = "idle"
                session.position_seconds = 0.0
                session.position_anchor_at = None
                return

            session.current_index = next_index
            session.position_seconds = 0.0
            session.state = "playing"
            session.position_anchor_at = _now_utc()
            return

        if direction == "prev":
            if position_seconds is not None and position_seconds > 3 and 0 <= current < length:
                session.position_seconds = 0.0
                session.position_anchor_at = _now_utc() if session.state == "playing" else None
                return

            prev_index: Optional[int]
            if session.shuffle and length > 1:
                prev_index = random.choice([i for i in range(length) if i != current])
            else:
                prev_index = current - 1
                if prev_index < 0:
                    prev_index = length - 1 if session.repeat == "all" else None

            if prev_index is None:
                session.state = "idle"
                session.position_seconds = 0.0
                session.position_anchor_at = None
                return

            session.current_index = prev_index
            session.position_seconds = 0.0
            session.state = "playing"
            session.position_anchor_at = _now_utc()

    async def _drain_events(self) -> None:
        """Process events emitted by the provider driver."""
        if self.driver is None:
            return

        while not self.driver.events.empty():
            event = self.driver.events.get_nowait()
            if event.get("type") == "source_ended":
                await self._on_source_ended(event)
            elif event.get("type") == "error":
                logger.error("Driver error: %s", event.get("message"))
                await self._update_output_status(
                    status="error",
                    last_error=event.get("message"),
                )
            elif event.get("type") == "listeners":
                logger.debug("Listener count: %s", event.get("count"))

    def _clear_advance_retry(self) -> None:
        """Forget any pending failed-advance retry."""
        self._advance_retry_at = None
        self._advance_retry_event = None
        self._advance_retry_count = 0

    def _schedule_advance_retry(self, event: Optional[dict]) -> None:
        """Queue a failed track advance for another attempt shortly.

        The event is stored with the retry so the generation check still
        drops it if something else resyncs the driver meanwhile.
        """
        if self.driver is None:
            return
        self._advance_retry_count += 1
        self._advance_retry_event = event
        self._advance_retry_at = time.monotonic() + self._ADVANCE_RETRY_DELAY_S

    async def _maybe_retry_advance(self) -> None:
        """Re-run a failed track advance once its backoff has elapsed."""
        if self._advance_retry_at is None or time.monotonic() < self._advance_retry_at:
            return
        self._advance_retry_at = None
        if self.driver is None:
            self._clear_advance_retry()
            return
        if self._advance_retry_count > self._ADVANCE_MAX_RETRIES:
            logger.error(
                "source_ended retries exhausted for %s; feeding silence",
                self.session_id,
            )
            self._clear_advance_retry()
            if self.driver is not None:
                try:
                    await self.driver.pause()
                except Exception:
                    logger.exception("Failed to pause driver for %s", self.session_id)
            return
        await self._on_source_ended(self._advance_retry_event)

    async def _on_source_ended(self, event: Optional[dict] = None) -> None:
        """Handle a track finishing: record the listen, then advance or idle."""
        # Drop events from a decoder generation that has been superseded, e.g.
        # a command swapped the source before this event was drained.
        if self.driver is not None and event:
            gen = event.get("generation")
            current_gen = getattr(self.driver, "generation", None)
            if gen is not None and current_gen is not None and gen != current_gen:
                logger.debug("Ignoring stale source_ended gen=%s current=%s", gen, current_gen)
                return
        if self._live_ingest_id is not None:
            # The live decoder reached the end of its ingest feed (the
            # browser stopped or disconnected): hand the mount back to the
            # session. Broadcast audio never records a listen.
            await self._end_live()
            return
        try:
            async with get_session() as db:
                session = await self._load_session(db)
                if session is None:
                    return

                # Only move the queue forward while it still points at the
                # track the driver just finished. Otherwise the advance
                # already happened (a command resynced, or this is a retry
                # of a failed attempt whose index update committed) — jump
                # straight to reconciling the driver instead of skipping
                # the listener's track. The ``_advanced`` marker on the
                # event covers the queue-end case, where the committed
                # advance leaves the index unchanged.
                finished = _current_track_id(session)
                advance_pending = not (event or {}).get("_advanced")
                if advance_pending and (self._active_track_id is None or finished == self._active_track_id):
                    if finished and self._record_listens:
                        await record_server_listen(db, session, finished)

                    # Always advance autonomously: the controller only sends
                    # commands on explicit user actions, so pausing here would
                    # just starve the encoder until someone presses play again.
                    await self._advance_index(session, direction="next")
                    await db.flush()
                    state = await session_state_dict(db, session)
                    await self._publish_state(state)

                source, metadata = await self._resolve_source(db, session)
        except Exception:
            logger.exception("source_ended handling failed for %s", self.session_id)
            self._schedule_advance_retry(event)
            return

        # The queue handling for this event is committed — a retry of a
        # later failure must not advance again.
        if event is not None:
            event["_advanced"] = True

        if self.driver is None:
            return

        track_id = _current_track_id(session)
        if source is not None and session.state == "playing":
            try:
                metadata = metadata or TrackMeta(track_id="", title="", artist="")
                await self.driver.set_source(
                    source,
                    position=session.position_seconds,
                    metadata=metadata,
                )
                self._active_track_id = track_id
                await self.driver.update_metadata(metadata)
            except Exception:
                logger.exception("Failed to set next driver source for %s", self.session_id)
                self._schedule_advance_retry(event)
                return
        else:
            try:
                await self.driver.pause()
            except Exception:
                logger.exception("Failed to pause driver for %s", self.session_id)
                self._schedule_advance_retry(event)
                return
        self._clear_advance_retry()

    async def _resolve_metadata(self, db, session: PlaybackSession) -> Optional[TrackMeta]:
        """Resolve the current queue track to a TrackMeta without touching audio."""
        track_dict = _current_track(session)
        if track_dict is None:
            return None

        track_id = track_dict.get("id")
        if not track_id:
            return None

        track = await db.execute(
            select(Track).where(Track.id == track_id).options(selectinload(Track.artist), selectinload(Track.album))
        )
        track = track.scalar_one_or_none()
        if track is None:
            return None

        return TrackMeta(
            track_id=str(track.id),
            title=track.title,
            artist=track.artist.name if track.artist is not None else "",
            album=track.album.title if track.album is not None else None,
            duration=track.duration,
        )

    async def _resolve_source(
        self,
        db,
        session: PlaybackSession,
    ) -> tuple[Optional[AudioSource], Optional[TrackMeta]]:
        """Resolve the current queue track to an AudioSource and TrackMeta."""
        track_dict = _current_track(session)
        track_id = track_dict.get("id") if track_dict else None
        metadata = await self._resolve_metadata(db, session)
        if metadata is None or track_id is None:
            return None, None

        stored_file = await resolve_track_file(db, track_id)
        if stored_file is not None:
            backend = get_storage(self.worker.config.storage)
            local_path = await backend.retrieve(stored_file.storage_path)
            if local_path is not None:
                return (
                    AudioSource(
                        kind="path",
                        path=local_path,
                        content_type=stored_file.content_type,
                        size=stored_file.size,
                    ),
                    metadata,
                )

        from ..models.user import User as _User

        session_user = await db.get(_User, session.user_id) if session.user_id else None
        try:
            external_stream = await resolve_external_stream(db, track_id, user=session_user)
        except Exception:
            logger.warning("External stream resolution failed for track %s", track_id, exc_info=True)
            external_stream = None
        if external_stream is not None:
            if external_stream.path is not None:
                return (
                    AudioSource(
                        kind="path",
                        path=external_stream.path,
                        content_type=external_stream.content_type,
                        size=external_stream.size,
                    ),
                    metadata,
                )

            if external_stream.url is not None:
                return (
                    AudioSource(
                        kind="url",
                        url=external_stream.url,
                        content_type=external_stream.content_type,
                        size=external_stream.size,
                    ),
                    metadata,
                )

            if external_stream.iterator is not None:
                temp_path = await self._spill_iterator(external_stream.iterator)
                if temp_path is not None:
                    return (
                        AudioSource(
                            kind="path",
                            path=temp_path,
                            content_type=external_stream.content_type,
                            size=temp_path.stat().st_size if temp_path.exists() else None,
                        ),
                        metadata,
                    )

        return None, metadata

    async def _spill_iterator(self, iterator) -> Optional[Path]:
        """Write an async byte iterator to a temporary file and return its path."""
        import tempfile

        fd, name = tempfile.mkstemp(prefix="songhive-stream-", suffix=".tmp")
        os.close(fd)
        path = Path(name)

        try:
            async with aiofiles.open(path, "wb") as f:
                async for chunk in iterator:
                    if chunk:
                        await f.write(chunk)
            return path
        except Exception:
            logger.exception("Failed to spill iterator stream")
            with suppress(OSError):
                path.unlink()
            return None

    async def _update_output_status(
        self,
        status: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        """Update the session output status row."""
        if (status is None and last_error is None) or self.session_id is None:
            return
        try:
            async with get_session() as db:
                stmt = select(PlaybackSessionOutput).where(
                    PlaybackSessionOutput.session_id == self.session_id,
                    PlaybackSessionOutput.output_stream_id == self.output_stream_id,
                )
                result = await db.execute(stmt)
                pso = result.scalar_one_or_none()
                if pso is None:
                    return
                if status is not None:
                    pso.status = status
                if last_error is not None:
                    pso.last_error = last_error
        except Exception:
            logger.exception("Failed to update output status for %s", self.output_stream_id)

    async def _load_session(self, db) -> Optional[PlaybackSession]:
        """Load the session with outputs, ensuring the stream output is still attached."""
        result = await db.execute(
            select(PlaybackSession)
            .where(PlaybackSession.id == self.session_id)
            .options(selectinload(PlaybackSession.outputs))
        )
        return result.scalar_one_or_none()

    async def _session_has_stream_output(self) -> bool:
        """Return whether the session still has an enabled stream output."""
        try:
            async with get_session() as db:
                stmt = (
                    select(PlaybackSessionOutput)
                    .join(OutputStream)
                    .where(
                        PlaybackSessionOutput.session_id == self.session_id,
                        PlaybackSessionOutput.output_stream_id == self.output_stream_id,
                        OutputStream.enabled.is_(True),
                    )
                )
                result = await db.execute(stmt)
                return result.scalar_one_or_none() is not None
        except Exception:
            # A transient DB error must not read as "output gone": assume the
            # stream output is still attached rather than tearing the driver
            # (and every listener's connection) down over a blip.
            logger.exception("Failed to check stream output for %s", self.session_id)
            return True

    async def _should_stop_on_idle(self) -> bool:
        """Return True when the output has been idle with no controller/listeners."""
        if self._live_ingest_id is not None:
            return False
        try:
            async with get_session() as db:
                session = await self._load_session(db)
                if session is None:
                    return True

                no_controller = not session.controller_connection_id
                no_listeners = True
                if self.driver is not None:
                    try:
                        listener_count = await self.driver.listener_count()
                    except Exception:
                        listener_count = 0
                    no_listeners = listener_count == 0

                if no_controller and no_listeners:
                    now = time.monotonic()
                    if self._idle_since is None:
                        self._idle_since = now
                    elapsed = now - self._idle_since
                    if elapsed >= self.worker.config.streams.background_idle_timeout_seconds:
                        await self._update_output_status(status="stopped")
                        return True
                else:
                    self._idle_since = None
        except Exception:
            logger.exception("Idle check failed for %s", self.session_id)
        return False

    async def _publish_state(self, state: dict) -> None:
        """Publish the session state to the user's tabs."""
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _publish_sync, self.user_id, state)
        except Exception:
            logger.exception("Failed to publish playback state for %s", self.user_id)


def _current_track_id(session: PlaybackSession) -> Optional[str]:
    """Return the current queue track id, if any."""
    track = _current_track(session)
    if track is None:
        return None
    return track.get("id")


def _publish_sync(user_id: str, state: dict) -> None:
    """Synchronous wrapper for WebSocket publishing from the worker loop."""
    publish_playback_event(user_id, state)
