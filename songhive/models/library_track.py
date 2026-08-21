"""
Library-to-track membership model.
"""

from typing import Optional

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class LibraryTrack(Base):
    """Join table linking a library to its member tracks."""

    __tablename__ = "library_tracks"
    __table_args__ = (UniqueConstraint("library_id", "track_id"),)

    library_id: Mapped[str] = mapped_column(
        ForeignKey("libraries.id", ondelete="CASCADE"),
        index=True,
    )
    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"),
        index=True,
    )
    added_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    library = relationship("Library", backref="library_tracks", lazy="selectin")
    track = relationship("Track", backref="library_tracks", lazy="selectin")
