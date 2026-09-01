"""
ExternalSyncRun model - records a single sync run against an ExternalLibrary.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TZDateTime


class ExternalSyncRun(Base):
    """A single run of the external-library sync process."""

    __tablename__ = "external_sync_runs"

    external_library_id: Mapped[str] = mapped_column(
        ForeignKey("external_libraries.id", ondelete="CASCADE"),
        index=True,
    )
    triggered_by: Mapped[str] = mapped_column(String(16))
    triggered_by_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default="queued",
        server_default="queued",
        index=True,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    items_seen: Mapped[int] = mapped_column(
        Integer,
        default=0,
        insert_default=0,
        server_default="0",
    )
    tracks_created: Mapped[int] = mapped_column(
        Integer,
        default=0,
        insert_default=0,
        server_default="0",
    )
    tracks_updated: Mapped[int] = mapped_column(
        Integer,
        default=0,
        insert_default=0,
        server_default="0",
    )
    tracks_shadowed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        insert_default=0,
        server_default="0",
    )
    tracks_tombstoned: Mapped[int] = mapped_column(
        Integer,
        default=0,
        insert_default=0,
        server_default="0",
    )
    tracks_missing: Mapped[int] = mapped_column(
        Integer,
        default=0,
        insert_default=0,
        server_default="0",
    )
    tracks_failed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        insert_default=0,
        server_default="0",
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
