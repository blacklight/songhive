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

    @classmethod
    def federates(cls, visibility: "Visibility") -> bool:
        """
        Return whether ``visibility`` is delivered to remote instances.

        ``private`` and ``local`` content never leaves the instance, while
        ``mentioned``, ``followers``, and ``public`` content is federated to
        the addressed audience.
        """
        return visibility in (cls.MENTIONED, cls.FOLLOWERS, cls.PUBLIC)
