"""
Album model.
"""

from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._enums import Visibility
from .base import Base


class Album(Base):
    __tablename__ = "albums"

    title: Mapped[str] = mapped_column(String(256), index=True)
    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.id"), index=True)
    musicbrainz_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, unique=True, index=True)
    release_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cover_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cover_file_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stored_files.id"), nullable=True, index=True)
    owner_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    visibility: Mapped[str] = mapped_column(
        String(16),
        default=Visibility.PRIVATE.value,
        index=True,
    )

    artist = relationship("Artist", backref="albums", lazy="selectin")
    cover_file = relationship("StoredFile", foreign_keys=[cover_file_id], lazy="selectin")
