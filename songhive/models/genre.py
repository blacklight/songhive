"""
Genre and entity association models.
"""

from typing import TYPE_CHECKING, List

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .album import Album
    from .track import Track


class Genre(Base):
    """A normalised genre that can be attached to tracks and albums."""

    __tablename__ = "genres"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    tracks: Mapped[List["GenreTrack"]] = relationship(
        "GenreTrack",
        back_populates="genre",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    albums: Mapped[List["GenreAlbum"]] = relationship(
        "GenreAlbum",
        back_populates="genre",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class GenreTrack(Base):
    """Association between a genre and a track."""

    __tablename__ = "genre_tracks"
    __table_args__ = (UniqueConstraint("genre_id", "track_id", name="uq_genre_tracks"),)

    genre_id: Mapped[str] = mapped_column(
        ForeignKey("genres.id", ondelete="CASCADE"),
        index=True,
    )
    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"),
        index=True,
    )
    inherited: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",
    )

    genre: Mapped["Genre"] = relationship("Genre", back_populates="tracks", lazy="selectin")
    track: Mapped["Track"] = relationship("Track", back_populates="genre_associations", lazy="selectin")


class GenreAlbum(Base):
    """Association between a genre and an album."""

    __tablename__ = "genre_albums"
    __table_args__ = (UniqueConstraint("genre_id", "album_id", name="uq_genre_albums"),)

    genre_id: Mapped[str] = mapped_column(
        ForeignKey("genres.id", ondelete="CASCADE"),
        index=True,
    )
    album_id: Mapped[str] = mapped_column(
        ForeignKey("albums.id", ondelete="CASCADE"),
        index=True,
    )

    genre: Mapped["Genre"] = relationship("Genre", back_populates="albums", lazy="selectin")
    album: Mapped["Album"] = relationship("Album", back_populates="genre_associations", lazy="selectin")
