"""
ExternalTrack model - a track item discovered through an external library adapter.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from .base import Base, TZDateTime


class ExternalTrack(Base):
    """An item discovered through an external library adapter."""

    __tablename__ = "external_tracks"
    __table_args__ = (
        UniqueConstraint(
            "external_library_id",
            "provider_key",
            name="uq_external_tracks_lib_key",
        ),
        Index(
            "ix_external_tracks_lib_state",
            "external_library_id",
            "state",
        ),
        Index(
            "ix_external_tracks_sha256",
            "sha256",
        ),
    )

    external_library_id: Mapped[str] = mapped_column(
        ForeignKey("external_libraries.id", ondelete="CASCADE"),
        index=True,
    )
    track_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("tracks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider_key: Mapped[str] = mapped_column(String(512))
    provider_etag: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provider_mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provider_checksum: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    provider_mtime: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    provider_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    state: Mapped[str] = mapped_column(
        String(16),
        default="active",
        server_default="active",
        index=True,
    )
    sync_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    write_back_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    write_back_pending: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        insert_default=False,
        server_default="0",
    )
    raw_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    external_library = relationship(
        "ExternalLibrary",
        backref=backref(
            "external_tracks",
            lazy="selectin",
            passive_deletes="all",
        ),
        lazy="selectin",
        passive_deletes=True,
    )
    track = relationship(
        "Track",
        lazy="selectin",
    )
