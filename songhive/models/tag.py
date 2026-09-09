"""
Tag and entity association models.
"""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .activity import ActivityTag
    from .album import Album
    from .artist import Artist
    from .library import Library
    from .playlist import Playlist
    from .track import Track
    from .user import User


class Tag(Base):
    """A normalised tag that can be attached to many resources."""

    __tablename__ = "tags"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    tracks: Mapped[List["TagTrack"]] = relationship(
        "TagTrack",
        back_populates="tag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    albums: Mapped[List["TagAlbum"]] = relationship(
        "TagAlbum",
        back_populates="tag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    artists: Mapped[List["TagArtist"]] = relationship(
        "TagArtist",
        back_populates="tag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    playlists: Mapped[List["TagPlaylist"]] = relationship(
        "TagPlaylist",
        back_populates="tag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    libraries: Mapped[List["TagLibrary"]] = relationship(
        "TagLibrary",
        back_populates="tag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    activities: Mapped[List["ActivityTag"]] = relationship(
        "ActivityTag",
        back_populates="tag",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TagTrack(Base):
    """Association between a tag and a track."""

    __tablename__ = "tag_tracks"
    __table_args__ = (UniqueConstraint("tag_id", "track_id", name="uq_tag_tracks"),)

    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"),
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

    tag: Mapped["Tag"] = relationship("Tag", back_populates="tracks", lazy="selectin")
    track: Mapped["Track"] = relationship("Track", back_populates="tag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")


class TagAlbum(Base):
    """Association between a tag and an album."""

    __tablename__ = "tag_albums"
    __table_args__ = (UniqueConstraint("tag_id", "album_id", name="uq_tag_albums"),)

    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"),
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

    tag: Mapped["Tag"] = relationship("Tag", back_populates="albums", lazy="selectin")
    album: Mapped["Album"] = relationship("Album", back_populates="tag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")


class TagArtist(Base):
    """Association between a tag and an artist."""

    __tablename__ = "tag_artists"
    __table_args__ = (UniqueConstraint("tag_id", "artist_id", name="uq_tag_artists"),)

    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"),
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

    tag: Mapped["Tag"] = relationship("Tag", back_populates="artists", lazy="selectin")
    artist: Mapped["Artist"] = relationship("Artist", back_populates="tag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")


class TagPlaylist(Base):
    """Association between a tag and a playlist."""

    __tablename__ = "tag_playlists"
    __table_args__ = (UniqueConstraint("tag_id", "playlist_id", name="uq_tag_playlists"),)

    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"),
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

    tag: Mapped["Tag"] = relationship("Tag", back_populates="playlists", lazy="selectin")
    playlist: Mapped["Playlist"] = relationship("Playlist", back_populates="tag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")


class TagLibrary(Base):
    """Association between a tag and a library."""

    __tablename__ = "tag_libraries"
    __table_args__ = (UniqueConstraint("tag_id", "library_id", name="uq_tag_libraries"),)

    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"),
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

    tag: Mapped["Tag"] = relationship("Tag", back_populates="libraries", lazy="selectin")
    library: Mapped["Library"] = relationship("Library", back_populates="tag_associations", lazy="selectin")
    user: Mapped[Optional["User"]] = relationship("User", lazy="selectin")
