"""
Shared model enums.
"""

from enum import Enum


class Visibility(str, Enum):
    """Visibility levels for shareable media items and activities."""

    PRIVATE = "private"
    MENTIONED = "mentioned"
    LOCAL = "local"
    FOLLOWERS = "followers"
    PUBLIC = "public"

    @classmethod
    def rank(cls, visibility: "Visibility") -> int:
        """Return the precedence rank of ``visibility``; higher means less restrictive."""
        return {
            cls.PRIVATE: 0,
            cls.MENTIONED: 1,
            cls.LOCAL: 2,
            cls.FOLLOWERS: 3,
            cls.PUBLIC: 4,
        }[visibility]

    @classmethod
    def can_contain(cls, child: "Visibility", parent: "Visibility") -> bool:
        """Return whether an entity with ``parent`` visibility can contain a ``child`` activity."""
        return cls.rank(child) <= cls.rank(parent)
