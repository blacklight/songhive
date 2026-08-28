"""
Playlist model.
"""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._enums import Visibility
from .base import Base

if TYPE_CHECKING:
    from .track import Track


class Playlist(Base):
    __tablename__ = "playlists"

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

    owner = relationship("User", backref="playlists", lazy="selectin")
    image_file = relationship("StoredFile", foreign_keys=[image_file_id], lazy="selectin")
    cover_file = relationship("StoredFile", foreign_keys=[cover_file_id], lazy="selectin")
    tracks: Mapped[List["PlaylistTrack"]] = relationship("PlaylistTrack", back_populates="playlist", lazy="selectin")


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"

    playlist_id: Mapped[str] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)

    playlist: Mapped["Playlist"] = relationship("Playlist", back_populates="tracks", lazy="selectin")
    track: Mapped["Track"] = relationship("Track", lazy="selectin")
