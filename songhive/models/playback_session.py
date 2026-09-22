"""
PlaybackSession model - persisted playback state for server-side outputs.

``PlaybackSession`` stores the queue, index, position anchor and repeat/shuffle
state for a user. ``PlaybackSessionOutput`` records which outputs are attached
to a session.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TZDateTime


class PlaybackSession(Base):
    """A persisted playback session owned by a single user."""

    __tablename__ = "playback_sessions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('idle', 'playing', 'paused')",
            name="ck_playback_sessions_state",
        ),
        CheckConstraint(
            "repeat IN ('off', 'all', 'one')",
            name="ck_playback_sessions_repeat",
        ),
    )

    # The database column is ``session_id``; the Python attribute is ``id``.
    id: Mapped[str] = mapped_column(
        "session_id",
        String(36),
        primary_key=True,
        default=lambda: str(__import__("uuid").uuid4()),
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(16),
        default="idle",
        server_default="idle",
    )
    current_index: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    position_seconds: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        server_default="0",
    )
    position_anchor_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(),
        nullable=True,
    )
    repeat: Mapped[str] = mapped_column(
        String(16),
        default="off",
        server_default="off",
    )
    shuffle: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )
    controller_connection_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    queue: Mapped[Optional[list]] = mapped_column(
        JSON,
        default=list,
        nullable=True,
    )
    last_active_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime(),
        nullable=True,
    )

    outputs = relationship(
        "PlaybackSessionOutput",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def session_id(self) -> str:
        """Return the canonical session id (the table's primary key)."""
        return self.id


class PlaybackSessionOutput(Base):
    """An output attached to a playback session."""

    __tablename__ = "playback_session_outputs"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "output_kind",
            name="uq_playback_session_outputs_session_kind",
        ),
    )

    id: Mapped[str] = mapped_column(
        "session_output_id",
        String(36),
        primary_key=True,
        default=lambda: str(__import__("uuid").uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("playback_sessions.session_id", ondelete="CASCADE"),
        index=True,
    )
    output_kind: Mapped[str] = mapped_column(String(16))
    output_stream_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("output_streams.output_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    connection_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default="connecting",
        server_default="connecting",
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latency_offset_ms: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )

    session = relationship(
        "PlaybackSession",
        back_populates="outputs",
        lazy="selectin",
    )
