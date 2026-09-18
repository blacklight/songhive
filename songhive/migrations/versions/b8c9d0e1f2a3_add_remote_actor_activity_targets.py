"""add remote actor activity subscription targets

Extends ``activity_subscriptions`` with a ``target_actor_url`` column so a
subscription can point at a remote ActivityPub actor instead of a local
user, and relaxes ``target_user_id`` to nullable. Exactly one of the two
targets is set per row, enforced by ``ck_activity_subscriptions_one_target``.

Databases that applied ``a7b8c9d0e1f2`` before remote-actor support have
the local-only table — they get the new column, constraints and index via
a batch rebuild. Databases already carrying the final shape (fresh
``create_all`` installs stamped at head) skip every guarded step.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-19 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Add remote-actor targeting to activity_subscriptions."""
    if not table_exists("activity_subscriptions"):
        op.create_table(
            "activity_subscriptions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("target_user_id", sa.String(), nullable=True),
            sa.Column("target_actor_url", sa.String(length=512), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "target_user_id", name="uq_activity_subscriptions_user_id_target_user_id"),
            sa.UniqueConstraint(
                "user_id",
                "target_actor_url",
                name="uq_activity_subscriptions_user_id_target_actor_url",
            ),
            sa.CheckConstraint("user_id <> target_user_id", name="ck_activity_subscriptions_no_self"),
            sa.CheckConstraint(
                "(target_user_id IS NULL) <> (target_actor_url IS NULL)",
                name="ck_activity_subscriptions_one_target",
            ),
        )
        op.create_index("ix_activity_subscriptions_user_id", "activity_subscriptions", ["user_id"])
        op.create_index(
            "ix_activity_subscriptions_target_user_id",
            "activity_subscriptions",
            ["target_user_id"],
        )
        op.create_index(
            "ix_activity_subscriptions_target_actor_url",
            "activity_subscriptions",
            ["target_actor_url"],
        )
        return

    if column_exists("activity_subscriptions", "target_actor_url"):
        return

    # Local-only table from a7b8c9d0e1f2: rebuild to add the actor target,
    # relax target_user_id, and attach the remote-target constraints.
    with op.batch_alter_table("activity_subscriptions", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("target_actor_url", sa.String(length=512), nullable=True))
        batch_op.alter_column("target_user_id", existing_type=sa.String(), nullable=True)
        batch_op.create_unique_constraint(
            "uq_activity_subscriptions_user_id_target_actor_url",
            ["user_id", "target_actor_url"],
        )
        batch_op.create_check_constraint(
            "ck_activity_subscriptions_one_target",
            "(target_user_id IS NULL) <> (target_actor_url IS NULL)",
        )
    if not index_exists("ix_activity_subscriptions_target_actor_url", "activity_subscriptions"):
        op.create_index(
            "ix_activity_subscriptions_target_actor_url",
            "activity_subscriptions",
            ["target_actor_url"],
        )


def downgrade() -> None:
    """Drop remote-actor targeting from activity_subscriptions."""
    if not table_exists("activity_subscriptions") or not column_exists("activity_subscriptions", "target_actor_url"):
        return

    # Remote-target rows cannot survive the NOT NULL restoration.
    op.execute("DELETE FROM activity_subscriptions WHERE target_user_id IS NULL")
    if index_exists("ix_activity_subscriptions_target_actor_url", "activity_subscriptions"):
        op.drop_index("ix_activity_subscriptions_target_actor_url", table_name="activity_subscriptions")
    with op.batch_alter_table("activity_subscriptions", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_activity_subscriptions_user_id_target_actor_url", type_="unique")
        batch_op.drop_constraint("ck_activity_subscriptions_one_target", type_="check")
        batch_op.alter_column("target_user_id", existing_type=sa.String(), nullable=False)
        batch_op.drop_column("target_actor_url")
