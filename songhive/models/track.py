"""
Track model.

``source`` documents where the track came from. Allowed values are:
``"upload"`` (user upload), ``"import"`` (bulk/directory import), and
``"federation"`` ( ActivityPub / federation).
"""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._enums import Visibility
from .base import Base, TZDateTime

if TYPE_CHECKING:
    from .album import Album
    from .artist import Artist
    from .hashtag import Hashtag, HashtagTrack


class Track(Base):
    __tablename__ = "tracks"

    title: Mapped[str] = mapped_column(String(256), index=True)
    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.id"), index=True)
    album_id: Mapped[Optional[str]] = mapped_column(ForeignKey("albums.id"), nullable=True, index=True)
    track_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    disc_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    play_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        insert_default=0,
        server_default="0",
    )
    musicbrainz_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    musicbrainz_enriched_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    genre: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    audio_file_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stored_files.id"), nullable=True, index=True)
    image_file_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("stored_files.id"),
        nullable=True,
        index=True,
    )
    release_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    audio_mime_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    federation_object_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
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

    artist: Mapped["Artist"] = relationship("Artist", back_populates="tracks", lazy="selectin")
    album: Mapped[Optional["Album"]] = relationship("Album", back_populates="tracks", lazy="selectin")
    audio_file = relationship("StoredFile", foreign_keys=[audio_file_id], lazy="selectin")
    image_file = relationship("StoredFile", foreign_keys=[image_file_id], lazy="selectin")
    owner = relationship("User", foreign_keys=[owner_id], lazy="selectin")
    hashtags: Mapped[List["Hashtag"]] = relationship(
        "Hashtag",
        secondary="hashtag_tracks",
        viewonly=True,
        lazy="selectin",
    )
    hashtag_associations: Mapped[List["HashtagTrack"]] = relationship(
        "HashtagTrack",
        back_populates="track",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
