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

from ..config import load_config
from ..config.schema import SonghiveConfig
from ..models.base import get_session, init_db
from ..models.output_stream import OutputStream
from ..models.playback_session import PlaybackSession, PlaybackSessionOutput
from ..models.track import Track
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
from ..streams.driver import OutputDriver
from ..streams.registry import get_output
from ..streams.types import AudioSource, TrackMeta

logger = logging.getLogger(__name__)

CONTROL_KEY = "songhive:playback:control:{session_id}"
LOCK_KEY = "songhive:stream:lock:{output_id}"


async def run_stream_worker() -> None:
    """Entry point for the ``songhive stream-worker`` command."""
    config = load_config([])
    init_db(config.database.url)
    worker = StreamWorker(config)
    await worker.run()


class StreamWorker:
    """Discovers active stream sessions and spawns a ``SessionDriver`` for each."""

    def __init__(self, config: SonghiveConfig) -> None:
        self.config = config
        self.redis = create_redis_client(config)
        self._shutting_down = False
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    async def run(self) -> None:
        """Run the discovery loop until signalled to stop."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._request_shutdown)

        while not self._shutting_down:
            await self._scan_sessions()
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

    async def _reap_finished(self) -> None:
        """Remove completed tasks from the active map and refresh their locks."""
        for output_id in list(self._tasks):
            task = self._tasks[output_id]
            if task.done():
                with suppress(Exception):
                    await task
                del self._tasks[output_id]


class SessionDriver:
    """Drives one server output for a single playback session."""

    def __init__(
        self,
        worker: StreamWorker,
        session_id: str,
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

    @property
    def _control_key(self) -> str:
        return _control_key(self.session_id)

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

            provider_cls = get_output(output_stream.provider_type)
            provider = provider_cls()
            config = decrypt_json(output_stream.config)
            await provider.validate_config(config)

            # Allow icecast config to fall back to the configured ffmpeg path.
            if output_stream.provider_type == "icecast" and not config.get("ffmpeg_path"):
                config["ffmpeg_path"] = (
                    self.worker.config.streams.icecast_ffmpeg_path or self.worker.config.streaming.ffmpeg_path
                )

            self.driver = provider.create_driver(config)
            await self.driver.start()
            await self._update_output_status(status="live")

    async def _stop_driver(self) -> None:
        """Stop the provider driver, if it was started."""
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

            # Drain any pending control commands.
            raw = cast(Optional[str], await self.worker.redis.lpop(self._control_key, count=None))  # type: ignore[misc]
            if raw is not None:
                try:
                    envelope = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Ignoring malformed control envelope: %s", raw)
                    envelope = None
                if envelope:
                    await self._handle_command(envelope)

            # Drain any driver events.
            await self._drain_events()

            # Stop the worker if the session/output has been removed or disabled.
            if not await self._session_has_stream_output():
                logger.info("Session %s no longer has a stream output", self.session_id)
                break

            # Background idle shutdown: nothing playing and no controller/listeners.
            if await self._should_stop_on_idle():
                logger.info("Idle timeout for session %s; stopping driver", self.session_id)
                break

            await asyncio.sleep(0.1)

    async def _sync_to_session(self) -> None:
        """Synchronise the driver to the current session state on startup."""
        if self.driver is None:
            return
        try:
            async with get_session() as db:
                session = await self._load_session(db)
                if session is None:
                    return
                if session.state == "playing":
                    source, metadata = await self._resolve_source(db, session)
                    if source is not None:
                        await self.driver.set_source(
                            source,
                            position=session.position_seconds,
                            metadata=metadata or TrackMeta(track_id="", title="", artist=""),
                        )
                        self._active_track_id = _current_track_id(session)
                        session.position_anchor_at = _now_utc()
                        session.last_active_at = _now_utc()
                elif session.state == "paused":
                    await self.driver.pause()
        except Exception:
            logger.exception("Failed to sync session %s on startup", self.session_id)

    async def _handle_command(self, envelope: dict) -> None:
        """Apply a non-index control command and sync the driver to session state."""
        command = envelope.get("command") or ""
        args = envelope.get("args") or {}

        try:
            async with get_session() as db:
                session = await self._load_session(db)
                if session is None:
                    return

                prev_state = session.state
                connection_id = envelope.get("issued_by") or args.get("connection_id")
                await self._apply_command(db, session, command, args, connection_id)

                session.last_active_at = _now_utc()
                await db.flush()
                state = await session_state_dict(db, session)
                await self._publish_state(state)

                source, metadata = await self._resolve_source(db, session)
        except Exception:
            logger.exception("Command %s failed for session %s", command, self.session_id)
            return

        if self.driver is None:
            return

        track_id = _current_track_id(session)
        if source is not None and session.state == "playing":
            # Restart the decoder only when the track changed or the command
            # explicitly repositions playback; control-only commands (e.g.
            # take_control, set_repeat) leave the stream untouched so
            # listeners are not interrupted.
            needs_source = (
                track_id != self._active_track_id
                or command in ("play_at", "next", "prev", "seek")
                or (command == "play" and prev_state != "playing")
            )
            if needs_source:
                try:
                    duration = metadata.duration if metadata else None
                    await self.driver.set_source(
                        source,
                        position=_clamp_position(session.position_seconds, duration),
                        metadata=metadata or TrackMeta(track_id="", title="", artist=""),
                    )
                    self._active_track_id = track_id
                except Exception:
                    logger.exception("Failed to set driver source for %s", self.session_id)
        elif session.state in ("paused", "idle"):
            try:
                if command == "seek" and source is not None:
                    await self.driver.seek(session.position_seconds)
                else:
                    await self.driver.pause()
            except Exception:
                logger.exception("Failed to pause driver for %s", self.session_id)

    async def _apply_command(
        self,
        db,
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
        try:
            async with get_session() as db:
                session = await self._load_session(db)
                if session is None:
                    return

                track = _current_track(session)
                track_id = track.get("id") if track else None
                if track_id:
                    await record_server_listen(db, session, track_id)

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
            return

        if self.driver is None:
            return

        track_id = _current_track_id(session)
        if source is not None and session.state == "playing":
            try:
                await self.driver.set_source(
                    source,
                    position=session.position_seconds,
                    metadata=metadata or TrackMeta(track_id="", title="", artist=""),
                )
                self._active_track_id = track_id
            except Exception:
                logger.exception("Failed to set next driver source for %s", self.session_id)
        else:
            try:
                await self.driver.pause()
            except Exception:
                logger.exception("Failed to pause driver for %s", self.session_id)

    async def _resolve_source(
        self,
        db,
        session: PlaybackSession,
    ) -> tuple[Optional[AudioSource], Optional[TrackMeta]]:
        """Resolve the current queue track to an AudioSource and TrackMeta."""
        track_dict = _current_track(session)
        if track_dict is None:
            return None, None

        track_id = track_dict.get("id")
        if not track_id:
            return None, None

        track = await db.execute(
            select(Track).where(Track.id == track_id).options(selectinload(Track.artist), selectinload(Track.album))
        )
        track = track.scalar_one_or_none()
        if track is None:
            return None, None

        metadata = TrackMeta(
            track_id=str(track.id),
            title=track.title,
            artist=track.artist.name if track.artist is not None else "",
            album=track.album.title if track.album is not None else None,
            duration=track.duration,
        )

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

        external_stream = await resolve_external_stream(db, track_id)
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
                temp_path = await self._spill_iterator(external_stream.iterator, external_stream.content_type)
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

    async def _spill_iterator(self, iterator, content_type: Optional[str]) -> Optional[Path]:
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
        if status is None and last_error is None:
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
            logger.exception("Failed to check stream output for %s", self.session_id)
            return False

    async def _should_stop_on_idle(self) -> bool:
        """Return True when the output has been idle with no controller/listeners."""
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
