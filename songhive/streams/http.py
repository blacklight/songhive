"""
Native HTTP audio output provider and driver.

Provides Icecast-style mountpoints served directly by Songhive — no external
Icecast server required. The worker-side driver runs the same persistent
encoder/per-track-decoder pipeline as the Icecast driver, but the encoder
writes to ``pipe:1`` and a publish task forwards encoded chunks into a Redis
stream (``songhive:stream:data:{mount}``). The Tornado listener endpoint
(``GET /streams/{mount}``, ``songhive/streaming/mount.py``) tails that Redis
stream and fans out to any number of listeners.

Redis keys per mount:

- ``songhive:stream:data:{mount}`` — capped stream of base64-encoded audio
  chunks (``{"d": ...}`` entries), ``{"m": ...}`` now-playing metadata
  entries appended on track changes, and an ``{"end": "1"}`` sentinel
  appended on a graceful stop so listeners disconnect promptly.
- ``songhive:stream:meta:{mount}`` — JSON blob (content type, bitrate, ICY
  fields, current track) with a short TTL, refreshed on a timer for the
  driver's lifetime so dead air during track transitions cannot expire it;
  its absence means the mount is offline and listeners get a 404.
- ``songhive:stream:listener:{mount}:{id}`` — per-listener TTL keys written
  by the web process so the driver can report ``listener_count`` for idle
  shutdown; listeners refresh them on a timer while connected.

Track changes, starts and stops also emit a ``stream_update`` event on the
WebSocket fan-out channel (``songhive:ws-events``, see ``ws.events``) so the
``/streams`` directory page updates live. Public mounts broadcast on the
``streams`` topic; ``listen_token`` mounts are private and the event is
delivered only to the owner's connections.
"""

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, ClassVar, Optional

from .driver import OutputDriver
from .icecast import _FORMAT_SPECS, IcecastDriver, IcecastOutput
from .registry import register_output
from .types import OutputCapabilities, TrackMeta

logger = logging.getLogger(__name__)

_MOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_CHUNK_BYTES = 16 * 1024
_META_TTL_SECONDS = 15.0
_META_REFRESH_INTERVAL = 5.0
_LISTENER_CACHE_TTL = 5.0
_DEFAULT_MAX_ENTRIES = 512


def normalize_mount(mount: Any) -> str:
    """Normalize a user-supplied mount to a bare slug (no slashes)."""
    return str(mount or "").strip().strip("/")


def stream_data_key(mount: str) -> str:
    """Redis stream carrying the encoded audio chunks for a mount."""
    return f"songhive:stream:data:{mount}"


def stream_meta_key(mount: str) -> str:
    """Redis key holding the JSON mount metadata while the source is live."""
    return f"songhive:stream:meta:{mount}"


def stream_listener_key(mount: str, listener_id: str) -> str:
    """Per-listener presence key used to count active listeners."""
    return f"songhive:stream:listener:{mount}:{listener_id}"


def stream_listener_pattern(mount: str) -> str:
    """Match pattern covering all listener keys of a mount."""
    return f"songhive:stream:listener:{mount}:*"


class HttpStreamOutput(IcecastOutput):
    """Built-in HTTP streaming provider (Songhive-managed mountpoints)."""

    provider_type = "http"
    label = "HTTP stream (built-in)"

    FIELDS: ClassVar[list[dict[str, Any]]] = [
        {
            "name": "mount",
            "type": "text",
            "required": True,
            "label": "Mount point",
            "help": "Slug served at /streams/<mount>",
        },
        {
            "name": "format",
            "type": "select",
            "required": True,
            "label": "Format",
            "options": [
                {"value": "ogg", "label": "Ogg Vorbis"},
                {"value": "mp3", "label": "MP3"},
                {"value": "opus", "label": "Opus"},
            ],
        },
        {"name": "bitrate", "type": "text", "required": True, "label": "Bitrate"},
        {"name": "sample_rate", "type": "number", "required": True, "label": "Sample rate", "default": 44100},
        {"name": "name", "type": "text", "required": False, "label": "Stream name"},
        {"name": "description", "type": "text", "required": False, "label": "Description"},
        {"name": "genre", "type": "text", "required": False, "label": "Genre"},
        {
            "name": "listen_token",
            "type": "password",
            "required": False,
            "label": "Listen token",
            "help": "Optional token listeners must supply as ?token= to connect",
        },
    ]

    _REDACTED_KEYS: ClassVar[set[str]] = {"listen_token"}

    async def validate_config(self, config: dict) -> OutputCapabilities:
        """Validate a native HTTP output config and return capabilities."""
        for key in ("mount", "format", "bitrate", "sample_rate"):
            if not config.get(key):
                raise ValueError(f"Missing required config field: {key}")

        if config["format"] not in _FORMAT_SPECS:
            raise ValueError(f"Unsupported format: {config['format']}")

        mount = normalize_mount(config["mount"])
        if not _MOUNT_RE.match(mount):
            raise ValueError(
                "mount must be 1-64 characters of letters, digits, '.', '_' or '-' and start with a letter or digit"
            )
        config["mount"] = mount

        if not re.match(r"^[1-9][0-9]*$", str(config["sample_rate"])):
            raise ValueError("sample_rate must be a positive integer")

        self._capabilities = OutputCapabilities(
            metadata_updates=True,
            pause_supported=True,
            seek_supported=True,
            multi_listener=True,
            user_configurable=True,
        )
        return self._capabilities

    def create_driver(self, config: dict) -> OutputDriver:
        """Return a driver for this native HTTP output."""
        return HttpStreamDriver(config)


class HttpStreamDriver(IcecastDriver):
    """Persistent ffmpeg encoder whose output is published to a Redis stream.

    Inherits the decoder/silence/pause machinery from ``IcecastDriver``; only
    the encoder sink differs: encoded chunks go to
    ``songhive:stream:data:{mount}`` instead of an Icecast mount. The web
    process serves listeners by tailing that stream.
    """

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._redis = config.get("_redis")
        self._mount = normalize_mount(config.get("mount"))
        self._max_entries = int(config.get("_stream_max_entries") or _DEFAULT_MAX_ENTRIES)
        self._publish_task: Optional[asyncio.Task] = None
        self._meta_task: Optional[asyncio.Task] = None

    def _encoder_argv(self) -> list[str]:
        """Encoder writing the mount's encoded format to stdout."""
        cfg = self.config
        fmt = self._format_spec
        return [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(self._sample_rate),
            "-ac",
            "2",
            "-i",
            "pipe:0",
            "-c:a",
            fmt["codec"],
            "-b:a",
            str(cfg["bitrate"]),
            "-f",
            fmt["ffmpeg_fmt"],
            "pipe:1",
        ]

    async def _start_encoder(self) -> None:
        """Start the encoder plus the Redis publish pump."""
        old_publish_task = self._publish_task
        if old_publish_task is not None:
            old_publish_task.cancel()
            try:
                await old_publish_task
            except asyncio.CancelledError:
                pass
            self._publish_task = None
            # A fresh encoder writes a brand-new container stream into the
            # same Redis stream, and listeners cannot splice it into the old
            # one (a second Ogg chain breaks most players). Signal them to
            # reconnect so they pick up a clean stream from the burst.
            if self._redis is not None:
                try:
                    await self._redis.xadd(stream_data_key(self._mount), {"end": "1"})
                except Exception:
                    logger.warning("Failed to signal encoder restart for mount %s", self._mount)

        argv = self._encoder_argv()
        logger.info("Starting encoder: %s", argv)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._encoder = proc
        self._encoder_watch_task = asyncio.create_task(self._watch_encoder(proc))
        self._publish_task = asyncio.create_task(self._run_publish(proc))
        await self._publish_meta()

    async def _publish_meta(self) -> None:
        """Refresh the mount metadata key so listeners see the stream as live."""
        if self._redis is None:
            return
        cfg = self.config
        track = self._current_metadata
        meta = {
            "mount": self._mount,
            "content_type": self._format_spec["content_type"],
            "bitrate": cfg.get("bitrate"),
            "name": cfg.get("name") or "",
            "description": cfg.get("description") or "",
            "genre": cfg.get("genre") or "",
            "song": track.song,
            "track_id": track.track_id,
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
        }
        try:
            await self._redis.set(stream_meta_key(self._mount), json.dumps(meta), ex=int(_META_TTL_SECONDS))
        except Exception:
            logger.warning("Failed to refresh stream meta for mount %s", self._mount)

    async def _meta_refresh_loop(self) -> None:
        """Keep the liveness key fresh for the driver's whole lifetime.

        Track transitions, slow source resolution and decoder gaps all
        starve the publish pump without meaning the stream is offline —
        if the TTL were only renewed while chunks flow, any quiet spell
        would expire the key and drop every listener.
        """
        while True:
            await asyncio.sleep(_META_REFRESH_INTERVAL)
            await self._publish_meta()

    def _now_playing_payload(self) -> Optional[dict]:
        """Return the REST-shaped now-playing block for the current metadata."""
        track = self._current_metadata
        if not track.song:
            return None
        return {
            "track_id": track.track_id or None,
            "title": track.title or None,
            "artist": track.artist or None,
            "album": track.album or None,
        }

    async def _publish_ws_update(self, *, online: bool) -> None:
        """Fan out a ``stream_update`` event so the streams page stays live.

        The stream worker process holds no WebSocket connections, so the
        event goes through ``EventWebSocket``'s Redis pub/sub fan-out — the
        same channel Celery tasks publish on — and the Tornado process
        delivers it to connected clients. Public mounts broadcast on the
        ``streams`` topic; ``listen_token`` mounts are private, so their
        events only reach the owner's connections (the worker injects the
        owner's id into the driver config as ``_owner_user_id``).
        """
        data = {
            "mount": self._mount,
            "online": online,
            "now_playing": self._now_playing_payload() if online else None,
        }
        try:
            from ..ws.events import EventWebSocket

            if self.config.get("listen_token"):
                owner_id = str(self.config.get("_owner_user_id") or "")
                if not owner_id:
                    # A private mount with no known owner must not leak its
                    # metadata into the public broadcast.
                    return
                await asyncio.to_thread(EventWebSocket.send_to_user, owner_id, "stream_update", data)
            else:
                await asyncio.to_thread(
                    EventWebSocket.broadcast,
                    "stream_update",
                    data,
                    topic="streams",
                )
        except Exception:
            logger.warning("Failed to publish stream_update event for mount %s", self._mount)

    async def _run_publish(self, proc: asyncio.subprocess.Process) -> None:
        """Copy encoder stdout chunks into the mount's Redis stream."""
        stdout = proc.stdout
        if stdout is None or self._redis is None:
            return
        key = stream_data_key(self._mount)
        try:
            while True:
                chunk = await stdout.read(_CHUNK_BYTES)
                if not chunk:
                    break
                if self._encoder is not proc:
                    break
                try:
                    await self._redis.xadd(
                        key,
                        {"d": base64.b64encode(chunk).decode("ascii")},
                        maxlen=self._max_entries,
                        approximate=True,
                    )
                except Exception:
                    logger.warning("Failed to publish stream chunk for mount %s", self._mount)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Publish pump failed for mount %s", self._mount)

    async def start(self) -> None:
        """Start the encoder/pipeline; silence feeds the mount until a source is set."""
        if self._redis is None:
            raise RuntimeError("HttpStreamDriver requires a '_redis' client in the driver config")
        await super().start()
        if self._meta_task is None:
            self._meta_task = asyncio.create_task(self._meta_refresh_loop())
        await self._publish_meta()
        await self._publish_ws_update(online=True)

    async def stop(self) -> None:
        """Signal listeners to disconnect and stop the pipeline."""
        self._shutting_down = True
        if self._meta_task is not None:
            self._meta_task.cancel()
            try:
                await self._meta_task
            except asyncio.CancelledError:
                pass
            self._meta_task = None
        if self._redis is not None:
            try:
                await self._redis.xadd(stream_data_key(self._mount), {"end": "1"})
                await self._redis.delete(stream_meta_key(self._mount))
            except Exception:
                logger.warning("Failed to publish stream end for mount %s", self._mount)
            await self._publish_ws_update(online=False)
        if self._publish_task is not None:
            self._publish_task.cancel()
            try:
                await self._publish_task
            except asyncio.CancelledError:
                pass
            self._publish_task = None
        await super().stop()

    async def update_metadata(self, metadata: TrackMeta) -> None:
        """Publish now-playing metadata into the mount's stream and meta blob.

        An ``{"m": ...}`` entry appended to the Redis stream reaches every
        listener in audio order; ``streaming/mount.py`` turns it into an ICY
        ``StreamTitle`` block at the next metaint boundary for clients that
        sent ``Icy-MetaData: 1``. The meta blob carries the same fields so a
        newly-connected listener seeds its title before the first ``m``
        entry arrives.
        """
        self._current_metadata = metadata
        song = metadata.song
        if song == self._pushed_song:
            return
        if self._redis is not None:
            try:
                await self._redis.xadd(
                    stream_data_key(self._mount),
                    {
                        "m": json.dumps(
                            {
                                "song": song,
                                "track_id": metadata.track_id,
                                "title": metadata.title,
                                "artist": metadata.artist,
                                "album": metadata.album,
                            }
                        )
                    },
                    maxlen=self._max_entries,
                    approximate=True,
                )
            except Exception:
                logger.warning("Failed to publish metadata entry for mount %s", self._mount)
                return
            await self._publish_meta()
            await self._publish_ws_update(online=True)
        self._pushed_song = song

    async def listener_count(self) -> int:
        """Count active listeners via their TTL presence keys."""
        if self._redis is None:
            return 0
        now = time.monotonic()
        if self._listener_count_cache is not None and now - self._listener_count_at < _LISTENER_CACHE_TTL:
            return self._listener_count_cache
        count = 0
        try:
            async for _ in self._redis.scan_iter(match=stream_listener_pattern(self._mount), count=100):
                count += 1
        except Exception:
            logger.warning("Failed to scan listener keys for mount %s", self._mount)
            return self._listener_count_cache if self._listener_count_cache is not None else 0
        self._listener_count_cache = count
        self._listener_count_at = now
        return count


register_output("http", HttpStreamOutput)
