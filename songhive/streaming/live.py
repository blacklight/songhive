"""
Live broadcast ingest WebSocket.

``/ws/live/{output_id}`` accepts compressed audio chunks from the owner of a
native HTTP stream mount (typically a browser ``MediaRecorder``) and relays
them into the ``songhive:stream:ingest:{output_id}`` Redis stream. The stream
worker's ``SessionDriver`` polls the live claim key, decodes the ingest feed
through a ``kind="live"`` source, and the mount's persistent encoder keeps
serving listeners — this handler only ever writes to the ingest side. See
``streams/live.py`` for the Redis key layout and claim protocol.

Client → server messages:

- ``{"action": "start", "ingest_id": <id>, "mime": ..., "title": ...}`` must
  be the first message: it claims the live slot (single broadcaster per
  mount) and registers the broadcast.
- Binary frames are MediaRecorder chunks, relayed verbatim as ``d`` entries.
- ``{"action": "stop"}`` appends an ``end`` entry and closes cleanly.

Server → client messages:

- ``{"type": "accepted"}`` — the claim was registered; recording may start.
- ``{"type": "on_air"}`` — the worker swapped the mount to this ingest.
- ``{"type": "listeners", "count": n}`` — listener count updates while on air.
- ``{"type": "stopped", "reason": ...}`` — the broadcast ended.
- ``{"type": "error", "code": ..., "message": ...}`` — terminal failure,
  followed by a close frame.

Terminal close codes:

- 4001 — unauthenticated (shared with ``/ws/events``)
- 4003 — forbidden: feature disabled, not the owner, non-HTTP output,
  output disabled, or unsupported ingest format
- 4004 — output not found
- 4409 — another broadcaster holds the live slot (or the claim was lost)
- 4415 — the worker did not take the mount over within
  ``streams.live_start_timeout_seconds``
- 4429 — the browser produced audio faster than the ingest accepts
  (write-queue backpressure or the configured bitrate cap)
- 4503 — no stream worker heartbeat (``songhive stream-worker`` not running)
- 4504 — internal error: Redis unavailable, output went away mid-broadcast,
  or the worker dropped the broadcast
"""

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, Optional, Union

import tornado.websocket

from ..models.base import get_session
from ..models.output_stream import OutputStream
from ..services.secrets import decrypt_json
from ..streams.http import normalize_mount, stream_listener_pattern, stream_meta_key
from ..streams.live import (
    LIVE_KEY_REFRESH_SECONDS,
    LIVE_KEY_TTL_SECONDS,
    delete_live_key,
    demuxer_for_mime,
    drop_ingest_header,
    encode_live_state,
    ingest_key,
    live_key,
    read_live_state,
    refresh_live_key,
    store_ingest_header,
    worker_heartbeat_present,
)
from ..ws.auth import AuthenticatedWebSocket

logger = logging.getLogger(__name__)

_INGEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_MAX_TITLE_LEN = 256


class LiveIngestHandler(AuthenticatedWebSocket):
    """Authenticated live-audio ingest endpoint for one stream output."""

    # Outstanding Redis writes allowed before the browser is too fast.
    _WRITE_QUEUE_MAX = 64
    _META_POLL_SECONDS = 0.5
    _LISTENER_POLL_SECONDS = 2.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user_id: Optional[str] = None
        self._output_id = ""
        self._mount = ""
        self._ingest_id: Optional[str] = None
        self._live_payload: Optional[str] = None
        self._write_queue: asyncio.Queue = asyncio.Queue(maxsize=self._WRITE_QUEUE_MAX)
        self._writer_task: Optional[asyncio.Task] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._meta_task: Optional[asyncio.Task] = None
        self._deadline_timer: Optional[Any] = None
        self._closing = False
        self._cleaned = False
        self._on_air = False
        self._header_stored = False
        self._rate_allowance = 0.0
        self._rate_last = 0.0

    @property
    def _config(self):
        return self.application.settings.get("config")

    @property
    def config(self):
        config = self._config
        assert config
        return config

    @property
    def _redis(self):
        return self.application.settings.get("redis")

    def _send_json(self, payload: dict) -> None:
        """Write a JSON control message, ignoring a dead socket."""
        try:
            self.write_message(json.dumps(payload))
        except tornado.websocket.WebSocketClosedError:
            pass
        except Exception:
            logger.exception("Failed to write live ingest message")

    async def _terminate(self, code: int, reason: str, *, error_code: Optional[str] = None) -> None:
        """Send a final error/stopped message and close with ``code``."""
        if self._closing:
            return
        self._closing = True
        if error_code is not None:
            self._send_json({"type": "error", "code": error_code, "message": reason})
        try:
            self.close(code, reason)
        except Exception:
            pass

    async def open(self, *args: str, **kwargs: str) -> None:
        """
        Authenticate and authorize the ingest connection.

        Audio does not flow until a ``start`` message arrives; ``open`` only
        establishes that the requester may broadcast on this output.
        """
        output_id = args[0] if args else kwargs.get("output_id", "")
        config = self._config
        redis = self._redis
        if config is None or redis is None:
            await self._terminate(4504, "server misconfigured", error_code="internal_error")
            return
        if not config.streams.enabled or not config.streams.live_enabled:
            await self._terminate(4003, "live streaming is disabled", error_code="disabled")
            return

        user_id = await self._authenticate_websocket()
        if user_id is None:
            return

        try:
            async with get_session() as session:
                output = await session.get(OutputStream, output_id)
                if output is None:
                    await self._terminate(4004, "stream output not found", error_code="not_found")
                    return
                mount = normalize_mount(decrypt_json(output.config).get("mount"))
                if output.provider_type != "http" or not output.enabled or str(output.user_id) != user_id:
                    await self._terminate(4003, "not allowed to broadcast on this output", error_code="forbidden")
                    return
        except tornado.websocket.WebSocketClosedError:
            raise
        except Exception:
            logger.exception("Failed to authorize live ingest for output %s", output_id)
            await self._terminate(4504, "internal error", error_code="internal_error")
            return

        try:
            if not await worker_heartbeat_present(redis):
                await self._terminate(4503, "stream worker unavailable", error_code="worker_unavailable")
                return
        except Exception:
            logger.exception("Worker heartbeat check failed for %s", output_id)
            await self._terminate(4503, "stream worker unavailable", error_code="worker_unavailable")
            return

        self.user_id = user_id
        self._output_id = str(output.id)
        self._mount = mount
        logger.info("Live ingest connection accepted for output %s", output_id)

    async def on_message(self, message: Union[str, bytes]) -> None:
        """Handle start/stop control messages and binary audio chunks."""
        if isinstance(message, bytes):
            if self._ingest_id is None:
                await self._terminate(4003, "expected a start message", error_code="expected_start")
                return
            await self._handle_chunk(message)
            return

        try:
            payload = json.loads(message)
        except (TypeError, ValueError):
            self._send_json({"type": "error", "code": "bad_message", "message": "invalid JSON"})
            return
        if not isinstance(payload, dict):
            return

        action = payload.get("action")
        if action == "start":
            if self._ingest_id is not None:
                self._send_json({"type": "error", "code": "already_started", "message": "broadcast already started"})
                return
            await self._handle_start(payload)
        elif action == "stop" and self._ingest_id is not None:
            await self._stop_gracefully("stopped by broadcaster")

    async def _handle_start(self, payload: dict) -> None:
        """Claim the live slot and register the broadcast."""
        redis = self._redis
        ingest_id = str(payload.get("ingest_id") or "")
        mime = str(payload.get("mime") or "")
        title = str(payload.get("title") or "")[:_MAX_TITLE_LEN]

        if not _INGEST_ID_RE.match(ingest_id):
            await self._terminate(4003, "invalid ingest_id", error_code="bad_ingest_id")
            return
        if demuxer_for_mime(mime) is None:
            await self._terminate(4003, f"unsupported ingest format: {mime}", error_code="unsupported_format")
            return

        live_payload = encode_live_state(
            ingest_id=ingest_id,
            user_id=self.user_id or "",
            mime=mime,
            title=title,
        )
        try:
            assert redis  # for mypy
            claimed = await redis.set(
                live_key(self._output_id),
                live_payload,
                nx=True,
                ex=LIVE_KEY_TTL_SECONDS,
            )
            if not claimed:
                # The same owner may supersede their own stale claim (e.g. a
                # reconnect whose old socket has not noticed yet); anyone
                # else holding the slot is a conflict.
                existing = await read_live_state(redis, self._output_id)
                if existing is not None and existing.user_id == self.user_id:
                    await redis.set(live_key(self._output_id), live_payload, ex=LIVE_KEY_TTL_SECONDS)
                    claimed = True
            if not claimed:
                await self._terminate(4409, "another broadcast is already live", error_code="conflict")
                return
            await redis.xadd(
                ingest_key(self._output_id),
                {"start": ingest_id, "mime": mime},
                maxlen=self.config.streams.live_ingest_max_entries,
                approximate=True,
            )
        except Exception:
            logger.exception("Failed to claim live slot for %s", self._output_id)
            await self._terminate(4504, "internal error", error_code="internal_error")
            return

        self._ingest_id = ingest_id
        self._live_payload = live_payload
        # Token bucket: the configured max bitrate plus a one-second burst.
        rate_bps = max(1, int(self.config.streams.live_max_bitrate_kbps)) * 125
        self._rate_allowance = float(rate_bps)
        self._rate_last = time.monotonic()

        self._writer_task = asyncio.create_task(self._writer_loop())
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._meta_task = asyncio.create_task(self._meta_poll_loop())

        max_seconds = float(self.config.streams.live_max_duration_seconds)
        if max_seconds > 0:
            self._deadline_timer = asyncio.get_running_loop().call_later(
                max_seconds,
                lambda: asyncio.ensure_future(self._max_duration_reached()),
            )

        self._send_json({"type": "accepted", "ingest_id": ingest_id})

    async def _handle_chunk(self, chunk: bytes) -> None:
        """Relay one binary audio chunk into the ingest stream."""
        if not chunk:
            return

        # Bitrate cap: refill the bucket by elapsed time and deduct the chunk.
        now = time.monotonic()
        rate_bps = max(1, int(self.config.streams.live_max_bitrate_kbps)) * 125
        self._rate_allowance = min(self._rate_allowance + (now - self._rate_last) * rate_bps, float(rate_bps))
        self._rate_last = now
        self._rate_allowance -= len(chunk)
        if self._rate_allowance < 0:
            await self._terminate(4429, "ingest bitrate limit exceeded", error_code="bitrate_exceeded")
            return

        # The first chunk carries the container header; keep it so a worker
        # restarting mid-broadcast can re-open the stream at a clean point.
        if not self._header_stored:
            self._header_stored = True
            try:
                await store_ingest_header(self._redis, self._output_id, self._ingest_id or "", chunk)
            except Exception:
                logger.warning("Failed to store ingest header for %s", self._output_id)

        try:
            self._write_queue.put_nowait(("chunk", chunk))
        except asyncio.QueueFull:
            await self._terminate(
                4429,
                "ingest is congested; the connection cannot keep up",
                error_code="backpressure",
            )

    async def _writer_loop(self) -> None:
        """Serialize ingest XADDs so stream order matches arrival order."""
        redis = self._redis
        key = ingest_key(self._output_id)
        maxlen = self.config.streams.live_ingest_max_entries
        try:
            while True:
                kind, data = await self._write_queue.get()
                assert redis  # for mypy
                if kind == "end":
                    await redis.xadd(key, {"end": data}, maxlen=maxlen, approximate=True)
                    return
                await redis.xadd(
                    key,
                    {"d": base64.b64encode(data).decode("ascii")},
                    maxlen=maxlen,
                    approximate=True,
                )
        except asyncio.CancelledError:
            logger.warning("Ingest writer cancelled for %s", self._output_id)
            raise
        except Exception:
            logger.exception("Ingest writer failed for %s", self._output_id)
            await self._terminate(4504, "ingest relay failed", error_code="internal_error")

    async def _refresh_loop(self) -> None:
        """Refresh the live claim and re-verify the output while broadcasting."""
        redis = self._redis
        try:
            while True:
                await asyncio.sleep(LIVE_KEY_REFRESH_SECONDS)
                ok = await refresh_live_key(redis, self._output_id, self._live_payload or "")
                if not ok:
                    # The claim expired or was superseded; stop broadcasting.
                    await self._terminate(4409, "live slot was taken over", error_code="conflict")
                    return
                async with get_session() as session:
                    output = await session.get(OutputStream, self._output_id)
                if output is None or not output.enabled or output.provider_type != "http":
                    await self._terminate(4504, "stream output went away", error_code="output_gone")
                    return
        except asyncio.CancelledError:
            logger.warning("Live refresh cancelled for %s", self._output_id)
            raise
        except Exception:
            logger.exception("Live refresh failed for %s", self._output_id)
            await self._terminate(4504, "internal error", error_code="internal_error")

    async def _meta_poll_loop(self) -> None:
        """
        Watch the mount meta for the worker's on-air acknowledgement.

        The ingest handler is never told on-air directly: the worker flips
        ``live_ingest_id`` in ``songhive:stream:meta:{mount}`` when the live
        source is swapped in (``HttpStreamDriver.set_live_state``). Before
        acknowledgement, exceeding the start timeout closes with 4415; after
        it, the flag disappearing means the worker ended the broadcast.
        """
        redis = self._redis
        meta_key = stream_meta_key(self._mount)
        deadline = time.monotonic() + float(self.config.streams.live_start_timeout_seconds)
        listener_at = 0.0
        try:
            while True:
                await asyncio.sleep(self._META_POLL_SECONDS)
                live_ingest_id = None
                meta_present = False
                try:
                    assert redis  # for mypy
                    raw = await redis.get(meta_key)
                    if raw is not None:
                        meta_present = True
                        meta = json.loads(raw)
                        if isinstance(meta, dict):
                            live_ingest_id = meta.get("live_ingest_id")
                except Exception:
                    # A transient error must not flap the broadcast.
                    continue

                if not self._on_air:
                    if live_ingest_id == self._ingest_id:
                        self._on_air = True
                        self._send_json({"type": "on_air", "ingest_id": self._ingest_id})
                    elif time.monotonic() >= deadline:
                        await self._terminate(
                            4415,
                            "stream worker did not acknowledge the broadcast",
                            error_code="start_timeout",
                        )
                        return
                    continue

                if live_ingest_id != self._ingest_id:
                    reason = "stream offline" if not meta_present else "broadcast ended"
                    self._send_json({"type": "stopped", "reason": reason})
                    code = 4504 if not meta_present else 1000
                    await self._terminate(code, reason)
                    return

                if time.monotonic() - listener_at >= self._LISTENER_POLL_SECONDS:
                    listener_at = time.monotonic()
                    try:
                        count = 0
                        async for _ in redis.scan_iter(match=stream_listener_pattern(self._mount), count=100):
                            count += 1
                        self._send_json({"type": "listeners", "count": count})
                    except Exception:
                        pass
        except asyncio.CancelledError:
            logger.warning("Meta poll cancelled for %s", self._output_id)
            raise

    async def _max_duration_reached(self) -> None:
        """End the broadcast when the configured maximum duration elapses."""
        self._send_json({"type": "stopped", "reason": "max_duration"})
        await self._stop_gracefully("maximum broadcast duration reached")

    async def _stop_gracefully(self, reason: str) -> None:
        """Append the end entry, notify the client, and close cleanly."""
        if self._closing:
            return
        self._closing = True
        if self._ingest_id is not None:
            try:
                self._write_queue.put_nowait(("end", self._ingest_id))
            except asyncio.QueueFull:
                # The queue only ever carries one end entry by construction,
                # but never let a stop fail on a saturated chunk queue.
                try:
                    self._write_queue.get_nowait()
                    self._write_queue.put_nowait(("end", self._ingest_id))
                except Exception:
                    pass
        self._send_json({"type": "stopped", "reason": reason})
        try:
            self.close(1000, reason)
        except Exception:
            pass

    def on_close(self) -> None:
        """Schedule resource cleanup after the socket closes."""
        asyncio.ensure_future(self._cleanup())

    async def _cleanup(self) -> None:
        """Release the live claim and mark the ingest stream ended."""
        if self._cleaned:
            return
        self._cleaned = True

        if self._deadline_timer is not None:
            self._deadline_timer.cancel()
            self._deadline_timer = None
        for task in (self._meta_task, self._refresh_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        redis = self._redis
        output_id = self._output_id
        ingest_id = self._ingest_id
        payload = self._live_payload

        if redis is not None and ingest_id is not None:
            # Signal the worker's ingest iterator to finish promptly even if
            # the writer task is gone, then release the claim only if it is
            # still ours (compare-and-delete).
            try:
                await redis.xadd(
                    ingest_key(output_id),
                    {"end": ingest_id},
                    maxlen=self.config.streams.live_ingest_max_entries,
                    approximate=True,
                )
            except Exception:
                pass
            if self._writer_task is not None:
                # Let a still-running writer drain its queue briefly so
                # in-flight chunks are not dropped by a graceful stop.
                try:
                    await asyncio.wait_for(asyncio.shield(self._writer_task), timeout=2.0)
                except Exception:
                    pass
            try:
                await delete_live_key(redis, output_id, payload or "")
                await drop_ingest_header(redis, output_id, ingest_id)
            except Exception:
                pass

        if self._writer_task is not None:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except asyncio.CancelledError:
                pass
            self._writer_task = None
