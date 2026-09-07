"""add activities

Revision ID: 4adb5fbea9d6
Revises: c1d4e7f29a3b
Create Date: 2026-09-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import table_exists

# revision identifiers, used by Alembic.
revision: str = "4adb5fbea9d6"
down_revision: Union[str, Sequence[str], None] = "c1d4e7f29a3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVITY_ENTITY_TYPES = ("track", "album", "artist", "playlist", "library")
_ACTIVITY_TYPES = (
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
_ACTIVITY_TARGET_STATES = ("pending", "sent", "failed", "skipped")


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def _uuid_sql_expression() -> str:
    """Return a dialect-specific SQL expression that mints a UUID4 text value."""
    bind = op.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name == "sqlite":
        # Build an 8-4-4-4-12 hexadecimal UUID from random bytes; there is no
        # built-in uuid function in SQLite.
        return (
            "lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' || "
            "lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(2))) || '-' || "
            "lower(hex(randomblob(6)))"
        )
    return "gen_random_uuid()::text"


def _backfill_track_activities() -> None:
    """Insert a ``create`` activity for every track already published to the fediverse."""
    id_expr = _uuid_sql_expression()
    op.execute(
        sa.text(
            f"""
            INSERT INTO activities (
                id, entity_type, entity_id, activity_type, source_type,
                source_actor, source_id, local_object_id, owner_user_id,
                visibility, published_at, created_at, updated_at
            )
            SELECT
                {id_expr},
                'track',
                t.id,
                'create',
                'local',
                u.actor_url,
                u.actor_url || '/objects/' || t.federation_object_id,
                t.federation_object_id,
                t.owner_id,
                'public',
                COALESCE(t.metadata_updated_at, CURRENT_TIMESTAMP),
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM tracks t
            JOIN users u ON t.owner_id = u.id
            WHERE t.federation_object_id IS NOT NULL
              AND u.actor_url IS NOT NULL
            """
        )
    )


def upgrade() -> None:
    """Create the activities, activity_mentions, and activity_targets tables."""
    created_activities = not table_exists("activities")
    if created_activities:
        op.create_table(
            "activities",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("entity_type", sa.String(length=32), nullable=False),
            sa.Column("entity_id", sa.String(length=64), nullable=False),
            sa.Column("activity_type", sa.String(length=32), nullable=False),
            sa.Column("source_type", sa.String(length=32), nullable=False),
            sa.Column("source_actor", sa.String(length=512), nullable=False),
            sa.Column("source_id", sa.String(length=512), nullable=False),
            sa.Column("local_object_id", sa.String(length=64), nullable=True),
            sa.Column("owner_user_id", sa.String(), nullable=True),
            sa.Column("visibility", sa.String(length=16), nullable=False),
            sa.Column("in_reply_to_activity_id", sa.String(), nullable=True),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("content_source", sa.Text(), nullable=True),
            sa.Column("content_type", sa.String(length=64), server_default="text/plain", nullable=True),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("retracted", sa.Boolean(), server_default="0", nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["in_reply_to_activity_id"], ["activities.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_type", "source_id", name="uq_activities_source_type_source_id"),
            sa.UniqueConstraint("local_object_id", name="uq_activities_local_object_id"),
            sa.CheckConstraint(
                f"entity_type IN ({', '.join(repr(t) for t in _ACTIVITY_ENTITY_TYPES)})",
                name="ck_activities_entity_type",
            ),
            sa.CheckConstraint(
                f"activity_type IN ({', '.join(repr(t) for t in _ACTIVITY_TYPES)})",
                name="ck_activities_activity_type",
            ),
        )
        op.create_index(
            "ix_activities_entity_type_entity_id_published_at",
            "activities",
            ["entity_type", "entity_id", "published_at"],
        )
        op.create_index(
            "ix_activities_entity_type_entity_id_activity_type",
            "activities",
            ["entity_type", "entity_id", "activity_type"],
        )
        op.create_index(
            "ix_activities_owner_user_id_published_at",
            "activities",
            ["owner_user_id", "published_at"],
        )

    if not table_exists("activity_mentions"):
        op.create_table(
            "activity_mentions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("activity_id", sa.String(), nullable=False),
            sa.Column("handle", sa.String(length=256), nullable=False),
            sa.Column("actor_url", sa.String(length=512), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("activity_id", "handle", name="uq_activity_mentions_activity_id_handle"),
        )
        op.create_index("ix_activity_mentions_activity_id", "activity_mentions", ["activity_id"])
        op.create_index("ix_activity_mentions_user_id", "activity_mentions", ["user_id"])

    if not table_exists("activity_targets"):
        op.create_table(
            "activity_targets",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("activity_id", sa.String(), nullable=False),
            sa.Column("inbox_url", sa.String(length=512), nullable=False),
            sa.Column("state", sa.String(length=16), server_default="pending", nullable=False),
            sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("activity_id", "inbox_url", name="uq_activity_targets_activity_id_inbox_url"),
            sa.CheckConstraint(
                f"state IN ({', '.join(repr(s) for s in _ACTIVITY_TARGET_STATES)})",
                name="ck_activity_targets_state",
            ),
        )

    if created_activities:
        _backfill_track_activities()


def downgrade() -> None:
    """Drop the activity tables."""
    if table_exists("activity_targets"):
        op.drop_table("activity_targets")
    if table_exists("activity_mentions"):
        op.drop_table("activity_mentions")
    if table_exists("activities"):
        op.drop_table("activities")
