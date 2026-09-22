"""
Abstract base class for audio output drivers.

A driver owns a single long-lived output pipeline (e.g. an ffmpeg encoder
pushing an Icecast mount). The stream worker consumes events from the driver's
``events`` queue and issues commands through the public methods below.
"""

import asyncio
from abc import ABC, abstractmethod

from .types import AudioSource, OutputHealth, TrackMeta


class OutputDriver(ABC):
    """Abstract base class for a running audio output driver."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.events: asyncio.Queue[dict] = asyncio.Queue()

    @abstractmethod
    async def start(self) -> None:
        """Start the output pipeline."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the output pipeline and release any resources."""

    @abstractmethod
    async def set_source(
        self,
        source: AudioSource,
        *,
        position: float,
        metadata: TrackMeta,
    ) -> None:
        """Switch to a new audio source, optionally starting at ``position``."""

    @abstractmethod
    async def pause(self) -> None:
        """Pause playback while keeping the output pipeline alive."""

    @abstractmethod
    async def resume(self) -> None:
        """Resume playback."""

    @abstractmethod
    async def update_metadata(self, metadata: TrackMeta) -> None:
        """Update the now-playing metadata advertised by the output."""

    @abstractmethod
    async def health(self) -> OutputHealth:
        """Return the current runtime health of the driver."""

    def _emit(self, event: dict) -> None:
        """Put an event onto the worker queue from any context."""
        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            pass
