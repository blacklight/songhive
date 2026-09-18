"""add follows

Adds the ``follows`` table tracking outbound follow relationships: which
local user follows (or requested to follow) which actor, whether that actor
is local or remote, and the delivery metadata needed to send a later
``Undo(Follow)``.

Revision ID: f2a9c1d4e7b8
Revises: 36d3bfe1da27
Create Date: 2026-12-22 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "f2a9c1d4e7b8"
down_revision: Union[str, Sequence[str], None] = "36d3bfe1da27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FOLLOW_STATES = ("pending", "accepted")
_FOLLOW_STATE_CHECK = f"state IN ({', '.join(repr(s) for s in _FOLLOW_STATES)})"


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the ``follows`` table."""
    if not table_exists("follows"):
        op.create_table(
            "follows",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("actor_url", sa.String(length=512), nullable=False),
            sa.Column("target_actor_url", sa.String(length=512), nullable=False),
            sa.Column("target_user_id", sa.String(), nullable=True),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("activity_id", sa.String(length=512), nullable=True),
            sa.Column("actor_data", sa.JSON(), nullable=True),
            sa.Column("inbox_url", sa.String(length=512), nullable=True),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "target_actor_url", name="uq_follows_user_id_target_actor_url"),
            sa.CheckConstraint(_FOLLOW_STATE_CHECK, name="ck_follows_state"),
        )
        op.create_index("ix_follows_user_id", "follows", ["user_id"])
        op.create_index("ix_follows_target_actor_url", "follows", ["target_actor_url"])


def downgrade() -> None:
    """Drop the ``follows`` table."""
    if table_exists("follows"):
        if index_exists("ix_follows_target_actor_url", "follows"):
            op.drop_index("ix_follows_target_actor_url", table_name="follows")
        if index_exists("ix_follows_user_id", "follows"):
            op.drop_index("ix_follows_user_id", table_name="follows")
        op.drop_table("follows")
