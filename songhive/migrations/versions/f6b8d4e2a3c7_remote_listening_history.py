"""
remote listening history

Adds a nullable ``remote_object_id`` FK to ``listening_history`` so plays
of cached remote objects (Funkwhale/other instances) are recorded — and
scrobbled — without copying the remote track into the local catalog.
Each row must reference exactly one entity:

- ``listening_history``: ``track_id`` XOR ``remote_object_id``

Revision ID: f6b8d4e2a3c7
Revises: e5f7a3b9c2d4
Create Date: 2026-10-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "f6b8d4e2a3c7"
down_revision: Union[str, Sequence[str], None] = "e5f7a3b9c2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow listening-history rows to reference a remote object."""
    if table_exists("listening_history") and table_exists("remote_objects"):
        with op.batch_alter_table("listening_history") as batch_op:
            if not column_exists("listening_history", "remote_object_id"):
                batch_op.add_column(sa.Column("remote_object_id", sa.String(), nullable=True))
                batch_op.create_index("ix_listening_history_remote_object_id", ["remote_object_id"])
                batch_op.create_foreign_key(
                    "fk_listening_history_remote_object_id",
                    "remote_objects",
                    ["remote_object_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
            batch_op.alter_column("track_id", existing_type=sa.String(), nullable=True)
            if not check_constraint_exists("listening_history", "ck_listening_history_one_item"):
                batch_op.create_check_constraint(
                    "ck_listening_history_one_item",
                    "(track_id IS NULL) <> (remote_object_id IS NULL)",
                )


def downgrade() -> None:
    """Remove remote object references from listening history."""
    op.execute(sa.text("DELETE FROM listening_history WHERE remote_object_id IS NOT NULL"))

    with op.batch_alter_table("listening_history") as batch_op:
        if check_constraint_exists("listening_history", "ck_listening_history_one_item"):
            batch_op.drop_constraint("ck_listening_history_one_item", type_="check")
        if column_exists("listening_history", "remote_object_id"):
            batch_op.drop_constraint("fk_listening_history_remote_object_id", type_="foreignkey")
            batch_op.drop_index("ix_listening_history_remote_object_id")
            batch_op.drop_column("remote_object_id")
        batch_op.alter_column("track_id", existing_type=sa.String(), nullable=False)
