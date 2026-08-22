"""
Transcoded file cache metadata.

Each row maps a (track_id, format, bitrate) triple to a StoredFile that holds
the transcoded audio. Existing deployments can create the table with:

.. code-block:: sql

    CREATE TABLE transcoded_files (
        id TEXT PRIMARY KEY,
        track_id TEXT NOT NULL REFERENCES tracks(id),
        format VARCHAR(16) NOT NULL,
        bitrate VARCHAR(16) NOT NULL,
        stored_file_id TEXT NOT NULL REFERENCES stored_files(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_transcoded_file UNIQUE (track_id, format, bitrate)
    );
    CREATE INDEX ix_transcoded_files_track_id ON transcoded_files(track_id);
    CREATE INDEX ix_transcoded_files_stored_file_id ON transcoded_files(stored_file_id);
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class TranscodedFile(Base):
    """Cache entry for a pre-transcoded audio file."""

    __tablename__ = "transcoded_files"
    __table_args__ = (
        UniqueConstraint(
            "track_id",
            "format",
            "bitrate",
            name="uq_transcoded_file",
        ),
    )

    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id"),
        index=True,
    )
    format: Mapped[str] = mapped_column(String(16))
    bitrate: Mapped[str] = mapped_column(String(16))
    stored_file_id: Mapped[str] = mapped_column(
        ForeignKey("stored_files.id", ondelete="CASCADE"),
        index=True,
    )

    stored_file = relationship("StoredFile", lazy="selectin")
    track = relationship("Track", lazy="selectin")
