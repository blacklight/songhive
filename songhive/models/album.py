"""
Album model.
"""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._enums import Visibility
from .base import Base

if TYPE_CHECKING:
    from .artist import Artist
    from .genre import Genre, GenreAlbum
    from .hashtag import Hashtag, HashtagAlbum
    from .track import Track


class Album(Base):
    __tablename__ = "albums"

    title: Mapped[str] = mapped_column(String(256), index=True)
    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.id"), index=True)
    musicbrainz_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, unique=True, index=True)
    release_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    genre: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
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

    artist: Mapped["Artist"] = relationship("Artist", back_populates="albums", lazy="selectin")
    cover_file = relationship("StoredFile", foreign_keys=[cover_file_id], lazy="selectin")
    owner = relationship("User", foreign_keys=[owner_id], lazy="selectin")
    tracks: Mapped[List["Track"]] = relationship("Track", back_populates="album", lazy="selectin")
    hashtags: Mapped[List["Hashtag"]] = relationship(
        "Hashtag",
        secondary="hashtag_albums",
        viewonly=True,
        lazy="selectin",
    )
    hashtag_associations: Mapped[List["HashtagAlbum"]] = relationship(
        "HashtagAlbum",
        back_populates="album",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    genres: Mapped[List["Genre"]] = relationship(
        "Genre",
        secondary="genre_albums",
        viewonly=True,
        lazy="selectin",
    )
    genre_associations: Mapped[List["GenreAlbum"]] = relationship(
        "GenreAlbum",
        back_populates="album",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
