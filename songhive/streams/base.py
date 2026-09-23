"""
Abstract base class for audio output providers.
"""

import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Optional

from .driver import OutputDriver
from .types import OutputCapabilities


class AudioOutput(ABC):
    """Abstract base class for an audio output provider (e.g. Icecast, Fake)."""

    provider_type: ClassVar[str] = ""
    user_configurable: ClassVar[bool] = False

    # Optional human-friendly label for UI listings; defaults to the type key.
    label: ClassVar[str] = ""

    # Optional schema hint for the frontend output-configuration form.
    FIELDS: ClassVar[list[dict]] = []

    # By default, redact any key that looks like a secret-bearing field.  A
    # subclass may override this with a set of exact field names instead.
    _REDACTED_KEYS: ClassVar[Any] = re.compile(
        r"(secret|password|token|key|credential)",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        self._capabilities: Optional[OutputCapabilities] = None

    @abstractmethod
    async def validate_config(self, config: dict) -> OutputCapabilities:
        """Validate provider configuration and return capabilities."""

    @abstractmethod
    def create_driver(self, config: dict) -> OutputDriver:
        """Create a runtime driver for this provider using ``config``."""

    def capabilities(self) -> OutputCapabilities:
        """Return the cached capabilities populated by validate_config."""
        if self._capabilities is None:
            raise RuntimeError("Capabilities have not been loaded; call validate_config first")
        return self._capabilities

    def sanitize_config_for_response(self, config: dict) -> dict:
        """Return a shallow copy of ``config`` with sensitive values redacted."""
        redactor = self._REDACTED_KEYS

        if hasattr(redactor, "search"):

            def _should_redact(key: str) -> bool:
                return bool(redactor.search(key))

        else:

            def _should_redact(key: str) -> bool:
                return key in redactor

        return {key: "<redacted>" if _should_redact(key) else value for key, value in config.items()}
