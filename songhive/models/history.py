"""
Listening history model.
"""

from typing import Optional

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ListeningHistory(Base):
    __tablename__ = "listening_history"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    track_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tracks.id"), index=True)
    remote_object_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("remote_objects.id", ondelete="CASCADE"),
        index=True,
    )

    user = relationship("User", backref="listening_history", lazy="selectin")
    track = relationship("Track", lazy="selectin")
    remote_object = relationship("RemoteObject", lazy="selectin")
