"""
Mention record model: the permanent per-user archive backing ``/mentions``.

A ``MentionRecord`` row is written for every local user an activity
mentions, regardless of notification delivery preferences — mention
notifications can be dismissed or purged, but the record stays so the
mentions page can always list every activity that ever addressed the user.
``source`` identifies the pipeline that produced the mention (``local``
activities authored here, ``activitypub`` objects received through the
federated inbox, ``webmention`` entries materialized from incoming
Webmentions).  ``source_url`` is the mentioning object's stable identifier
(the local or remote object id, or the deterministic
``urn:songhive:webmention:<hash>`` of a materialized Webmention) and — with
``user_id`` and ``source`` — keys the dedup constraint so re-delivery and
edits update the row instead of duplicating it.  ``activity_id`` links the
materialized ``Activity`` row when one exists (local activities, remote
replies/quotes stored locally, Webmention activities); ``payload``
snapshots the same render fields the matching notification carries so
remote notes never materialized still render a card.  Retractions delete
the row; edits merge into ``payload``.
"""

from enum import Enum
from typing import Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from .base import Base


class MentionSource(str, Enum):
    """Pipelines a mention record can originate from."""

    LOCAL = "local"
    ACTIVITYPUB = "activitypub"
    WEBMENTION = "webmention"


MENTION_SOURCES = tuple(s.value for s in MentionSource)

_MENTION_SOURCE_CHECK = f"source IN ({', '.join(repr(s) for s in MENTION_SOURCES)})"


class MentionRecord(Base):
    __tablename__ = "mention_records"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "source_url", name="uq_mention_records_user_id_source_url"),
        Index("ix_mention_records_user_id_created_at", "user_id", "created_at"),
        CheckConstraint(_MENTION_SOURCE_CHECK, name="ck_mention_records_source"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    source: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str] = mapped_column(String(1024))
    activity_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("activities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    visibility: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    @validates("source")
    def _check_source(self, key: str, value: str) -> str:
        return MentionSource(value).value
