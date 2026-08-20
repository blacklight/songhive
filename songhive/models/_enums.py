"""
Shared model enums.
"""

from enum import Enum


class Visibility(str, Enum):
    """Visibility levels for shareable media items."""

    PRIVATE = "private"
    LOCAL = "local"
    PUBLIC = "public"
