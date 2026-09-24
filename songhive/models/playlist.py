"""
Playlist model.
"""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._enums import Visibility
from .base import Base

if TYPE_CHECKING:
    from .podcast import PodcastEpisode
    from .remote_object import RemoteObject
    from .tag import Tag, TagPlaylist
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
    tags: Mapped[List["Tag"]] = relationship(
        "Tag",
        secondary="tag_playlists",
        viewonly=True,
        lazy="selectin",
    )
    tag_associations: Mapped[List["TagPlaylist"]] = relationship(
        "TagPlaylist",
        back_populates="playlist",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PlaylistTrack(Base):
    """One ordered entry in a playlist — a local track, a podcast episode, or a cached remote object.

    Exactly one of ``track_id`` / ``podcast_episode_id`` /
    ``remote_object_id`` is set; episode rows keep the remote enclosure URL
    on the episode itself and are streamed from the source, never copied
    locally. Remote rows reference the ``remote_objects`` cache and are
    played through the remote stream-resolution endpoint.
    """

    __tablename__ = "playlist_tracks"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN track_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN podcast_episode_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN remote_object_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_playlist_tracks_one_item",
        ),
    )

    playlist_id: Mapped[str] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tracks.id"), nullable=True, index=True)
    podcast_episode_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("podcast_episodes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    remote_object_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("remote_objects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer)

    playlist: Mapped["Playlist"] = relationship("Playlist", back_populates="tracks", lazy="selectin")
    track: Mapped[Optional["Track"]] = relationship("Track", lazy="selectin")
    episode: Mapped[Optional["PodcastEpisode"]] = relationship("PodcastEpisode", lazy="selectin")
    remote_object: Mapped[Optional["RemoteObject"]] = relationship("RemoteObject", lazy="selectin")
