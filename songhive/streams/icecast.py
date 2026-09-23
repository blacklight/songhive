"""
Icecast audio output provider and driver.
"""

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, ClassVar, Optional

import aiofiles
import httpx

from .base import AudioOutput
from .driver import OutputDriver
from .registry import register_output
from .types import AudioSource, OutputCapabilities, OutputHealth, TrackMeta

logger = logging.getLogger(__name__)

_FORMAT_SPECS: dict[str, dict[str, str]] = {
    "ogg": {"codec": "libvorbis", "ffmpeg_fmt": "ogg", "content_type": "audio/ogg"},
    "mp3": {"codec": "libmp3lame", "ffmpeg_fmt": "mp3", "content_type": "audio/mpeg"},
    "opus": {"codec": "libopus", "ffmpeg_fmt": "opus", "content_type": "audio/ogg"},
}

_INT_RE = re.compile(r"^[1-9][0-9]*$")


class IcecastOutput(AudioOutput):
    """Icecast streaming provider."""

    provider_type = "icecast"
    user_configurable = True

    FIELDS: ClassVar[list[dict[str, Any]]] = [
        {"name": "host", "type": "text", "required": True, "label": "Host"},
        {"name": "port", "type": "number", "required": True, "label": "Port"},
        {"name": "mount", "type": "text", "required": True, "label": "Mount point"},
        {"name": "username", "type": "text", "required": False, "label": "Username", "default": "source"},
        {"name": "password", "type": "password", "required": True, "label": "Password"},
        {
            "name": "protocol",
            "type": "select",
            "required": False,
            "label": "Protocol",
            "default": "http",
            "options": [{"value": "http", "label": "HTTP"}, {"value": "https", "label": "HTTPS"}],
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
        {"name": "public", "type": "boolean", "required": False, "label": "Public", "default": False},
    ]

    _REDACTED_KEYS: ClassVar[set[str]] = {"password"}

    async def validate_config(self, config: dict) -> OutputCapabilities:
        """Validate an Icecast config and return capabilities."""
        for key in ("host", "port", "mount", "format", "bitrate", "sample_rate"):
            if not config.get(key):
                raise ValueError(f"Missing required config field: {key}")

        fmt = config["format"]
        if fmt not in _FORMAT_SPECS:
            raise ValueError(f"Unsupported format: {fmt}")

        if config.get("protocol", "http") not in ("http", "https"):
            raise ValueError("protocol must be 'http' or 'https'")

        mount = config["mount"]
        if not mount.startswith("/"):
            mount = "/" + mount
            config["mount"] = mount

        if not _INT_RE.match(str(config["port"])):
            raise ValueError("port must be a positive integer")

        if not _INT_RE.match(str(config["sample_rate"])):
            raise ValueError("sample_rate must be a positive integer")

        if config.get("username", "") == "":
            config["username"] = "source"

        self._capabilities = OutputCapabilities(
            metadata_updates=False,
            pause_supported=True,
            seek_supported=True,
            multi_listener=True,
            user_configurable=True,
        )
        return self._capabilities

    def create_driver(self, config: dict) -> OutputDriver:
        """Return a driver for this Icecast output."""
        return IcecastDriver(config)


class IcecastDriver(OutputDriver):
    """Long-lived ffmpeg -> Icecast driver.

    Keeps one encoder process open for the lifetime of the output.  Per-track
    decoder processes are swapped in and out; a ``lavfi`` silence generator is
    used while paused.  The driver emits ``source_ended`` and ``error`` events
    on its queue.
    """

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._encoder: Optional[asyncio.subprocess.Process] = None
        self._encoder_watch_task: Optional[asyncio.Task] = None
        self._encoder_stderr_task: Optional[asyncio.Task] = None
        self._encoder_stderr_tail = bytearray()
        self._decoder_task: Optional[asyncio.Task] = None
        self._current_source: Optional[AudioSource] = None
        self._current_metadata: TrackMeta = TrackMeta(track_id="", title="", artist="")
        self._paused = False
        self._volume = 1.0
        self._position = 0.0
        self._resume_position = 0.0
        self._source_started_at = 0.0
        self._shutting_down = False
        self._sample_rate = int(config.get("sample_rate", 44100))
        self._task_count = 0
        self._listener_count_cache: Optional[int] = None
        self._listener_count_at = 0.0
        self._listener_count_ttl = 5.0

    @property
    def _format_spec(self) -> dict[str, str]:
        return _FORMAT_SPECS[self.config["format"]]

    @property
    def _ffmpeg(self) -> str:
        return str(self.config.get("ffmpeg_path") or "ffmpeg")

    def _encoder_argv(self) -> list[str]:
        """Build the persistent encoder ffmpeg command."""
        cfg = self.config
        fmt = self._format_spec
        url = f"icecast://{cfg['username']}:{cfg['password']}@" f"{cfg['host']}:{cfg['port']}{cfg['mount']}"

        argv: list[str] = [
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
            "-content_type",
            fmt["content_type"],
            "-f",
            fmt["ffmpeg_fmt"],
        ]

        for ice_key in ("name", "description", "genre"):
            value = cfg.get(ice_key)
            if value:
                argv.extend([f"-ice_{ice_key}", str(value)])

        public = cfg.get("public", False)
        argv.extend(["-ice_public", "1" if public else "0"])

        argv.append(url)
        return argv

    def _decoder_argv(self, source: AudioSource, *, position: float) -> list[str]:
        """Build a per-track decoder ffmpeg command."""
        argv: list[str] = [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-ss",
            str(max(0.0, position)),
        ]

        if source.kind == "url":
            argv.extend(["-i", source.url or ""])
        else:
            argv.extend(["-i", str(source.path)])

        argv.extend(
            [
                "-af",
                f"volume={self._volume}",
                "-f",
                "s16le",
                "-ar",
                str(self._sample_rate),
                "-ac",
                "2",
                "pipe:1",
            ]
        )
        return argv

    def _silence_argv(self) -> list[str]:
        """Build a silence generator command for pause."""
        return [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            # Without -re, anullsrc generates silence as fast as the CPU allows:
            # the encoder floods Icecast with hours of "audio" per minute,
            # Icecast drops listeners that fall behind, and the mount can wedge.
            "-re",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={self._sample_rate}:cl=stereo",
            "-f",
            "s16le",
            "-ar",
            str(self._sample_rate),
            "-ac",
            "2",
            "pipe:1",
        ]

    async def _run_decoder(self, source: AudioSource, position: float, task_id: int) -> None:
        """Run a decoder/silence process and copy its stdout to the encoder stdin."""
        if source.kind == "iterator" and source.iterator is not None:
            source = await self._spill_iterator_to_temp(source)

        argv = self._silence_argv() if self._paused else self._decoder_argv(source, position=position)
        logger.info("Starting decoder task=%s paused=%s argv=%s", task_id, self._paused, argv)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout = proc.stdout
            if stdout is None:
                proc.kill()
                return

            while True:
                chunk = await stdout.read(8192)
                if not chunk:
                    break

                encoder = self._encoder
                if self._task_count != task_id or encoder is None:
                    break
                stdin = encoder.stdin
                if stdin is None:
                    break
                try:
                    stdin.write(chunk)
                    await stdin.drain()
                except (BrokenPipeError, ConnectionResetError, AttributeError):
                    break

            try:
                await proc.wait()
            except ProcessLookupError:
                pass

            stderr = b""
            if proc.stderr is not None:
                try:
                    stderr = await asyncio.wait_for(proc.stderr.read(), timeout=2.0)
                except Exception:
                    pass
            stderr_text = stderr.decode("utf-8", "replace").strip()
            if stderr_text:
                logger.error("Decoder stderr task=%s rc=%s: %s", task_id, proc.returncode, stderr_text)
            else:
                logger.info("Decoder task=%s rc=%s finished (no stderr)", task_id, proc.returncode)

            if (
                self._task_count == task_id
                and self._encoder is not None
                and self._encoder.returncode is None
                and not self._paused
            ):
                self._emit({"type": "source_ended", "generation": task_id})
        except asyncio.CancelledError:
            self._kill_proc(proc)
            raise
        except Exception as exc:
            logger.exception("Decoder error")
            if self._task_count == task_id:
                self._emit({"type": "error", "message": str(exc)})

    def _kill_proc(self, proc: Optional[asyncio.subprocess.Process]) -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return

    async def _spill_iterator_to_temp(self, source: AudioSource) -> AudioSource:
        """Write an iterator stream to a temporary file and return a path source."""
        import tempfile

        fd, name = tempfile.mkstemp(prefix="songhive-stream-", suffix=".tmp")
        os.close(fd)
        path = Path(name)

        async with aiofiles.open(path, "wb") as f:
            if source.iterator is not None:
                async for chunk in source.iterator:
                    if chunk:
                        await f.write(chunk)

        return AudioSource(
            kind="path",
            path=path,
            content_type=source.content_type,
            size=path.stat().st_size if path.exists() else None,
        )

    async def _start_encoder(self) -> None:
        """Start a fresh ffmpeg encoder process and a watcher for it."""
        argv = self._encoder_argv()
        redacted = list(argv)
        redacted[-1] = re.sub(r"^(icecast://)[^@]+@", r"\1source:***@", redacted[-1])
        logger.info("Starting encoder: %s", redacted)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._encoder = proc
        self._encoder_stderr_tail = bytearray()
        self._encoder_stderr_task = asyncio.create_task(self._drain_encoder_stderr(proc))
        self._encoder_watch_task = asyncio.create_task(self._watch_encoder(proc))

    async def _drain_encoder_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """Keep the tail of the encoder's stderr for post-mortem logging."""
        stream = proc.stderr
        if stream is None:
            return
        try:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                self._encoder_stderr_tail += chunk
                del self._encoder_stderr_tail[:-4096]
        except Exception:
            pass

    async def _watch_encoder(self, proc: asyncio.subprocess.Process) -> None:
        """Watch the encoder and restart it if it exits unexpectedly."""
        rc = await proc.wait()
        if self._shutting_down or self._encoder is not proc:
            return
        # Give the stderr drain a moment to flush before reading the tail.
        stderr_task = self._encoder_stderr_task
        if stderr_task is not None and not stderr_task.done():
            try:
                await asyncio.wait_for(stderr_task, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        stderr_tail = bytes(self._encoder_stderr_tail).decode("utf-8", "replace").strip()
        logger.error(
            "Encoder exited unexpectedly rc=%s; restarting%s", rc, f" (stderr: {stderr_tail})" if stderr_tail else ""
        )
        self._encoder = None
        self._encoder_watch_task = None
        try:
            await asyncio.sleep(0.5)
            await self._start_encoder()
        except Exception:
            logger.exception("Failed to restart encoder")
            self._emit({"type": "error", "message": f"encoder exited (rc={rc})"})
            return
        await self._restart_decoder()

    async def _restart_decoder(self) -> None:
        """Restart the decoder against the current source after an encoder restart."""
        if self._current_source is not None and not self._paused:
            await self._start_decoder_task(self._current_source, self._elapsed_position())
        else:
            await self._start_decoder_task(AudioSource(kind="path"), 0.0)

    async def start(self) -> None:
        """Start the encoder and begin producing output."""
        self._shutting_down = False
        await self._start_encoder()
        # Feed silence while waiting for the first track or a pause command so
        # Icecast does not drop the source for inactivity.
        self._paused = True
        self._source_started_at = time.monotonic()
        await self._start_decoder_task(AudioSource(kind="path"), 0.0)

    async def stop(self) -> None:
        """Stop the encoder and any decoder."""
        self._shutting_down = True
        if self._encoder_watch_task is not None:
            self._encoder_watch_task.cancel()
            try:
                await self._encoder_watch_task
            except asyncio.CancelledError:
                pass
            self._encoder_watch_task = None
        if self._encoder_stderr_task is not None:
            self._encoder_stderr_task.cancel()
            try:
                await self._encoder_stderr_task
            except asyncio.CancelledError:
                pass
            self._encoder_stderr_task = None
        if self._decoder_task is not None:
            self._decoder_task.cancel()
            try:
                await self._decoder_task
            except asyncio.CancelledError:
                pass
            self._decoder_task = None

        if self._encoder is not None:
            self._kill_proc(self._encoder)
            try:
                await self._encoder.wait()
            except ProcessLookupError:
                pass
            self._encoder = None

    def _elapsed_position(self) -> float:
        """Estimate the current playback position."""
        if self._paused or not self._source_started_at:
            return self._position
        elapsed = time.monotonic() - self._source_started_at
        duration = self._current_metadata.duration
        if duration is not None:
            return min(self._position + elapsed, duration)
        return self._position + elapsed

    async def set_source(
        self,
        source: AudioSource,
        *,
        position: float,
        metadata: TrackMeta,
    ) -> None:
        """Switch to a new audio source, optionally starting at ``position``."""
        logger.info("set_source position=%s metadata=%s source=%s", position, metadata, source)
        self._current_metadata = metadata
        self._current_source = source
        self._position = position
        self._resume_position = position
        self._source_started_at = time.monotonic()
        self._paused = False
        await self._start_decoder_task(source, position)

    async def _start_decoder_task(self, source: AudioSource, position: float) -> None:
        logger.info("_start_decoder_task source=%s position=%s", source, position)
        if self._decoder_task is not None:
            self._decoder_task.cancel()
            try:
                await self._decoder_task
            except asyncio.CancelledError:
                pass

        self._task_count += 1
        task_id = self._task_count
        self._decoder_task = asyncio.create_task(self._run_decoder(source, position, task_id))

    async def pause(self) -> None:
        """Pause by swapping to a silence generator."""
        if self._paused:
            return
        logger.info("pause")
        self._paused = True
        self._resume_position = self._elapsed_position()
        self._position = self._resume_position
        self._source_started_at = time.monotonic()
        await self._start_decoder_task(AudioSource(kind="path"), 0.0)

    async def resume(self) -> None:
        """Resume by swapping back to the current source."""
        if not self._paused:
            return
        logger.info("resume")
        self._paused = False
        self._position = self._resume_position
        self._source_started_at = time.monotonic()
        if self._current_source is not None:
            await self._start_decoder_task(self._current_source, self._resume_position)

    async def seek(self, seconds: float) -> None:
        """Reposition the current source; updates the resume point while paused."""
        if self._current_source is None:
            return
        if self._paused:
            self._resume_position = seconds
            self._position = seconds
            return
        await self.set_source(self._current_source, position=seconds, metadata=self._current_metadata)

    async def set_volume(self, volume: float) -> None:
        """Set the decoder gain; restarts the decoder at the live position."""
        self._volume = min(max(float(volume), 0.0), 1.0)
        if self._paused or self._current_source is None:
            return
        await self.set_source(
            self._current_source,
            position=self._elapsed_position(),
            metadata=self._current_metadata,
        )

    @property
    def is_paused(self) -> bool:
        """Whether the silence generator is currently feeding the encoder."""
        return self._paused

    @property
    def generation(self) -> Optional[int]:
        """Decoder task generation, used to drop stale ``source_ended`` events."""
        return self._task_count

    async def update_metadata(self, metadata: TrackMeta) -> None:
        """v1: no-op because the ffmpeg icecast muxer cannot update ICY in flight."""
        logger.debug("update_metadata no-op for icecast v1: %s", metadata)

    async def health(self) -> OutputHealth:
        """Return whether the encoder appears alive."""
        if self._encoder is None:
            return OutputHealth(ok=False, message="encoder not started")
        if self._encoder.returncode is not None:
            return OutputHealth(
                ok=False,
                message="encoder exited",
                details={"returncode": self._encoder.returncode},
            )
        return OutputHealth(ok=True, message="encoder running")

    async def listener_count(self) -> int:
        """Best-effort listener count via the Icecast status JSON endpoint."""
        now = time.monotonic()
        if self._listener_count_cache is not None and now - self._listener_count_at < self._listener_count_ttl:
            return self._listener_count_cache

        cfg = self.config
        status_url = f"http://{cfg['host']}:{cfg['port']}/status-json.xsl"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(status_url)
                resp.raise_for_status()
                data = resp.json()
            mount = data.get("icestats", {}).get("source")
            count = 0
            if isinstance(mount, list):
                for src in mount:
                    if src.get("listenurl", "").endswith(cfg["mount"]):
                        count = int(src.get("listeners", 0))
                        break
            elif isinstance(mount, dict) and mount.get("listenurl", "").endswith(cfg["mount"]):
                count = int(mount.get("listeners", 0))
            self._listener_count_cache = count
            self._listener_count_at = now
            return count
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("Icecast status endpoint unreachable at %s: %s", status_url, exc)
        except Exception:
            logger.exception("Failed to query Icecast listener count")
        return self._listener_count_cache if self._listener_count_cache is not None else 0


register_output("icecast", IcecastOutput)
