"""
API token model.

Long-lived named tokens issued as HS256 JWTs for programmatic access.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TZDateTime


class ApiToken(Base):
    """SQLAlchemy model for a user-issued API token."""

    __tablename__ = "api_tokens"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "name",
            name="uq_api_tokens_user_name",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    jti: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(),
        nullable=True,
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(),
        nullable=True,
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(),
        nullable=True,
    )

    @property
    def is_active(self) -> bool:
        """Return True if the token has not been revoked and has not expired."""
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= datetime.now(timezone.utc):
            return False
        return True
