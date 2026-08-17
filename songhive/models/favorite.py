"""
Favorite model.
"""

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "track_id", name="uq_user_track_favorite"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"), index=True)

    user = relationship("User", backref="favorites", lazy="selectin")
    track = relationship("Track", backref="favorited_by", lazy="selectin")
