"""
Local (outbound) follow relationships.

A ``Follow`` row records that a local user follows — or has requested to
follow — another actor, whether that actor lives on this instance or on a
remote one. Pubby's ``federation_followers``/``federation_follow_requests``
tables track the *inbound* side (who follows our actors); this table is the
mirroring *outbound* side and drives:

- the ``/{username}/follows`` profile listing,
- remote-activity admission (only actors followed by a local user get their
  inbound activities materialized),
- outbound ``Undo(Follow)`` reconstruction (``activity_id`` keeps the id of
  the ``Follow`` activity we sent so the ``Undo`` object can be rebuilt).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from .base import Base, TZDateTime

FOLLOW_STATE_PENDING = "pending"
FOLLOW_STATE_ACCEPTED = "accepted"
VALID_FOLLOW_STATES = (FOLLOW_STATE_PENDING, FOLLOW_STATE_ACCEPTED)


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (
        UniqueConstraint("user_id", "target_actor_url", name="uq_follows_user_id_target_actor_url"),
        CheckConstraint(
            f"state IN ({', '.join(repr(s) for s in VALID_FOLLOW_STATES)})",
            name="ck_follows_state",
        ),
        Index("ix_follows_target_actor_url", "target_actor_url"),
    )

    # The local user performing the follow.
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Denormalized actor URL of the local user — used to match inbound
    # ``Accept``/``Reject`` activities back to this row.
    actor_url: Mapped[str] = mapped_column(String(512))
    # The followed actor's ActivityPub id (local or remote).
    target_actor_url: Mapped[str] = mapped_column(String(512))
    # Local target's user id when the followed actor lives on this instance.
    target_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    # ``pending`` until the target's instance sends ``Accept(Follow)`` (or,
    # for local targets, until the owner approves the request); remote
    # follows stay ``pending`` until the remote decision arrives.
    state: Mapped[str] = mapped_column(String(16), default=FOLLOW_STATE_PENDING)
    # Id of the ``Follow`` activity sent to the remote inbox — needed to
    # rebuild the embedded object inside a later ``Undo(Follow)``.
    activity_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Snapshot of the followed actor's document fields for card rendering.
    actor_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Remote inbox used for delivery — retained so ``Undo(Follow)`` can be
    # sent without re-fetching the actor document.
    inbox_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)

    @validates("state")
    def _validate_state(self, key: str, value: str) -> str:
        if value not in VALID_FOLLOW_STATES:
            raise ValueError(f"Invalid follow state: {value!r}")
        return value
