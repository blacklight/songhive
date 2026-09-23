"""Tests for the Snapcast output provider and driver."""

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from songhive.streams.registry import get_output
from songhive.streams.snapcast import SnapcastDriver, SnapcastOutput
from songhive.streams.types import AudioSource, TrackMeta


def _fifo_config(tmp_path: Path) -> dict:
    return {
        "mode": "fifo",
        "fifo_path": str(tmp_path / "snapfifo"),
        "sample_rate": 48000,
    }


def _tcp_config() -> dict:
    return {
        "mode": "tcp",
        "host": "snapserver.local",
        "port": 4953,
        "sample_rate": 48000,
    }


def test_snapcast_provider_registered():
    """The snapcast provider is registered under its provider type."""
    assert get_output("snapcast") is SnapcastOutput


@pytest.mark.asyncio
async def test_snapcast_validate_config_returns_capabilities(tmp_path):
    provider = SnapcastOutput()
    caps = await provider.validate_config(_fifo_config(tmp_path))
    assert caps.pause_supported is True
    assert caps.seek_supported is True
    assert caps.multi_listener is True
    assert caps.metadata_updates is False
    assert caps.user_configurable is True


@pytest.mark.asyncio
async def test_snapcast_mode_defaults_to_fifo(tmp_path):
    provider = SnapcastOutput()
    config = _fifo_config(tmp_path)
    del config["mode"]
    await provider.validate_config(config)
    assert config["mode"] == "fifo"


@pytest.mark.asyncio
async def test_snapcast_invalid_mode(tmp_path):
    provider = SnapcastOutput()
    config = _fifo_config(tmp_path)
    config["mode"] = "udp"
    with pytest.raises(ValueError, match="mode must be one of"):
        await provider.validate_config(config)


@pytest.mark.asyncio
async def test_snapcast_fifo_path_required(tmp_path):
    provider = SnapcastOutput()
    config = _fifo_config(tmp_path)
    del config["fifo_path"]
    with pytest.raises(ValueError, match="fifo_path"):
        await provider.validate_config(config)


@pytest.mark.asyncio
async def test_snapcast_fifo_path_must_be_absolute():
    provider = SnapcastOutput()
    config = {"mode": "fifo", "fifo_path": "relative/snapfifo", "sample_rate": 48000}
    with pytest.raises(ValueError, match="absolute path"):
        await provider.validate_config(config)


@pytest.mark.asyncio
async def test_snapcast_fifo_path_rejects_existing_non_fifo(tmp_path):
    """An existing non-FIFO path is refused so it can never be clobbered."""
    regular = tmp_path / "regular-file"
    regular.write_bytes(b"data")
    provider = SnapcastOutput()
    config = {"mode": "fifo", "fifo_path": str(regular), "sample_rate": 48000}
    with pytest.raises(ValueError, match="not a FIFO"):
        await provider.validate_config(config)


@pytest.mark.asyncio
async def test_snapcast_fifo_path_accepts_existing_fifo(tmp_path):
    fifo = tmp_path / "snapfifo"
    os.mkfifo(fifo)
    provider = SnapcastOutput()
    config = {"mode": "fifo", "fifo_path": str(fifo), "sample_rate": 48000}
    await provider.validate_config(config)
    assert config["fifo_path"] == str(fifo)


@pytest.mark.asyncio
async def test_snapcast_tcp_requires_host_and_port():
    provider = SnapcastOutput()
    config = _tcp_config()
    del config["host"]
    with pytest.raises(ValueError, match="Missing required config field: host"):
        await provider.validate_config(config)

    config = _tcp_config()
    del config["port"]
    with pytest.raises(ValueError, match="Missing required config field: port"):
        await provider.validate_config(config)


@pytest.mark.asyncio
async def test_snapcast_tcp_port_must_be_valid():
    provider = SnapcastOutput()
    for bad in ("abc", 0, 70000):
        config = _tcp_config()
        config["port"] = bad
        with pytest.raises(ValueError):
            await provider.validate_config(config)


@pytest.mark.asyncio
async def test_snapcast_sample_rate_must_be_positive_int(tmp_path):
    provider = SnapcastOutput()
    config = _fifo_config(tmp_path)
    config["sample_rate"] = "fast"
    with pytest.raises(ValueError, match="sample_rate"):
        await provider.validate_config(config)

    config = _fifo_config(tmp_path)
    del config["sample_rate"]
    with pytest.raises(ValueError, match="Missing required config field: sample_rate"):
        await provider.validate_config(config)


@pytest.mark.asyncio
async def test_snapcast_control_port_defaults_and_validates(tmp_path):
    provider = SnapcastOutput()
    config = _fifo_config(tmp_path)
    await provider.validate_config(config)
    assert config["control_port"] == 1705

    config = _fifo_config(tmp_path)
    config["control_port"] = "bogus"
    with pytest.raises(ValueError, match="control_port"):
        await provider.validate_config(config)


@pytest.mark.asyncio
async def test_snapcast_encoder_argv_fifo(tmp_path):
    provider = SnapcastOutput()
    config = _fifo_config(tmp_path)
    await provider.validate_config(config)
    driver = SnapcastDriver(config)
    argv = driver._encoder_argv()
    assert argv[0] == "ffmpeg"
    assert "-y" in argv
    assert argv.index("-y") < argv.index("-i")
    assert "pipe:0" in argv
    assert "pcm_s16le" in argv
    assert "s16le" in argv
    assert "48000" in argv
    assert argv[-1] == str(tmp_path / "snapfifo")
    # No codec/icecast options leak into the passthrough argv.
    assert not any("icecast://" in arg for arg in argv)
    assert not any(arg.startswith("-ice_") for arg in argv)


@pytest.mark.asyncio
async def test_snapcast_encoder_argv_tcp():
    provider = SnapcastOutput()
    config = _tcp_config()
    await provider.validate_config(config)
    driver = SnapcastDriver(config)
    argv = driver._encoder_argv()
    assert argv[-1] == "tcp://snapserver.local:4953"
    assert "-y" not in argv
    assert "pcm_s16le" in argv


@pytest.mark.asyncio
async def test_snapcast_encoder_argv_honours_ffmpeg_path(tmp_path):
    provider = SnapcastOutput()
    config = _fifo_config(tmp_path)
    config["ffmpeg_path"] = "/opt/bin/ffmpeg"
    await provider.validate_config(config)
    driver = SnapcastDriver(config)
    assert driver._encoder_argv()[0] == "/opt/bin/ffmpeg"
    decoder_argv = driver._decoder_argv(
        AudioSource(kind="path", path=Path("/tmp/t.flac")),
        position=0.0,
    )
    assert decoder_argv[0] == "/opt/bin/ffmpeg"


@pytest.mark.asyncio
async def test_snapcast_driver_creates_missing_fifo(tmp_path):
    """The driver mkfifos a missing sink path before starting the encoder."""
    provider = SnapcastOutput()
    config = _fifo_config(tmp_path)
    await provider.validate_config(config)
    driver = SnapcastDriver(config)
    assert not (tmp_path / "snapfifo").exists()
    driver._ensure_fifo()
    assert stat.S_ISFIFO((tmp_path / "snapfifo").stat().st_mode)


@pytest.mark.asyncio
async def test_snapcast_driver_refuses_non_fifo_on_start(tmp_path):
    regular = tmp_path / "snapfifo"
    regular.write_bytes(b"data")
    driver = SnapcastDriver({"mode": "fifo", "fifo_path": str(regular), "sample_rate": 48000})
    with pytest.raises(RuntimeError, match="not a FIFO"):
        driver._ensure_fifo()


@pytest.mark.asyncio
async def test_snapcast_control_host_defaults(tmp_path):
    """control_host falls back to the stream host (tcp) or localhost (fifo)."""
    provider = SnapcastOutput()

    config = _tcp_config()
    await provider.validate_config(config)
    driver = SnapcastDriver(config)
    assert driver._control_host == "snapserver.local"
    assert driver._control_port == 1705

    config = _fifo_config(tmp_path)
    await provider.validate_config(config)
    driver = SnapcastDriver(config)
    assert driver._control_host == "127.0.0.1"

    config = _fifo_config(tmp_path)
    config["control_host"] = "snapctrl.local"
    config["control_port"] = 1785
    await provider.validate_config(config)
    driver = SnapcastDriver(config)
    assert driver._control_host == "snapctrl.local"
    assert driver._control_port == 1785


class _FakeStream:
    async def read(self, _n: int = -1) -> bytes:
        return b""

    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None


class _FakeEncoderProc:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stdin = _FakeStream()
        self.stdout = None
        self.stderr = None

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.005)
        return self.returncode


class _FakeDecoderProc:
    def __init__(self) -> None:
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.stdin = None
        self.returncode: int | None = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


def _patch_exec(monkeypatch, encoder: _FakeEncoderProc):
    async def fake_exec(*args, **_kwargs):
        if "pipe:0" in args:
            return encoder
        return _FakeDecoderProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


@pytest.mark.asyncio
async def test_snapcast_driver_pause_resume_via_silence(tmp_path, monkeypatch):
    """The inherited pause machinery works against the passthrough encoder."""
    provider = SnapcastOutput()
    config = _fifo_config(tmp_path)
    await provider.validate_config(config)
    driver = SnapcastDriver(config)

    _patch_exec(monkeypatch, _FakeEncoderProc())
    await driver.start()
    try:
        # The pipeline starts feeding silence until the first source arrives.
        assert driver.is_paused is True
        await driver.set_source(
            AudioSource(kind="path", path=Path("/tmp/t.flac")),
            position=0.0,
            metadata=TrackMeta(track_id="t1", title="T", artist="A"),
        )
        assert driver.is_paused is False
        await driver.pause()
        assert driver.is_paused is True
    finally:
        await driver.stop()


def _snapserver_status() -> dict:
    """A Server.GetStatus result with two groups and assorted clients."""
    return {
        "server": {
            "streams": [
                {"id": "songhive", "uri": {"query": {"name": "Songhive"}}},
                {"id": "other", "uri": {"query": {"name": "Other"}}},
            ],
            "groups": [
                {
                    "id": "g1",
                    "muted": False,
                    "stream_id": "songhive",
                    "clients": [
                        {"connected": True, "config": {"volume": {"muted": False}}},
                        {"connected": True, "config": {"volume": {"muted": True}}},
                        {"connected": False, "config": {"volume": {"muted": False}}},
                    ],
                },
                {
                    "id": "g2",
                    "muted": False,
                    "stream_id": "other",
                    "clients": [
                        {"connected": True, "config": {"volume": {"muted": False}}},
                    ],
                },
                {
                    "id": "g3",
                    "muted": True,
                    "stream_id": "songhive",
                    "clients": [
                        {"connected": True, "config": {"volume": {"muted": False}}},
                    ],
                },
            ],
        }
    }


@pytest.fixture
async def fake_snapserver():
    """Run a fake snapserver JSON-RPC control endpoint on localhost."""
    state: dict = {"requests": [], "reply": _snapserver_status(), "notifications": False}

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            line = await reader.readline()
            if not line:
                return
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                continue
            state["requests"].append(request)
            if state["notifications"]:
                writer.write(
                    json.dumps({"jsonrpc": "2.0", "method": "Client.OnConnect", "params": {"id": "x"}}).encode()
                    + b"\r\n"
                )
            writer.write(
                json.dumps({"id": request.get("id"), "jsonrpc": "2.0", "result": state["reply"]}).encode() + b"\r\n"
            )
            await writer.drain()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    state["port"] = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()


def _rpc_driver(tmp_path: Path, fake_snapserver: dict, stream_name: str = "") -> SnapcastDriver:
    config = _fifo_config(tmp_path)
    config["control_host"] = "127.0.0.1"
    config["control_port"] = fake_snapserver["port"]
    if stream_name:
        config["stream_name"] = stream_name
    return SnapcastDriver(config)


@pytest.mark.asyncio
async def test_snapcast_listener_count(tmp_path, fake_snapserver):
    """Only connected, unmuted clients in unmuted groups are counted."""
    driver = _rpc_driver(tmp_path, fake_snapserver)
    # g1: 1 live client (1 muted, 1 disconnected), g2: 1, g3 muted group: 0.
    assert await driver.listener_count() == 2

    # Second call is served from the short-TTL cache.
    assert await driver.listener_count() == 2
    assert len(fake_snapserver["requests"]) == 1


@pytest.mark.asyncio
async def test_snapcast_listener_count_filters_by_stream_name(tmp_path, fake_snapserver):
    """stream_name restricts the count to groups playing that stream."""
    driver = _rpc_driver(tmp_path, fake_snapserver, stream_name="Songhive")
    # g1 only (g2 plays "other", g3 is muted).
    assert await driver.listener_count() == 1


@pytest.mark.asyncio
async def test_snapcast_listener_count_unknown_stream_fails_open(tmp_path, fake_snapserver):
    """A stream_name that matches nothing counts all clients instead of zero."""
    driver = _rpc_driver(tmp_path, fake_snapserver, stream_name="no-such-stream")
    assert await driver.listener_count() == 2


@pytest.mark.asyncio
async def test_snapcast_listener_count_skips_notifications(tmp_path, fake_snapserver):
    """Interleaved JSON-RPC notifications don't confuse the reply reader."""
    fake_snapserver["notifications"] = True
    driver = _rpc_driver(tmp_path, fake_snapserver)
    assert await driver.listener_count() == 2


@pytest.mark.asyncio
async def test_snapcast_listener_count_unreachable(tmp_path):
    """An unreachable control endpoint reports zero (uncached) listeners."""
    # Nothing listens on this port.
    listener = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    listener.close()
    await listener.wait_closed()

    driver = _rpc_driver(tmp_path, {"port": port})
    assert await driver.listener_count() == 0
