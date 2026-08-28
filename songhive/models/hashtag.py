"""
Hashtag and entity association models.
"""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .album import Album
    from .artist import Artist
    from .library import Library
    from .playlist import Playlist
    from .track import Track
    from .user import User


class Hashtag(Base):
    """A normalised hashtag that can be attached to many resources."""

    __tablename__ = "hashtags"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    tracks: Mapped[List["HashtagTrack"]] = relationship(
        "HashtagTrack",
        back_populates="hashtag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    albums: Mapped[List["HashtagAlbum"]] = relationship(
        "HashtagAlbum",
        back_populates="hashtag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    artists: Mapped[List["HashtagArtist"]] = relationship(
        "HashtagArtist",
        back_populates="hashtag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    playlists: Mapped[List["HashtagPlaylist"]] = relationship(
        "HashtagPlaylist",
        back_populates="hashtag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    libraries: Mapped[List["HashtagLibrary"]] = relationship(
        "HashtagLibrary",
        back_populates="hashtag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class HashtagTrack(Base):
    """Association between a hashtag and a track."""

    __tablename__ = "hashtag_tracks"
    __table_args__ = (UniqueConstraint("hashtag_id", "track_id", name="uq_hashtag_tracks"),)

    hashtag_id: Mapped[str] = mapped_column(
        ForeignKey("hashtags.id", ondelete="CASCADE"),
        index=True,
    )
    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    hashtag: Mapped["Hashtag"] = relationship("Hashtag", back_populates="tracks", lazy="selectin")
    track: Mapped["Track"] = relationship("Track", back_populates="hashtag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")


class HashtagAlbum(Base):
    """Association between a hashtag and an album."""

    __tablename__ = "hashtag_albums"
    __table_args__ = (UniqueConstraint("hashtag_id", "album_id", name="uq_hashtag_albums"),)

    hashtag_id: Mapped[str] = mapped_column(
        ForeignKey("hashtags.id", ondelete="CASCADE"),
        index=True,
    )
    album_id: Mapped[str] = mapped_column(
        ForeignKey("albums.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    hashtag: Mapped["Hashtag"] = relationship("Hashtag", back_populates="albums", lazy="selectin")
    album: Mapped["Album"] = relationship("Album", back_populates="hashtag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")


class HashtagArtist(Base):
    """Association between a hashtag and an artist."""

    __tablename__ = "hashtag_artists"
    __table_args__ = (UniqueConstraint("hashtag_id", "artist_id", name="uq_hashtag_artists"),)

    hashtag_id: Mapped[str] = mapped_column(
        ForeignKey("hashtags.id", ondelete="CASCADE"),
        index=True,
    )
    artist_id: Mapped[str] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    hashtag: Mapped["Hashtag"] = relationship("Hashtag", back_populates="artists", lazy="selectin")
    artist: Mapped["Artist"] = relationship("Artist", back_populates="hashtag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")


class HashtagPlaylist(Base):
    """Association between a hashtag and a playlist."""

    __tablename__ = "hashtag_playlists"
    __table_args__ = (UniqueConstraint("hashtag_id", "playlist_id", name="uq_hashtag_playlists"),)

    hashtag_id: Mapped[str] = mapped_column(
        ForeignKey("hashtags.id", ondelete="CASCADE"),
        index=True,
    )
    playlist_id: Mapped[str] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    hashtag: Mapped["Hashtag"] = relationship("Hashtag", back_populates="playlists", lazy="selectin")
    playlist: Mapped["Playlist"] = relationship("Playlist", back_populates="hashtag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")


class HashtagLibrary(Base):
    """Association between a hashtag and a library."""

    __tablename__ = "hashtag_libraries"
    __table_args__ = (UniqueConstraint("hashtag_id", "library_id", name="uq_hashtag_libraries"),)

    hashtag_id: Mapped[str] = mapped_column(
        ForeignKey("hashtags.id", ondelete="CASCADE"),
        index=True,
    )
    library_id: Mapped[str] = mapped_column(
        ForeignKey("libraries.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    hashtag: Mapped["Hashtag"] = relationship("Hashtag", back_populates="libraries", lazy="selectin")
    library: Mapped["Library"] = relationship("Library", back_populates="hashtag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")
