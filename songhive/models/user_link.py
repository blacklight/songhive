"""
User profile link model.
"""

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, validates

from .base import Base


class UserLink(Base):
    __tablename__ = "user_links"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(512))

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
        return value
