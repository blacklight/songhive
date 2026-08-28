"""
make library playlist radio owner nullable

Revision ID: 8ad99ccdcef2
Revises: 69bc219bdb87
Create Date: 2026-08-28 02:54:51.541514

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import index_exists

# revision identifiers, used by Alembic.
revision: str = "8ad99ccdcef2"
down_revision: Union[str, Sequence[str], None] = "69bc219bdb87"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("libraries", "playlists", "radios")


def _owner_id_column(table: str, name: str = "owner_id") -> sa.Column:
    """Return the nullable owner_id column with SET NULL behavior."""
    return sa.Column(
        name,
        sa.String(),
        sa.ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name=f"fk_{table}_owner_id_users",
        ),
        nullable=True,
    )


def _owner_index(table: str) -> str:
    return f"ix_{table}_owner_id"


def _make_owner_nullable(table: str) -> None:
    """Recreate a table's owner_id column as nullable with SET NULL."""
    index_name = _owner_index(table)

    # Rename the existing owner_id column to a temporary name.
    with op.batch_alter_table(table, recreate="always") as batch_op:
        if index_exists(index_name, table):
            batch_op.drop_index(index_name)
        batch_op.alter_column(
            "owner_id",
            new_column_name="owner_id_old",
            existing_type=sa.VARCHAR(),
            existing_nullable=False,
        )

    # Add a new nullable owner_id column with SET NULL behavior.
    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.add_column(_owner_id_column(table, "owner_id_new"))

    op.execute(f"UPDATE {table} SET owner_id_new = owner_id_old")

    # Replace the temporary column with the new owner_id column.
    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.drop_column("owner_id_old")
        batch_op.alter_column(
            "owner_id_new",
            new_column_name="owner_id",
            existing_type=sa.VARCHAR(),
            existing_nullable=True,
        )

    if not index_exists(index_name, table):
        op.create_index(index_name, table, ["owner_id"])


def upgrade() -> None:
    """Make library, playlist and radio owner_id nullable."""
    for table in _TABLES:
        _make_owner_nullable(table)


def _restore_cascade(table: str) -> None:
    """Restore the old CASCADE non-nullable owner_id column."""
    index_name = _owner_index(table)

    # Rename the existing nullable owner_id column to a temporary name.
    with op.batch_alter_table(table, recreate="always") as batch_op:
        if index_exists(index_name, table):
            batch_op.drop_index(index_name)
        batch_op.alter_column(
            "owner_id",
            new_column_name="owner_id_old",
            existing_type=sa.VARCHAR(),
            existing_nullable=True,
        )

    # Add a new non-nullable owner_id column with CASCADE behavior.
    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "owner_id_new",
                sa.String(),
                sa.ForeignKey(
                    "users.id",
                    ondelete="CASCADE",
                    name=f"fk_{table}_owner_id_users",
                ),
                nullable=False,
            )
        )

    op.execute(f"UPDATE {table} SET owner_id_new = owner_id_old")

    # Replace the temporary column with the new owner_id column.
    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.drop_column("owner_id_old")
        batch_op.alter_column(
            "owner_id_new",
            new_column_name="owner_id",
            existing_type=sa.VARCHAR(),
            existing_nullable=False,
        )

    if not index_exists(index_name, table):
        op.create_index(index_name, table, ["owner_id"])


def downgrade() -> None:
    """Restore the CASCADE non-nullable owner_id column."""
    for table in _TABLES:
        _restore_cascade(table)
