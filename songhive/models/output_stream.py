"""
OutputStream model - a user-configured audio output destination.

``config`` is a Fernet-encrypted JSON blob stored as text; it is decrypted on
read by the output service and never returned to clients in the clear.
"""

from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class OutputStream(Base):
    """A configured output destination such as an Icecast mount."""

    __tablename__ = "output_streams"

    # Override the inherited ``id`` so the database column is named ``output_id``
    # while the Python attribute stays ``id`` for consistency with the rest of
    # the model layer.
    id: Mapped[str] = mapped_column(
        "output_id",
        String(36),
        primary_key=True,
        default=lambda: str(__import__("uuid").uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    provider_type: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256))
    config: Mapped[str] = mapped_column(Text)
    capabilities: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        insert_default=True,
        server_default="1",
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def output_id(self) -> str:
        """Return the canonical output id (the table's primary key)."""
        return self.id
