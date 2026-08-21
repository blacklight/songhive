"""
Artist model.
"""

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Artist(Base):
    __tablename__ = "artists"

    name: Mapped[str] = mapped_column(String(256), index=True)
    musicbrainz_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, unique=True, index=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    image_file_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("stored_files.id"),
        nullable=True,
        index=True,
    )

    image_file = relationship("StoredFile", foreign_keys=[image_file_id], lazy="selectin")
