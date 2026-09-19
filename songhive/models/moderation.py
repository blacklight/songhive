"""
Moderation relationship models — user-level and admin-level.

Three tables back the Mastodon-style moderation feature set:

- ``UserModeration`` records a local user's mute or block of another
  actor (local or remote). Rows are keyed by the target's actor URL —
  the same identifier ``Activity.source_actor`` and ``Follow``
  rows carry — so remote targets need no local user record.
- ``AdminUserModeration`` records an administrator's limit or suspend
  action against a local user or remote actor. ``limit`` forces follows
  through approval and restricts fan-out to followers; ``suspend``
  cuts all interaction and hides the actor's content.
- ``InstanceModeration`` records an administrator's per-domain policy —
  ``defederate`` cuts federation both ways and hides the domain's
  actors, ``followers_only`` restricts the domain's activities to the
  local users who follow them. This is a database layer on top of the
  configured ``allowed_instances``/``blocked_instances`` lists — the
  config stays authoritative for deployment-level blocks.
"""

from typing import Optional

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from .base import Base

USER_MODERATION_MUTE = "mute"
USER_MODERATION_BLOCK = "block"
VALID_USER_MODERATION_KINDS = (USER_MODERATION_MUTE, USER_MODERATION_BLOCK)

ADMIN_ACTION_LIMIT = "limit"
ADMIN_ACTION_SUSPEND = "suspend"
VALID_ADMIN_USER_ACTIONS = (ADMIN_ACTION_LIMIT, ADMIN_ACTION_SUSPEND)

INSTANCE_ACTION_DEFEDERATE = "defederate"
INSTANCE_ACTION_FOLLOWERS_ONLY = "followers_only"
VALID_INSTANCE_ACTIONS = (INSTANCE_ACTION_DEFEDERATE, INSTANCE_ACTION_FOLLOWERS_ONLY)


class UserModeration(Base):
    """A local user's mute or block of another actor (local or remote)."""

    __tablename__ = "user_moderations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "target_actor_url",
            "kind",
            name="uq_user_moderations_user_target_kind",
        ),
        CheckConstraint(
            f"kind IN ({', '.join(repr(k) for k in VALID_USER_MODERATION_KINDS)})",
            name="ck_user_moderations_kind",
        ),
        Index("ix_user_moderations_target_actor_url", "target_actor_url"),
    )

    # The local user performing the moderation action.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # The moderated actor's ActivityPub id (local or remote). Local targets
    # without federation actors fall back to the ``urn:songhive:user:*``
    # identifier used by ``Activity.source_actor``.
    target_actor_url: Mapped[str] = mapped_column(String(512))
    # The target's user id when they are local — denormalized so follow
    # severing and owner-based activity filtering need no join.
    target_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # ``mute`` hides the target's activity for the muting user only;
    # ``block`` additionally prevents the target from seeing or
    # interacting with the blocker.
    kind: Mapped[str] = mapped_column(String(16))
    # Snapshot of the target actor's document fields for list rendering.
    actor_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    @validates("kind")
    def _validate_kind(self, key: str, value: str) -> str:
        if value not in VALID_USER_MODERATION_KINDS:
            raise ValueError(f"Invalid user moderation kind: {value!r}")
        return value


class AdminUserModeration(Base):
    """An administrator's limit or suspend of a local user or remote actor."""

    __tablename__ = "admin_user_moderations"
    __table_args__ = (
        CheckConstraint(
            f"action IN ({', '.join(repr(a) for a in VALID_ADMIN_USER_ACTIONS)})",
            name="ck_admin_user_moderations_action",
        ),
    )

    # The moderated actor's ActivityPub id — unique because an actor can
    # only hold one admin action at a time (limit upgrades to suspend).
    target_actor_url: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    # The target's user id when they are local.
    target_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # ``limit`` routes follows through approval and restricts fan-out to
    # followers; ``suspend`` severs local follow relationships, blocks
    # all interaction, and hides the actor's content.
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Snapshot of the target actor's document fields for list rendering.
    actor_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    @validates("action")
    def _validate_action(self, key: str, value: str) -> str:
        if value not in VALID_ADMIN_USER_ACTIONS:
            raise ValueError(f"Invalid admin user action: {value!r}")
        return value


class InstanceModeration(Base):
    """An administrator's per-domain moderation policy."""

    __tablename__ = "instance_moderations"
    __table_args__ = (
        CheckConstraint(
            f"action IN ({', '.join(repr(a) for a in VALID_INSTANCE_ACTIONS)})",
            name="ck_instance_moderations_action",
        ),
    )

    # Normalized remote domain — unique because a domain holds one policy.
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # ``defederate`` cuts all federation to and from the domain and hides
    # its actors; ``followers_only`` restricts the domain's activities to
    # the local users who follow them.
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    @validates("action")
    def _validate_action(self, key: str, value: str) -> str:
        if value not in VALID_INSTANCE_ACTIONS:
            raise ValueError(f"Invalid instance action: {value!r}")
        return value
