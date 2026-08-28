"""
Library model - a collection of tracks owned by a user.

Visibility uses the standard ``Visibility`` enum. The prompt's
"followers-only" semantics map to ``Visibility.LOCAL``.
"""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._enums import Visibility
from .base import Base

if TYPE_CHECKING:
    from .hashtag import Hashtag, HashtagLibrary


class Library(Base):
    __tablename__ = "libraries"

    name: Mapped[str] = mapped_column(String(256))
    owner_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(16),
        default=Visibility.PRIVATE.value,
        index=True,
    )
    image_file_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("stored_files.id"),
        nullable=True,
        index=True,
    )
    cover_file_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("stored_files.id"),
        nullable=True,
        index=True,
    )

    owner = relationship("User", backref="libraries", lazy="selectin")
    image_file = relationship("StoredFile", foreign_keys=[image_file_id], lazy="selectin")
    cover_file = relationship("StoredFile", foreign_keys=[cover_file_id], lazy="selectin")
    tracks = relationship(
        "Track",
        secondary="library_tracks",
        backref="libraries",
        lazy="selectin",
        viewonly=True,
    )
    hashtags: Mapped[List["Hashtag"]] = relationship(
        "Hashtag",
        secondary="hashtag_libraries",
        viewonly=True,
        lazy="selectin",
    )
    hashtag_associations: Mapped[List["HashtagLibrary"]] = relationship(
        "HashtagLibrary",
        back_populates="library",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
