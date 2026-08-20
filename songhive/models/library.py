"""
Library model - a collection of uploads owned by a user.
"""

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ._enums import Visibility
from .base import Base


class Library(Base):
    __tablename__ = "libraries"

    name: Mapped[str] = mapped_column(String(256))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(16),
        default=Visibility.PRIVATE.value,
        index=True,
    )

    owner = relationship("User", backref="libraries", lazy="selectin")
