"""
User profile link model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from .base import Base

if TYPE_CHECKING:
    from .user import User


class UserLink(Base):
    __tablename__ = "user_links"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(512))

    user: Mapped["User"] = relationship("User", back_populates="links")

    @validates("name")
    def _validate_name(self, _key: str, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("Link name cannot be empty")
        return value

    @validates("url")
    def _validate_url(self, _key: str, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("Link URL cannot be empty")
        if not value.startswith(("https://", "http://")):
            raise ValueError("Link URL must start with http:// or https://")
        return value
