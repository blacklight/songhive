"""add user followers approval

Adds the ``users.followers_approval`` column (``accept``, ``manual`` or
``reject``; default ``accept``) controlling how new follower requests are
handled, plus the matching check constraint.

Revision ID: d4a8c2f15b63
Revises: c9f3b71a2e48
Create Date: 2026-10-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "d4a8c2f15b63"
down_revision: Union[str, Sequence[str], None] = "c9f3b71a2e48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FOLLOWERS_APPROVALS = ("accept", "manual", "reject")
_FOLLOWERS_APPROVAL_CHECK = f"followers_approval IN ({', '.join(repr(v) for v in _FOLLOWERS_APPROVALS)})"


def upgrade() -> None:
    """Add the ``users.followers_approval`` column and check constraint."""
    if not table_exists("users"):
        return
    if not column_exists("users", "followers_approval"):
        op.add_column(
            "users",
            sa.Column(
                "followers_approval",
                sa.String(length=16),
                nullable=False,
                server_default="accept",
            ),
        )
    with op.batch_alter_table("users", recreate="always") as batch_op:
        if not check_constraint_exists("users", "ck_users_followers_approval"):
            batch_op.create_check_constraint("ck_users_followers_approval", _FOLLOWERS_APPROVAL_CHECK)


def downgrade() -> None:
    """Drop the ``users.followers_approval`` column and check constraint."""
    if not table_exists("users"):
        return
    with op.batch_alter_table("users", recreate="always") as batch_op:
        if check_constraint_exists("users", "ck_users_followers_approval"):
            batch_op.drop_constraint("ck_users_followers_approval", type_="check")
        if column_exists("users", "followers_approval"):
            batch_op.drop_column("followers_approval")
