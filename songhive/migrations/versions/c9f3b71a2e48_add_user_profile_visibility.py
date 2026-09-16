"""add user profile visibility

Adds the ``users.profile_visibility`` column (``public``, ``local`` or
``private``; default ``public``) controlling whether a profile is listed in
the users directory, plus the matching check constraint.

Revision ID: c9f3b71a2e48
Revises: b7e2f91a3c45
Create Date: 2026-09-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "c9f3b71a2e48"
down_revision: Union[str, Sequence[str], None] = "b7e2f91a3c45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROFILE_VISIBILITIES = ("public", "local", "private")
_PROFILE_VISIBILITY_CHECK = f"profile_visibility IN ({', '.join(repr(v) for v in _PROFILE_VISIBILITIES)})"


def upgrade() -> None:
    """Add the ``users.profile_visibility`` column and check constraint."""
    if not table_exists("users"):
        return
    if not column_exists("users", "profile_visibility"):
        op.add_column(
            "users",
            sa.Column(
                "profile_visibility",
                sa.String(length=16),
                nullable=False,
                server_default="public",
            ),
        )
    with op.batch_alter_table("users", recreate="always") as batch_op:
        if not check_constraint_exists("users", "ck_users_profile_visibility"):
            batch_op.create_check_constraint("ck_users_profile_visibility", _PROFILE_VISIBILITY_CHECK)


def downgrade() -> None:
    """Drop the ``users.profile_visibility`` column and check constraint."""
    if not table_exists("users"):
        return
    with op.batch_alter_table("users", recreate="always") as batch_op:
        if check_constraint_exists("users", "ck_users_profile_visibility"):
            batch_op.drop_constraint("ck_users_profile_visibility", type_="check")
        if column_exists("users", "profile_visibility"):
            batch_op.drop_column("profile_visibility")
