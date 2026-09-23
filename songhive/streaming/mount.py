"""
Tornado handler for native HTTP stream mountpoints.

``GET /streams/{mount}`` serves the audio a ``http`` output's stream worker
publishes to the Redis stream ``songhive:stream:data:{mount}`` (see
``songhive/streams/http.py``). Each listener is an independent XREAD cursor
into the capped stream, so fan-out is handled by Redis and a slow client only
stalls itself. New listeners are bursted with the most recent entries first
(``streams.http_stream_burst_entries``) so they can start decoding
immediately, mirroring an Icecast burst buffer.

Because audio must be played in real time, a listener that falls behind is
dropped forward instead of being fed the backlog: entries older than
``streams.http_stream_max_lag_seconds`` (entry IDs are server millisecond
timestamps) are skipped rather than written. ``X-Accel-Buffering: no`` keeps
buffering proxies from hiding that lag inside their own buffers.

The mount is considered live only while the driver keeps the short-TTL
``songhive:stream:meta:{mount}`` key refreshed; once it expires (or an
``{"end": "1"}`` sentinel arrives) listeners are disconnected.
"""

import base64
import hmac
import json
import logging
import secrets
import time

import tornado.iostream
import tornado.web

from ..config.schema import SonghiveConfig
from ..models.base import get_session
from ..services.outputs import find_http_stream_output
from ..streams.http import (
    normalize_mount,
    stream_data_key,
    stream_listener_key,
    stream_listener_pattern,
    stream_meta_key,
)

logger = logging.getLogger(__name__)


class StreamMountHandler(tornado.web.RequestHandler):
    """Serve a native HTTP audio mountpoint to listeners."""

    _LISTENER_TTL_SECONDS = 25
    _XREAD_BLOCK_MS = 5000
    _XREAD_COUNT = 64

    @property
    def _config(self) -> SonghiveConfig:
        """Return the application configuration from Tornado settings."""
        return self.application.settings["config"]

    @property
    def _redis(self):
        """Return the shared Redis client from Tornado settings, if available."""
        return self.application.settings.get("redis")

    def set_default_headers(self) -> None:
        # Match StreamHandler: the audio endpoints are embeddable cross-origin.
        self.set_header("Access-Control-Allow-Origin", "*")
        self.add_header("Vary", "Origin")

    async def options(self, *_, **__) -> None:
        """Answer CORS preflights."""
        self.set_status(204)
        self.set_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        requested = self.request.headers.get("Access-Control-Request-Headers")
        self.set_header("Access-Control-Allow-Headers", requested if requested else "Authorization")
        self.set_header("Access-Control-Max-Age", "86400")
        await self.finish()

    async def head(self, mount: str) -> None:
        """Return stream headers without joining the listener set."""
        await self._handle(mount, send_body=False)

    async def get(self, mount: str) -> None:
        """Stream the mount to this listener until disconnect or stream end."""
        await self._handle(mount, send_body=True)

    def _token_ok(self, cfg: dict) -> bool:
        """Check the optional per-mount listen token."""
        expected = str(cfg.get("listen_token") or "")
        if not expected:
            return True
        supplied = self.get_argument("token", "")
        auth = self.request.headers.get("Authorization", "")
        if not supplied and auth.startswith("Bearer "):
            supplied = auth[7:]
        return hmac.compare_digest(supplied, expected)

    def _set_stream_headers(self, meta: dict, cfg: dict) -> None:
        """Set audio content and ICY-style headers on the response."""
        self.set_status(200)
        self.set_header("Content-Type", meta.get("content_type") or "audio/mpeg")
        self.set_header("Cache-Control", "no-cache, no-store")
        self.set_header("Pragma", "no-cache")
        self.set_header("Accept-Ranges", "none")
        # Proxies (notably nginx proxy_buffering) must not decouple the
        # listener from this response: buffering upstream lets a slow client
        # accumulate latency the lag cap cannot see.
        self.set_header("X-Accel-Buffering", "no")
        for cfg_key, header in (("name", "icy-name"), ("description", "icy-description"), ("genre", "icy-genre")):
            value = meta.get(cfg_key) or cfg.get(cfg_key)
            if value:
                self.set_header(header, str(value))
        bitrate = str(meta.get("bitrate") or cfg.get("bitrate") or "")
        digits = "".join(c for c in bitrate if c.isdigit())
        if digits:
            self.set_header("icy-br", digits)

    async def _now_ms(self, redis) -> int:
        """Current time in ms, preferring the Redis clock that stamps entry IDs."""
        try:
            sec, usec = await redis.time()
            return int(sec) * 1000 + int(usec) // 1000
        except Exception:
            return int(time.time() * 1000)

    @staticmethod
    def _entry_age_ms(entry_id: str, now_ms: int) -> int:
        """Age of a stream entry; XADD auto-IDs are ``<ms>-<seq>`` timestamps."""
        try:
            return now_ms - int(str(entry_id).split("-", 1)[0])
        except (TypeError, ValueError):
            return 0

    async def _listener_count(self, mount: str) -> int:
        """Count currently-registered listeners for the mount."""
        redis = self._redis
        count = 0
        assert redis  # for mypy
        async for _ in redis.scan_iter(match=stream_listener_pattern(mount), count=100):
            count += 1
        return count

    async def _handle(self, mount: str, *, send_body: bool) -> None:
        redis = self._redis
        if redis is None:
            self.set_status(503)
            self.write({"error": "streaming unavailable"})
            await self.finish()
            return

        slug = normalize_mount(mount)
        async with get_session() as session:
            resolved = await find_http_stream_output(session, slug)
        if resolved is None:
            self.set_status(404)
            self.write({"error": "unknown mount"})
            await self.finish()
            return
        _, cfg = resolved

        if not self._token_ok(cfg):
            self.set_status(403)
            self.write({"error": "access denied"})
            await self.finish()
            return

        meta_key = stream_meta_key(slug)
        meta_raw = await redis.get(meta_key)
        if meta_raw is None:
            self.set_status(404)
            self.write({"error": "stream is offline"})
            await self.finish()
            return
        try:
            meta = json.loads(meta_raw)
        except (TypeError, ValueError):
            meta = {}

        max_listeners = self._config.streams.http_stream_max_listeners
        if send_body and max_listeners and await self._listener_count(slug) >= max_listeners:
            self.set_status(429)
            self.write({"error": "too many listeners"})
            await self.finish()
            return

        self._set_stream_headers(meta, cfg)
        if not send_body:
            await self.finish()
            return

        listener_id = secrets.token_urlsafe(8)
        listener_key = stream_listener_key(slug, listener_id)
        stream_key = stream_data_key(slug)
        await redis.set(listener_key, "1", ex=self._LISTENER_TTL_SECONDS)
        max_lag_ms = int(self._config.streams.http_stream_max_lag_seconds * 1000)

        try:
            # Burst the newest buffered entries first so the client can start
            # decoding immediately. The tail is newest-first; a previous run's
            # ``end`` sentinel may sit inside it, and only data newer than that
            # marker belongs to the live stream.
            burst = self._config.streams.http_stream_burst_entries
            tail = await redis.xrevrange(stream_key, count=burst) if burst else []
            last_id: str = tail[0][0] if tail else "0"
            now_ms = await self._now_ms(redis) if max_lag_ms else 0
            live_tail: list[tuple[str, str]] = []
            ended = False
            for entry_id, fields in tail:
                if fields.get("end"):
                    ended = True
                    break
                data = fields.get("d")
                if data and (not max_lag_ms or self._entry_age_ms(entry_id, now_ms) <= max_lag_ms):
                    live_tail.append((entry_id, data))
            if ended and not live_tail:
                # Everything buffered predates the last end marker: the stream
                # is over even though the meta key has not expired yet.
                return
            for _, data in reversed(live_tail):
                self.write(base64.b64decode(data))
            await self.flush()

            while not self._finished:
                result = await redis.xread(
                    {stream_key: last_id},
                    count=self._XREAD_COUNT,
                    block=self._XREAD_BLOCK_MS,
                )
                # redis-py returns {stream: entries}; fakeredis returns the
                # raw [(stream, entries)] list — accept both.
                entries: list = []
                if isinstance(result, dict):
                    entries = result.get(stream_key) or []
                elif result:
                    entries = [e for name, e in result if name == stream_key]
                    entries = entries[0] if entries else []
                if not entries:
                    # Read timeout: refresh listener presence and drop the
                    # connection if the mount went offline.
                    if not await redis.exists(meta_key):
                        return
                    await redis.set(listener_key, "1", ex=self._LISTENER_TTL_SECONDS)
                    continue
                now_ms = await self._now_ms(redis) if max_lag_ms else 0
                dropped = 0
                for entry_id, fields in entries:
                    last_id = entry_id
                    if fields.get("end"):
                        return
                    data = fields.get("d")
                    if not data:
                        continue
                    if max_lag_ms and self._entry_age_ms(entry_id, now_ms) > max_lag_ms:
                        # The listener fell behind: skip stale audio so it
                        # jumps forward instead of accumulating latency.
                        dropped += 1
                        continue
                    self.write(base64.b64decode(data))
                    await self.flush()
                if dropped:
                    logger.info(
                        "Dropped %d stale chunks for lagging listener on mount %s",
                        dropped,
                        slug,
                    )
        except tornado.iostream.StreamClosedError:
            pass
        except Exception:
            logger.exception("Stream mount %s listener failed", slug)
        finally:
            try:
                await redis.delete(listener_key)
            except Exception:
                pass
            if not self._finished:
                await self.finish()
