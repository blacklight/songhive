"""
ExternalItem model - an entity (track/album/artist/playlist) discovered through
an entity-backed external library adapter.

Unlike ``ExternalTrack``, which is a file-shaped reference (bytes, hashes,
write-back), an ``ExternalItem`` references a first-class local entity that the
provider owns metadata for and streams remotely. ``provider_key`` is the
provider's opaque stable item id; exactly one of the four entity FKs is set.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from .base import Base, TZDateTime


class ExternalItem(Base):
    """A provider entity reference for entity-backed external libraries."""

    __tablename__ = "external_items"
    __table_args__ = (
        UniqueConstraint(
            "external_library_id",
            "kind",
            "provider_key",
            name="uq_external_items_lib_kind_key",
        ),
        CheckConstraint(
            "(CASE WHEN track_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN album_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN artist_id IS NOT NULL THEN 1 ELSE 0 END"
            " + CASE WHEN playlist_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_external_items_one_entity",
        ),
        Index(
            "ix_external_items_lib_state",
            "external_library_id",
            "state",
        ),
    )

    external_library_id: Mapped[str] = mapped_column(
        ForeignKey("external_libraries.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), index=True)
    provider_key: Mapped[str] = mapped_column(String(512))
    track_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    album_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("albums.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    artist_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    playlist_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider_etag: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provider_mtime: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    state: Mapped[str] = mapped_column(
        String(16),
        default="active",
        server_default="active",
        index=True,
    )
    raw_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    external_library = relationship(
        "ExternalLibrary",
        backref=backref(
            "external_items",
            lazy="selectin",
            passive_deletes="all",
        ),
        lazy="selectin",
        passive_deletes=True,
    )
    track = relationship("Track", lazy="selectin")
    album = relationship("Album", lazy="selectin")
    artist = relationship("Artist", lazy="selectin")
    playlist = relationship("Playlist", lazy="selectin")
