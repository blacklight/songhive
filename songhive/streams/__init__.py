"""
Audio output provider package.

Importing a provider module self-registers it with
``songhive.streams.registry``. Real providers are registered here; the ``fake``
provider is intentionally only registered in test fixtures.
"""

from . import http, icecast  # noqa: F401
from .base import AudioOutput
from .driver import OutputDriver
from .registry import get_output, is_user_configurable, list_output_types, register_output
from .types import AudioSource, OutputCapabilities, OutputHealth, TrackMeta

__all__ = [
    "AudioOutput",
    "AudioSource",
    "OutputCapabilities",
    "OutputDriver",
    "OutputHealth",
    "TrackMeta",
    "get_output",
    "is_user_configurable",
    "list_output_types",
    "register_output",
]
