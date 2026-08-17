"""
Listening history model.
"""

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ListeningHistory(Base):
    __tablename__ = "listening_history"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"), index=True)

    user = relationship("User", backref="listening_history", lazy="selectin")
    track = relationship("Track", lazy="selectin")
