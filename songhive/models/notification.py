"""
Notification, notification-preference, and activity-subscription models.

A ``Notification`` row records a single user-facing event (follow, like,
boost, quote, reply, mention, share, webmention, activity) addressed to a
local user.  The delivery
targets enabled at creation time are snapshotted on ``delivered_targets`` so
later rendering/reporting does not depend on preference changes.  ``seen_at``
drives the read/unread state and ``digest_sent_at`` tracks inclusion in the
daily email digest.

``NotificationPreference`` stores per-(user, type) delivery targets.  A
missing row means the defaults: in-app enabled, email and digest disabled.

``ActivitySubscription`` records that a local user wants an ``activity``
notification for every activity a local user or a remote actor authors —
the "bell" toggle on a user profile.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from .base import Base, TZDateTime


class NotificationType(str, Enum):
    """Supported notification kinds."""

    FOLLOW = "follow"
    LIKE = "like"
    BOOST = "boost"
    QUOTE = "quote"
    REPLY = "reply"
    MENTION = "mention"
    SHARE = "share"
    WEBMENTION = "webmention"
    ACTIVITY = "activity"
    REPORT = "report"
    DOWNLOAD = "download"


NOTIFICATION_TYPES = tuple(t.value for t in NotificationType)

_NOTIFICATION_TYPE_CHECK = f"type IN ({', '.join(repr(t) for t in NOTIFICATION_TYPES)})"

DELIVERY_TARGETS = ("in_app", "email", "email_digest")


def _validate_notification_type(value: str) -> str:
    """Reject unknown notification types; normalize to the canonical str."""
    return NotificationType(value).value


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id_seen_at", "user_id", "seen_at"),
        Index("ix_notifications_user_id_created_at", "user_id", "created_at"),
        CheckConstraint(_NOTIFICATION_TYPE_CHECK, name="ck_notifications_type"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    type: Mapped[str] = mapped_column(String(32))
    actor_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    delivered_targets: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    seen_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    digest_sent_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)

    @validates("type")
    def _check_type(self, key: str, value: str) -> str:
        return _validate_notification_type(value)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "type", name="uq_notification_preferences_user_id_type"),
        CheckConstraint(_NOTIFICATION_TYPE_CHECK, name="ck_notification_preferences_type"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    type: Mapped[str] = mapped_column(String(32))
    in_app: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="1",
    )
    email: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )
    email_digest: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
    )

    @validates("type")
    def _check_type(self, key: str, value: str) -> str:
        return _validate_notification_type(value)


class ActivitySubscription(Base):
    """
    A local user's subscription to another actor's activity.

    Each row produces an ``activity`` notification for ``user_id`` whenever
    the target authors a new activity the subscriber may view. Local users
    are referenced through ``target_user_id``; remote actors — which have
    no ``users`` row — through ``target_actor_url``. Exactly one of the
    two is set per row.
    """

    __tablename__ = "activity_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "target_user_id", name="uq_activity_subscriptions_user_id_target_user_id"),
        UniqueConstraint("user_id", "target_actor_url", name="uq_activity_subscriptions_user_id_target_actor_url"),
        CheckConstraint("user_id <> target_user_id", name="ck_activity_subscriptions_no_self"),
        CheckConstraint(
            "(target_user_id IS NULL) <> (target_actor_url IS NULL)",
            name="ck_activity_subscriptions_one_target",
        ),
        Index("ix_activity_subscriptions_target_user_id", "target_user_id"),
        Index("ix_activity_subscriptions_target_actor_url", "target_actor_url"),
    )

    # The local user receiving the notifications.
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    # The local user whose authored activities trigger the notifications.
    target_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    # The remote actor whose materialized activities trigger the notifications.
    target_actor_url: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )


class PushSubscription(Base):
    """A browser push subscription for a local user."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "endpoint", name="uq_push_subscriptions_user_id_endpoint"),
        Index("ix_push_subscriptions_user_id", "user_id"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    endpoint: Mapped[str] = mapped_column(String(1024))
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
