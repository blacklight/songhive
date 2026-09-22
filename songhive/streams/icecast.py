"""
Icecast audio output provider and driver.
"""

import asyncio
import logging
import os
import re
import signal
import time
from pathlib import Path
from typing import Any, ClassVar, Optional

import aiofiles

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
        {"name": "protocol", "type": "select", "required": False, "label": "Protocol", "default": "http"},
        {"name": "format", "type": "select", "required": True, "label": "Format"},
        {"name": "bitrate", "type": "text", "required": True, "label": "Bitrate"},
        {"name": "sample_rate", "type": "number", "required": True, "label": "Sample rate"},
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
        self._decoder_task: Optional[asyncio.Task] = None
        self._current_source: Optional[AudioSource] = None
        self._current_metadata: TrackMeta = TrackMeta(track_id="", title="", artist="")
        self._paused = False
        self._position = 0.0
        self._resume_position = 0.0
        self._source_started_at = 0.0
        self._sample_rate = int(config.get("sample_rate", 44100))
        self._task_count = 0

    @property
    def _format_spec(self) -> dict[str, str]:
        return _FORMAT_SPECS[self.config["format"]]

    def _encoder_argv(self) -> list[str]:
        """Build the persistent encoder ffmpeg command."""
        cfg = self.config
        fmt = self._format_spec
        url = f"icecast://{cfg['username']}:{cfg['password']}@" f"{cfg['host']}:{cfg['port']}{cfg['mount']}"

        argv: list[str] = [
            "ffmpeg",
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
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(max(0.0, position)),
        ]

        if source.kind == "url":
            argv.extend(["-i", source.url or ""])
        else:
            argv.extend(["-i", str(source.path)])

        argv.extend(
            [
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
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
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

            if self._task_count == task_id and self._encoder is not None and not self._paused:
                self._emit({"type": "source_ended"})
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
            proc.send_signal(signal.SIGTERM)
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

    async def start(self) -> None:
        """Start the encoder and begin producing output."""
        self._encoder = await asyncio.create_subprocess_exec(
            *self._encoder_argv(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

    async def stop(self) -> None:
        """Stop the encoder and any decoder."""
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
        self._current_metadata = metadata
        self._current_source = source
        self._position = position
        self._resume_position = position
        self._source_started_at = time.monotonic()
        self._paused = False
        await self._start_decoder_task(source, position)

    async def _start_decoder_task(self, source: AudioSource, position: float) -> None:
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
        self._paused = True
        self._resume_position = self._elapsed_position()
        self._source_started_at = time.monotonic()
        await self._start_decoder_task(AudioSource(kind="path"), 0.0)

    async def resume(self) -> None:
        """Resume by swapping back to the current source."""
        if not self._paused:
            return
        self._paused = False
        self._position = self._resume_position
        self._source_started_at = time.monotonic()
        if self._current_source is not None:
            await self._start_decoder_task(self._current_source, self._resume_position)

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
        """Best-effort listener count; v1 cannot query Icecast, so always 0."""
        return 0


register_output("icecast", IcecastOutput)
