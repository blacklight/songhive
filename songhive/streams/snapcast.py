"""
Snapcast audio output provider and driver.

Casts playback to a Snapcast server (https://github.com/badaix/snapcast) so it
reaches every connected Snapclient in sync. The driver reuses the
IcecastDriver decoder/silence machinery: per-track decoders and the realtime
pause generator emit s16le stereo PCM on stdout, which is fed into a
long-lived "encoder" process. Here that process is an ffmpeg passthrough that
copies the raw PCM to the snapserver sink unchanged — no codec step.

Two sink modes, mirroring snapserver's own stream sources:

- ``fifo`` (default): writes to the named pipe read by a snapserver
  ``pipe:///<fifo_path>?name=...`` source. The FIFO is created with mkfifo
  when missing; an existing non-FIFO path is rejected so a typo can never
  clobber a regular file. ffmpeg's blocking ``open()`` simply stalls the
  pipeline until snapserver opens the read end, and an EOF/error restarts the
  encoder via the inherited watcher.
- ``tcp``: pushes PCM to a snapserver ``tcp://`` listening source
  (``stream = tcp://0.0.0.0:<port>?name=...`` in snapserver.conf), so the
  snapserver can live on a different host. A refused connection exits the
  encoder and the watcher retries until snapserver accepts it.

Snapclient presence is reported through snapserver's JSON-RPC control API
(``Server.GetStatus`` on TCP port 1705 by default — note the HTTP/snapweb
port 1780 does not speak the raw newline-delimited protocol): a connected,
unmuted client counts as a listener, optionally restricted to the groups
playing ``stream_name``. That keeps the worker's idle shutdown aligned with
real listeners the same way the Icecast status endpoint does.
"""

import asyncio
import json
import logging
import os
import stat
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar, Optional

from .base import AudioOutput
from .driver import OutputDriver
from .icecast import _INT_RE, IcecastDriver
from .registry import register_output
from .types import OutputCapabilities, TrackMeta

logger = logging.getLogger(__name__)

_MODES = ("fifo", "tcp")
_DEFAULT_CONTROL_PORT = 1705
_CONTROL_TIMEOUT_SECONDS = 5.0


class SnapcastOutput(AudioOutput):
    """Snapcast casting provider."""

    provider_type = "snapcast"
    user_configurable = True
    label = "Snapcast"

    # fifo_path and host/port are only required in their respective modes, so
    # they are marked optional here and enforced mode-dependently by
    # validate_config (the frontend form cannot express conditional fields).
    FIELDS: ClassVar[list[dict[str, Any]]] = [
        {
            "name": "mode",
            "type": "select",
            "required": True,
            "label": "Mode",
            "default": "fifo",
            "options": [
                {"value": "fifo", "label": "Named pipe (FIFO)"},
                {"value": "tcp", "label": "TCP source"},
            ],
            "help": (
                "fifo writes to the named pipe of a snapserver pipe:// source; "
                "tcp pushes to a snapserver tcp:// listening source. In both "
                "cases the source must be declared in snapserver.conf — "
                "streams cannot be registered dynamically."
            ),
        },
        {
            "name": "fifo_path",
            "type": "text",
            "required": False,
            "label": "FIFO path",
            "help": "fifo mode: path of the named pipe snapserver reads (e.g. /tmp/snapfifo); created if missing.",
        },
        {
            "name": "host",
            "type": "text",
            "required": False,
            "label": "Host",
            "help": "tcp mode: snapserver host to connect to.",
        },
        {
            "name": "port",
            "type": "number",
            "required": False,
            "label": "Port",
            "help": (
                "tcp mode: port of a snapserver tcp:// stream source declared in "
                "snapserver.conf (e.g. tcp://0.0.0.0:4953?name=...). NOT the "
                "snapclient port 1704."
            ),
        },
        {
            "name": "sample_rate",
            "type": "number",
            "required": True,
            "label": "Sample rate",
            "default": 48000,
            "help": "Must match the snapserver source's sampleformat (rate:16:2).",
        },
        {
            "name": "control_host",
            "type": "text",
            "required": False,
            "label": "Control host",
            "help": (
                "Snapserver JSON-RPC host used for listener counts; defaults to "
                "the stream host (tcp) or 127.0.0.1 (fifo)."
            ),
        },
        {
            "name": "control_port",
            "type": "number",
            "required": False,
            "label": "Control port",
            "default": _DEFAULT_CONTROL_PORT,
            "help": ("Snapserver JSON-RPC control port (raw TCP, default 1705 — not " "the HTTP/snapweb port 1780)."),
        },
        {
            "name": "stream_name",
            "type": "text",
            "required": False,
            "label": "Stream name",
            "help": (
                "Optional snapserver stream name (the name= of a source in "
                "snapserver.conf); only clients in groups playing this stream "
                "count as listeners."
            ),
        },
    ]

    @staticmethod
    def _check_port(config: dict, key: str) -> None:
        """Validate ``config[key]`` as a TCP port number."""
        value = config.get(key)
        if not _INT_RE.match(str(value)):
            raise ValueError(f"{key} must be a positive integer")
        port = int(str(value))
        if port > 65535:
            raise ValueError(f"{key} must be <= 65535")
        config[key] = port

    async def validate_config(self, config: dict) -> OutputCapabilities:
        """Validate a Snapcast config and return capabilities."""
        mode = str(config.get("mode") or "fifo").strip()
        if mode not in _MODES:
            raise ValueError(f"mode must be one of: {', '.join(_MODES)}")
        config["mode"] = mode

        if not config.get("sample_rate"):
            raise ValueError("Missing required config field: sample_rate")
        if not _INT_RE.match(str(config["sample_rate"])):
            raise ValueError("sample_rate must be a positive integer")
        config["sample_rate"] = int(str(config["sample_rate"]))

        if mode == "fifo":
            fifo_path = str(config.get("fifo_path") or "").strip()
            if not fifo_path:
                raise ValueError("Missing required config field: fifo_path")
            if not os.path.isabs(fifo_path):
                raise ValueError("fifo_path must be an absolute path")
            if os.path.lexists(fifo_path) and not stat.S_ISFIFO(os.stat(fifo_path).st_mode):
                raise ValueError(f"fifo_path {fifo_path} exists and is not a FIFO")
            config["fifo_path"] = fifo_path
        else:
            host = str(config.get("host") or "").strip()
            if not host:
                raise ValueError("Missing required config field: host")
            config["host"] = host
            if not config.get("port"):
                raise ValueError("Missing required config field: port")
            self._check_port(config, "port")

        if config.get("control_port") in (None, ""):
            config["control_port"] = _DEFAULT_CONTROL_PORT
        else:
            self._check_port(config, "control_port")

        for key in ("control_host", "stream_name"):
            if config.get(key) is not None:
                config[key] = str(config[key]).strip()

        self._capabilities = OutputCapabilities(
            metadata_updates=False,
            pause_supported=True,
            seek_supported=True,
            multi_listener=True,
            user_configurable=True,
        )
        return self._capabilities

    def create_driver(self, config: dict) -> OutputDriver:
        """Return a driver for this Snapcast output."""
        return SnapcastDriver(config)


class SnapcastDriver(IcecastDriver):
    """PCM passthrough driver feeding a snapserver pipe or TCP source.

    Inherits the decoder/silence/pause machinery from ``IcecastDriver``; the
    persistent "encoder" just copies raw s16le stereo PCM to the configured
    snapserver sink instead of encoding and pushing an Icecast mount.
    """

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._mode = str(config.get("mode") or "fifo")
        self._fifo_path = Path(str(config.get("fifo_path") or ""))
        self._stream_name = str(config.get("stream_name") or "").strip()

        control_host = str(config.get("control_host") or "").strip()
        if not control_host:
            control_host = str(config.get("host") or "") if self._mode == "tcp" else "127.0.0.1"
        self._control_host = control_host
        self._control_port = int(config.get("control_port") or _DEFAULT_CONTROL_PORT)

    def _encoder_argv(self) -> list[str]:
        """Passthrough ffmpeg copying PCM stdin to the snapserver sink."""
        argv = [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if self._mode == "fifo":
            # The sink is a filesystem path; -y skips the interactive
            # overwrite prompt ffmpeg emits for existing paths.
            argv.append("-y")
        argv.extend(
            [
                "-f",
                "s16le",
                "-ar",
                str(self._sample_rate),
                "-ac",
                "2",
                "-i",
                "pipe:0",
                "-c:a",
                "pcm_s16le",
                "-f",
                "s16le",
            ]
        )
        if self._mode == "tcp":
            argv.append(f"tcp://{self.config['host']}:{self.config['port']}")
        else:
            argv.append(str(self._fifo_path))
        return argv

    def _ensure_fifo(self) -> None:
        """Create the sink FIFO when missing; refuse to clobber other files."""
        try:
            file_mode = self._fifo_path.stat().st_mode
        except FileNotFoundError:
            os.mkfifo(self._fifo_path, 0o666)
            logger.info("Created snapcast FIFO %s", self._fifo_path)
            return
        if not stat.S_ISFIFO(file_mode):
            raise RuntimeError(f"{self._fifo_path} exists and is not a FIFO")

    async def _start_encoder(self) -> None:
        """Prepare the FIFO sink (fifo mode), then start the passthrough encoder."""
        if self._mode == "fifo":
            self._ensure_fifo()
        await super()._start_encoder()

    async def update_metadata(self, metadata: TrackMeta) -> None:
        """v1: no-op; the raw PCM sink carries no in-band metadata."""
        logger.debug("update_metadata no-op for snapcast v1: %s", metadata)

    async def listener_count(self) -> int:
        """Count connected, unmuted Snapclients via snapserver's JSON-RPC API."""
        now = time.monotonic()
        if self._listener_count_cache is not None and now - self._listener_count_at < self._listener_count_ttl:
            return self._listener_count_cache

        try:
            result = await asyncio.wait_for(
                self._rpc_call("Server.GetStatus"),
                timeout=_CONTROL_TIMEOUT_SECONDS,
            )
            count = self._count_clients(result)
        except Exception as exc:
            logger.warning(
                "Snapserver control endpoint %s:%s unreachable: %s",
                self._control_host,
                self._control_port,
                exc,
            )
            return self._listener_count_cache if self._listener_count_cache is not None else 0

        self._listener_count_cache = count
        self._listener_count_at = now
        return count

    async def _rpc_call(self, method: str) -> dict:
        """Send a newline-delimited JSON-RPC request to snapserver's control port."""
        reader, writer = await asyncio.open_connection(self._control_host, self._control_port)
        try:
            request_id = "songhive-listener-count"
            writer.write(json.dumps({"id": request_id, "jsonrpc": "2.0", "method": method}).encode("utf-8") + b"\r\n")
            await writer.drain()
            # Snapserver may interleave notifications on the control socket;
            # keep reading until the reply matching our request id arrives.
            reply: dict = {}
            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("snapserver closed the control connection")
                try:
                    reply = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if reply.get("id") == request_id:
                    break
            if reply.get("error"):
                raise RuntimeError(f"snapserver RPC error: {reply['error']}")
            return reply.get("result") or {}
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    def _count_clients(self, result: dict) -> int:
        """Count connected, unmuted clients in the groups playing our stream."""
        server = (result or {}).get("server") or {}
        stream_ids: Optional[set] = None
        if self._stream_name:
            matched = {
                stream.get("id")
                for stream in server.get("streams") or []
                if self._stream_name
                in (
                    stream.get("id"),
                    ((stream.get("uri") or {}).get("query") or {}).get("name"),
                )
            }
            if matched:
                stream_ids = matched
            else:
                # Fail open: a typo'd stream_name must not report zero
                # listeners and idle-stop the output mid-playback.
                logger.warning(
                    "Stream %r not found on snapserver; counting all clients",
                    self._stream_name,
                )

        count = 0
        for group in server.get("groups") or []:
            if group.get("muted"):
                continue
            if stream_ids is not None and group.get("stream_id") not in stream_ids:
                continue
            for client in group.get("clients") or []:
                if not client.get("connected"):
                    continue
                if ((client.get("config") or {}).get("volume") or {}).get("muted"):
                    continue
                count += 1
        return count


register_output("snapcast", SnapcastOutput)
