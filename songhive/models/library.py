"""
Library model - a collection of uploads owned by a user.
"""

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Library(Base):
    __tablename__ = "libraries"

    name: Mapped[str] = mapped_column(String(256))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)

    owner = relationship("User", backref="libraries", lazy="selectin")
