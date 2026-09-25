"""
DownloadArchive model — an asynchronous bulk-download request.

A ``DownloadArchive`` row records a user's request to package a set of
playable items (local/external tracks, cached remote objects, podcast
episodes) into a ZIP archive. The row is created with ``status="pending"``;
a Celery task materializes each item, writes the archive into the storage
backend as a ``StoredFile`` (``archive_file_id``), and flips the status to
``ready`` (or ``failed`` with ``error``). ``item_errors`` lists the items
that could not be materialized when the archive still succeeded partially.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TZDateTime

DOWNLOAD_ARCHIVE_STATUSES = ("pending", "processing", "ready", "failed")

_STATUS_CHECK = f"status IN ({', '.join(repr(s) for s in DOWNLOAD_ARCHIVE_STATUSES)})"


class DownloadArchive(Base):
    __tablename__ = "download_archives"
    __table_args__ = (
        Index("ix_download_archives_user_id_created_at", "user_id", "created_at"),
        CheckConstraint(_STATUS_CHECK, name="ck_download_archives_status"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # Human-readable archive name ("Album: Foo", "My playlist", "3 tracks").
    label: Mapped[str] = mapped_column(String(256))
    # Snapshot of the resolved items: [{kind, ref, title, artist}].
    items: Mapped[list] = mapped_column(JSON)
    archive_file_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Per-item failures for archives that completed partially:
    # [{ref, title, error}].
    item_errors: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    # Whole-archive failure message (status="failed").
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)

    archive_file = relationship("StoredFile", foreign_keys=[archive_file_id], lazy="selectin")
