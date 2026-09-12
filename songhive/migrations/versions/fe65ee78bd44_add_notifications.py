"""add notifications

Revision ID: fe65ee78bd44
Revises: 8dcaed7b2490
Create Date: 2026-09-12 03:54:55.950937

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import table_exists

# revision identifiers, used by Alembic.
revision: str = "fe65ee78bd44"
down_revision: Union[str, Sequence[str], None] = "8dcaed7b2490"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOTIFICATION_TYPES = ("follow", "like", "boost", "quote", "reply", "mention", "share")
_NOTIFICATION_TYPE_CHECK = f"type IN ({', '.join(repr(t) for t in _NOTIFICATION_TYPES)})"


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the notifications and notification_preferences tables."""
    if not table_exists("notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=False),
            sa.Column("actor_url", sa.String(length=512), nullable=True),
            sa.Column("source_url", sa.String(length=1024), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("delivered_targets", sa.JSON(), nullable=True),
            sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("digest_sent_at", sa.DateTime(timezone=True), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(_NOTIFICATION_TYPE_CHECK, name="ck_notifications_type"),
        )
        op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
        op.create_index("ix_notifications_user_id_created_at", "notifications", ["user_id", "created_at"])
        op.create_index("ix_notifications_user_id_seen_at", "notifications", ["user_id", "seen_at"])

    if not table_exists("notification_preferences"):
        op.create_table(
            "notification_preferences",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=False),
            sa.Column("in_app", sa.Boolean(), server_default="1", nullable=False),
            sa.Column("email", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("email_digest", sa.Boolean(), server_default="0", nullable=False),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "type", name="uq_notification_preferences_user_id_type"),
            sa.CheckConstraint(_NOTIFICATION_TYPE_CHECK, name="ck_notification_preferences_type"),
        )
        op.create_index("ix_notification_preferences_user_id", "notification_preferences", ["user_id"])


def downgrade() -> None:
    """Drop the notification tables."""
    if table_exists("notification_preferences"):
        op.drop_table("notification_preferences")
    if table_exists("notifications"):
        op.drop_table("notifications")
