"""
Instance settings model.

Stores runtime-editable key/value pairs that override file-based
configuration. Values are JSON-encoded so scalars and small structures can be
stored uniformly.
"""

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Setting(Base):
    """A runtime-editable instance setting."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
