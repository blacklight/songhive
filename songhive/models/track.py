"""
Track model.
"""

from typing import Optional

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Track(Base):
    __tablename__ = "tracks"

    title: Mapped[str] = mapped_column(String(256), index=True)
    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.id"), index=True)
    album_id: Mapped[Optional[str]] = mapped_column(ForeignKey("albums.id"), nullable=True, index=True)
    track_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    disc_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    musicbrainz_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, unique=True, index=True)
    genre: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    audio_file_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stored_files.id"), nullable=True, index=True)

    artist = relationship("Artist", backref="tracks", lazy="selectin")
    album = relationship("Album", backref="tracks", lazy="selectin")
    audio_file = relationship("StoredFile", foreign_keys=[audio_file_id], lazy="selectin")
