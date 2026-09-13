"""add status composer fields

Adds the ``language`` column to ``activities``, the ``status_content_type``
preference column to ``users``, and widens the ``activities.entity_type``
check constraint so standalone statuses can be attached to their author's
``user`` entity.

Revision ID: a1b2c3d4e5f6
Revises: fe65ee78bd44
Create Date: 2026-10-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "fe65ee78bd44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENTITY_TYPES = ("track", "album", "artist", "playlist", "library", "user")
_ENTITY_TYPES_LEGACY = ("track", "album", "artist", "playlist", "library")


def _entity_check(types: tuple) -> str:
    return f"entity_type IN ({', '.join(repr(t) for t in types)})"


def upgrade() -> None:
    """Add status composer columns and allow ``user`` activity entities."""
    if table_exists("activities"):
        if not column_exists("activities", "language"):
            op.add_column("activities", sa.Column("language", sa.String(length=35), nullable=True))
        with op.batch_alter_table("activities", recreate="always") as batch_op:
            if check_constraint_exists("activities", "ck_activities_entity_type"):
                batch_op.drop_constraint("ck_activities_entity_type", type_="check")
            batch_op.create_check_constraint("ck_activities_entity_type", _entity_check(_ENTITY_TYPES))

    if table_exists("users") and not column_exists("users", "status_content_type"):
        op.add_column(
            "users",
            sa.Column(
                "status_content_type",
                sa.String(length=64),
                nullable=False,
                server_default="text/markdown",
            ),
        )


def downgrade() -> None:
    """Remove status composer columns and the ``user`` entity type."""
    if table_exists("activities"):
        with op.batch_alter_table("activities", recreate="always") as batch_op:
            if check_constraint_exists("activities", "ck_activities_entity_type"):
                batch_op.drop_constraint("ck_activities_entity_type", type_="check")
            batch_op.create_check_constraint("ck_activities_entity_type", _entity_check(_ENTITY_TYPES_LEGACY))
        if column_exists("activities", "language"):
            op.drop_column("activities", "language")

    if table_exists("users") and column_exists("users", "status_content_type"):
        op.drop_column("users", "status_content_type")
