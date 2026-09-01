"""
ExternalLibrary model - an adapter-backed library attached to a Library.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from .base import Base, TZDateTime


class ExternalLibrary(Base):
    """An adapter-backed library whose audio bytes live outside Songhive storage."""

    __tablename__ = "external_libraries"

    library_id: Mapped[str] = mapped_column(
        ForeignKey("libraries.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    provider_type: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )
    scope: Mapped[str] = mapped_column(
        String(16),
        default="user",
        server_default="user",
        index=True,
    )
    name: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
    )
    config: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        insert_default=True,
        server_default="1",
    )
    include_in_library_index: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        insert_default=False,
        server_default="0",
    )
    sync_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        insert_default=True,
        server_default="1",
    )
    sync_interval_seconds: Mapped[Optional[int]] = mapped_column(
        nullable=True,
    )
    last_sync_started_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(),
        nullable=True,
    )
    last_sync_completed_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(),
        nullable=True,
    )
    last_sync_status: Mapped[Optional[str]] = mapped_column(
        String(16),
        nullable=True,
    )
    last_sync_error: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    capabilities: Mapped[Optional[dict]] = mapped_column(
        JSON,
        nullable=True,
    )
    created_by_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    library = relationship(
        "Library",
        backref=backref(
            "external_library",
            uselist=False,
            lazy="selectin",
            passive_deletes=True,
        ),
        lazy="selectin",
        passive_deletes=True,
    )
