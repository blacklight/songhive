"""add user upload quota

Adds the ``users.upload_quota`` column (bytes, nullable) controlling a
per-user upload quota override: ``NULL`` follows the instance default
(``storage.upload_quota``), ``-1`` means unlimited, any other non-negative
value is the user's byte cap. Includes the matching check constraint.

Revision ID: e8f1a3b5c7d9
Revises: b3e1a2f4c5d6
Create Date: 2026-09-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "e8f1a3b5c7d9"
down_revision: Union[str, Sequence[str], None] = "b3e1a2f4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UPLOAD_QUOTA_CHECK = "upload_quota IS NULL OR upload_quota >= -1"


def upgrade() -> None:
    """Add the ``users.upload_quota`` column and check constraint."""
    if not table_exists("users"):
        return
    if not column_exists("users", "upload_quota"):
        op.add_column(
            "users",
            sa.Column("upload_quota", sa.BigInteger(), nullable=True),
        )
    # ``recreate="auto"`` keeps SQLite on the copy-based path while Postgres
    # uses plain ``ALTER TABLE`` — recreating ``users`` would fail there since
    # dozens of foreign keys depend on ``users_pkey``.
    with op.batch_alter_table("users", recreate="auto") as batch_op:
        if not check_constraint_exists("users", "ck_users_upload_quota"):
            batch_op.create_check_constraint("ck_users_upload_quota", _UPLOAD_QUOTA_CHECK)


def downgrade() -> None:
    """Drop the ``users.upload_quota`` column and check constraint."""
    if not table_exists("users"):
        return
    with op.batch_alter_table("users", recreate="auto") as batch_op:
        if check_constraint_exists("users", "ck_users_upload_quota"):
            batch_op.drop_constraint("ck_users_upload_quota", type_="check")
        if column_exists("users", "upload_quota"):
            batch_op.drop_column("upload_quota")
