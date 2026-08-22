"""
Library model - a collection of tracks owned by a user.

Visibility uses the standard ``Visibility`` enum. The prompt's
"followers-only" semantics map to ``Visibility.LOCAL``.
"""

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._enums import Visibility
from .base import Base


class Library(Base):
    __tablename__ = "libraries"

    name: Mapped[str] = mapped_column(String(256))
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(16),
        default=Visibility.PRIVATE.value,
        index=True,
    )

    owner = relationship("User", backref="libraries", lazy="selectin")
    tracks = relationship(
        "Track",
        secondary="library_tracks",
        backref="libraries",
        lazy="selectin",
        viewonly=True,
    )
