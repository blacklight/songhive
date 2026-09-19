"""add report forwarding and report notifications

Adds ``target_actor_url`` and ``forwarded`` to ``reports`` so actor
reports carry the reported account's canonical URL and record whether a
``Flag`` activity was delivered to the remote instance. Widens the
``type`` check constraints on ``notifications`` and
``notification_preferences`` to include ``report``.

Revision ID: d4e6f8a0b2c4
Revises: c3d5e7f9a1b2
Create Date: 2026-10-05 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "d4e6f8a0b2c4"
down_revision: Union[str, Sequence[str], None] = "c3d5e7f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOTIFICATION_TYPES = (
    "follow",
    "like",
    "boost",
    "quote",
    "reply",
    "mention",
    "share",
    "webmention",
    "activity",
    "report",
)
_NOTIFICATION_TYPES_LEGACY = tuple(t for t in _NOTIFICATION_TYPES if t != "report")


def _type_check(types: tuple) -> str:
    return f"type IN ({', '.join(repr(t) for t in types)})"


def _reset_notification_check(table: str, constraint: str, types: tuple) -> None:
    if not table_exists(table):
        return
    with op.batch_alter_table(table, recreate="always") as batch_op:
        if check_constraint_exists(table, constraint):
            batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, _type_check(types))


def upgrade() -> None:
    """Add report forwarding columns and allow ``report`` notifications."""
    if table_exists("reports"):
        if not column_exists("reports", "target_actor_url"):
            op.add_column("reports", sa.Column("target_actor_url", sa.String(length=512), nullable=True))
            op.create_index("ix_reports_target_actor_url", "reports", ["target_actor_url"])
        if not column_exists("reports", "forwarded"):
            op.add_column(
                "reports",
                sa.Column("forwarded", sa.Boolean(), nullable=False, server_default=sa.false()),
            )
        # Remote actor URLs exceed the original 64-char ``target_id``.
        with op.batch_alter_table("reports", recreate="always") as batch_op:
            batch_op.alter_column("target_id", type_=sa.String(length=512))

    _reset_notification_check("notifications", "ck_notifications_type", _NOTIFICATION_TYPES)
    _reset_notification_check(
        "notification_preferences",
        "ck_notification_preferences_type",
        _NOTIFICATION_TYPES,
    )


def downgrade() -> None:
    """Remove report forwarding columns and restore the type checks."""
    if table_exists("reports"):
        if column_exists("reports", "forwarded"):
            op.drop_column("reports", "forwarded")
        if column_exists("reports", "target_actor_url"):
            op.drop_index("ix_reports_target_actor_url", table_name="reports")
            op.drop_column("reports", "target_actor_url")
        with op.batch_alter_table("reports", recreate="always") as batch_op:
            batch_op.alter_column("target_id", type_=sa.String(length=64))

    _reset_notification_check("notifications", "ck_notifications_type", _NOTIFICATION_TYPES_LEGACY)
    _reset_notification_check(
        "notification_preferences",
        "ck_notification_preferences_type",
        _NOTIFICATION_TYPES_LEGACY,
    )
