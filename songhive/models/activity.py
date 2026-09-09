"""
Activity, mention, and delivery-target models for the federation layer.

An ``Activity`` records a federation-relevant event (create, announce, like,
reply, quote, mention, update, delete, webmention) attached to a local or
remote entity.  ``ActivityMention`` rows capture ``@handle`` mentions embedded
in activity content, while ``ActivityTarget`` rows track outbound delivery of
the activity to remote inboxes.
"""

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from .base import Base, TZDateTime

if TYPE_CHECKING:
    from .tag import Tag

ACTIVITY_ENTITY_TYPES = ("track", "album", "artist", "playlist", "library")
ACTIVITY_TYPES = (
    "create",
    "announce",
    "like",
    "reply",
    "quote",
    "mention",
    "update",
    "delete",
    "webmention",
)
ACTIVITY_TARGET_STATES = ("pending", "sent", "failed", "skipped")

_ENTITY_TYPE_CHECK = f"entity_type IN ({', '.join(repr(t) for t in ACTIVITY_ENTITY_TYPES)})"
_ACTIVITY_TYPE_CHECK = f"activity_type IN ({', '.join(repr(t) for t in ACTIVITY_TYPES)})"
_TARGET_STATE_CHECK = f"state IN ({', '.join(repr(s) for s in ACTIVITY_TARGET_STATES)})"
_MENTION_HANDLE_RE = re.compile(r"^@?[a-zA-Z0-9_.\-]+(@[a-zA-Z0-9.\-]+)?$")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_entity_type_entity_id_published_at", "entity_type", "entity_id", "published_at"),
        Index("ix_activities_entity_type_entity_id_activity_type", "entity_type", "entity_id", "activity_type"),
        Index("ix_activities_owner_user_id_published_at", "owner_user_id", "published_at"),
        UniqueConstraint("source_type", "source_id", name="uq_activities_source_type_source_id"),
        UniqueConstraint("local_object_id", name="uq_activities_local_object_id"),
        CheckConstraint(_ENTITY_TYPE_CHECK, name="ck_activities_entity_type"),
        CheckConstraint(_ACTIVITY_TYPE_CHECK, name="ck_activities_activity_type"),
    )

    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    activity_type: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(32))
    source_actor: Mapped[str] = mapped_column(String(512))
    source_id: Mapped[str] = mapped_column(String(512))
    local_object_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    visibility: Mapped[str] = mapped_column(String(16))
    in_reply_to_activity_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("activities.id", ondelete="SET NULL"),
        nullable=True,
    )
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        default="text/plain",
        insert_default="text/plain",
        server_default="text/plain",
    )
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    retracted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        insert_default=False,
        server_default="0",
    )

    mentions: Mapped[List["ActivityMention"]] = relationship(
        "ActivityMention",
        back_populates="activity",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    targets: Mapped[List["ActivityTarget"]] = relationship(
        "ActivityTarget",
        back_populates="activity",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    tags: Mapped[List["ActivityTag"]] = relationship(
        "ActivityTag",
        back_populates="activity",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    in_reply_to_activity: Mapped[Optional["Activity"]] = relationship(
        "Activity",
        remote_side="Activity.id",
        backref="replies",
        lazy="selectin",
    )

    @validates("entity_type")
    def _validate_entity_type(self, _: str, value: str) -> str:
        if value not in ACTIVITY_ENTITY_TYPES:
            raise ValueError(f"Invalid entity_type: {value}")
        return value

    @validates("activity_type")
    def _validate_activity_type(self, _: str, value: str) -> str:
        if value not in ACTIVITY_TYPES:
            raise ValueError(f"Invalid activity_type: {value}")
        return value


class ActivityMention(Base):
    __tablename__ = "activity_mentions"
    __table_args__ = (UniqueConstraint("activity_id", "handle", name="uq_activity_mentions_activity_id_handle"),)

    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        index=True,
    )
    handle: Mapped[str] = mapped_column(String(256))
    actor_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    notified_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)

    activity: Mapped["Activity"] = relationship("Activity", back_populates="mentions")

    @validates("handle")
    def _validate_handle(self, _: str, value: str) -> str:
        if value is not None and not _MENTION_HANDLE_RE.match(value):
            raise ValueError(f"Invalid mention handle: {value}")
        return value


class ActivityTag(Base):
    """Association between an activity and a hashtag found in its content."""

    __tablename__ = "activity_tags"
    __table_args__ = (UniqueConstraint("activity_id", "tag_id", name="uq_activity_tags_activity_id_tag_id"),)

    activity_id: Mapped[str] = mapped_column(
        ForeignKey("activities.id", ondelete="CASCADE"),
        index=True,
    )
    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"),
        index=True,
    )

    activity: Mapped["Activity"] = relationship("Activity", back_populates="tags")
    tag: Mapped["Tag"] = relationship("Tag", back_populates="activities")


class ActivityTarget(Base):
    __tablename__ = "activity_targets"
    __table_args__ = (
        UniqueConstraint("activity_id", "inbox_url", name="uq_activity_targets_activity_id_inbox_url"),
        CheckConstraint(_TARGET_STATE_CHECK, name="ck_activity_targets_state"),
    )

    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"))
    inbox_url: Mapped[str] = mapped_column(String(512))
    state: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        insert_default="pending",
        server_default="pending",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        insert_default=0,
        server_default="0",
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)

    activity: Mapped["Activity"] = relationship("Activity", back_populates="targets")

    @validates("state")
    def _validate_state(self, _: str, value: str) -> str:
        if value not in ACTIVITY_TARGET_STATES:
            raise ValueError(f"Invalid activity target state: {value}")
        return value
