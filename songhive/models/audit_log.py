"""
Audit log model.

Records administrative and security-relevant actions with an actor, target,
and arbitrary JSON details.
"""

from enum import Enum
from typing import Optional

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AuditTargetType(str, Enum):
    """
    Canonical audit log target types.

    This is the single source of truth for ``AuditLog.target_type`` values:
    ``services.audit.log_action`` only accepts members of this enum, and the
    admin API exposes them so the frontend filter dropdown stays in sync.
    """

    ACTIVITY = "activity"
    ACTOR = "actor"
    ALBUM = "album"
    API_TOKEN = "api_token"
    ARTIST = "artist"
    CELERY = "celery"
    EXTERNAL_LIBRARY = "external_library"
    EXTERNAL_TRACK = "external_track"
    FEDERATION = "federation"
    FILE = "file"
    GENRE = "genre"
    IMAGES = "images"
    INSTANCE = "instance"
    INVITE = "invite"
    LIBRARY = "library"
    OAUTH_CLIENT = "oauth_client"
    PLAYLIST = "playlist"
    REPORT = "report"
    SETTING = "setting"
    STORAGE = "storage"
    TAG = "tag"
    TRACK = "track"
    USER = "user"
    USER_SESSION = "user_session"


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
