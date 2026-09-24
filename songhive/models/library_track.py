"""
Library-to-track membership model.
"""

from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class LibraryTrack(Base):
    """Join table linking a library to its member tracks.

    A member is either a local track or a cached remote object — exactly one
    of ``track_id`` / ``remote_object_id`` is set. Remote members keep their
    federated identity and are played through the remote stream-resolution
    endpoint rather than copied into the local catalog.
    """

    __tablename__ = "library_tracks"
    __table_args__ = (
        UniqueConstraint("library_id", "track_id"),
        UniqueConstraint("library_id", "remote_object_id", name="uq_library_remote_track"),
        CheckConstraint(
            "(track_id IS NULL) <> (remote_object_id IS NULL)",
            name="ck_library_tracks_one_item",
        ),
    )

    library_id: Mapped[str] = mapped_column(
        ForeignKey("libraries.id", ondelete="CASCADE"),
        index=True,
    )
    track_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    remote_object_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("remote_objects.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    added_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    library = relationship("Library", backref="library_tracks", lazy="selectin")
    track = relationship("Track", backref="library_tracks", lazy="selectin")
    remote_object = relationship("RemoteObject", lazy="selectin")
