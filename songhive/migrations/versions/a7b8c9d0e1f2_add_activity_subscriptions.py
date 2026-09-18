"""add activity subscriptions

Creates the ``activity_subscriptions`` table backing the profile "bell":
each row subscribes a local user to another local user's authored
activity, producing ``activity`` notifications. Widens the ``type``
check constraints on ``notifications`` and ``notification_preferences``
to include ``activity``.

Revision ID: a7b8c9d0e1f2
Revises: f0bb5e9db79c
Create Date: 2026-09-18 17:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f0bb5e9db79c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOTIFICATION_TYPES = ("follow", "like", "boost", "quote", "reply", "mention", "share", "webmention", "activity")
_NOTIFICATION_TYPES_LEGACY = ("follow", "like", "boost", "quote", "reply", "mention", "share", "webmention")


def _type_check(types: tuple) -> str:
    return f"type IN ({', '.join(repr(t) for t in types)})"


def _reset_notification_check(table: str, constraint: str, types: tuple) -> None:
    if not table_exists(table):
        return
    with op.batch_alter_table(table, recreate="always") as batch_op:
        if check_constraint_exists(table, constraint):
            batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, _type_check(types))


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the activity_subscriptions table and allow ``activity`` notifications."""
    if not table_exists("activity_subscriptions"):
        op.create_table(
            "activity_subscriptions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("target_user_id", sa.String(), nullable=False),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "target_user_id", name="uq_activity_subscriptions_user_id_target_user_id"),
            sa.CheckConstraint("user_id <> target_user_id", name="ck_activity_subscriptions_no_self"),
        )
        op.create_index("ix_activity_subscriptions_user_id", "activity_subscriptions", ["user_id"])
        op.create_index(
            "ix_activity_subscriptions_target_user_id",
            "activity_subscriptions",
            ["target_user_id"],
        )

    _reset_notification_check("notifications", "ck_notifications_type", _NOTIFICATION_TYPES)
    _reset_notification_check(
        "notification_preferences",
        "ck_notification_preferences_type",
        _NOTIFICATION_TYPES,
    )


def downgrade() -> None:
    """Drop the activity_subscriptions table and restore the type checks."""
    if table_exists("activity_subscriptions"):
        op.drop_table("activity_subscriptions")

    _reset_notification_check("notifications", "ck_notifications_type", _NOTIFICATION_TYPES_LEGACY)
    _reset_notification_check(
        "notification_preferences",
        "ck_notification_preferences_type",
        _NOTIFICATION_TYPES_LEGACY,
    )
