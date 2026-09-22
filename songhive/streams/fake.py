"""
In-memory fake audio output provider and driver for tests.

The fake driver records every command it receives and can emit events manually
so tests can exercise the stream worker without real ffmpeg/Icecast.
"""

from typing import Any

from .base import AudioOutput
from .driver import OutputDriver
from .types import AudioSource, OutputCapabilities, OutputHealth, TrackMeta


class FakeOutput(AudioOutput):
    """Fake output provider whose driver records commands for assertions."""

    provider_type = "fake"
    user_configurable = True
    _REDACTED_KEYS = frozenset({"secret"})
    FIELDS = [
        {"name": "items", "type": "dict"},
    ]

    async def validate_config(self, config: dict) -> OutputCapabilities:
        """Validate that the config contains an ``items`` dict."""
        items = config.get("items")
        if not isinstance(items, dict):
            raise ValueError('config["items"] must be a dict')

        self._capabilities = OutputCapabilities(
            pause_supported=True,
            seek_supported=True,
            metadata_updates=False,
            multi_listener=False,
            user_configurable=True,
        )
        return self._capabilities

    def create_driver(self, config: dict) -> OutputDriver:
        """Create a new fake driver."""
        return FakeDriver(config)


class FakeDriver(OutputDriver):
    """In-memory driver that records commands and exposes manual event triggers."""

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.commands: list[Any] = []
        self.current_metadata: TrackMeta | None = None
        self.playing = False
        self.paused = False

    async def start(self) -> None:
        self.commands.append("start")
        self.playing = True
        self.paused = False

    async def stop(self) -> None:
        self.commands.append("stop")
        self.playing = False
        self.paused = False

    async def set_source(
        self,
        source: AudioSource,
        *,
        position: float,
        metadata: TrackMeta,
    ) -> None:
        self.commands.append(("set_source", source, position, metadata))
        self.current_metadata = metadata
        self.playing = True
        self.paused = False

    async def pause(self) -> None:
        self.commands.append("pause")
        self.paused = True

    async def resume(self) -> None:
        self.commands.append("resume")
        self.paused = False

    async def update_metadata(self, metadata: TrackMeta) -> None:
        self.commands.append(("update_metadata", metadata))

    async def health(self) -> OutputHealth:
        return OutputHealth(ok=True, message="Fake driver is healthy")

    def trigger_source_ended(self) -> None:
        """Emit a ``source_ended`` event, as a real decoder EOF would."""
        self._emit({"type": "source_ended"})

    def trigger_error(self, message: str) -> None:
        """Emit an ``error`` event with the supplied message."""
        self._emit({"type": "error", "message": message})
