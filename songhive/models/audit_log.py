"""
Audit log model.

Records administrative and security-relevant actions with an actor, target,
and arbitrary JSON details.
"""

from typing import Optional

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AuditLog(Base):
    """A single audit log entry."""

    __tablename__ = "audit_logs"

    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    target_type: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
