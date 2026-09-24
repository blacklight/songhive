"""
Favorite model.
"""

from typing import Optional

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Favorite(Base):
    """A favorited track — either a local track or a cached remote object.

    Exactly one of ``track_id`` / ``remote_object_id`` is set; remote
    favorites reference the ``remote_objects`` cache row so remote tracks are
    never copied into the local catalog.
    """

    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "track_id", name="uq_user_track_favorite"),
        UniqueConstraint("user_id", "remote_object_id", name="uq_user_remote_favorite"),
        CheckConstraint(
            "(track_id IS NULL) <> (remote_object_id IS NULL)",
            name="ck_favorites_one_item",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    track_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tracks.id"), index=True, nullable=True)
    remote_object_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("remote_objects.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )

    user = relationship("User", backref="favorites", lazy="selectin")
    track = relationship("Track", backref="favorited_by", lazy="selectin")
    remote_object = relationship("RemoteObject", lazy="selectin")
