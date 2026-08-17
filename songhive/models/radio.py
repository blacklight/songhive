"""
Radio model - dynamic radio configuration.
"""

from typing import Optional

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Radio(Base):
    __tablename__ = "radios"

    name: Mapped[str] = mapped_column(String(256))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JSON-encoded filter configuration
    config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    owner = relationship("User", backref="radios", lazy="selectin")
