"""make users.last_login and users.password_reset_expires_at timezone-aware

Both columns were declared as naive ``DateTime`` while the code assigns
timezone-aware UTC datetimes. On PostgreSQL the mismatch fails at bind time
(asyncpg: can't subtract offset-naive and offset-aware datetimes), which
broke ``last_login`` updates and password-reset issuance. The model uses
``TZDateTime`` (``TIMESTAMP WITH TIME ZONE``); existing naive values are
reinterpreted as UTC.

Revision ID: c1e2eae3572c
Revises: 1c3e5f7a9b2d
Create Date: 2026-09-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, table_exists
from songhive.models.base import TZDateTime

# revision identifiers, used by Alembic.
revision: str = "c1e2eae3572c"
down_revision: Union[str, Sequence[str], None] = "1c3e5f7a9b2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("last_login", "password_reset_expires_at")


def upgrade() -> None:
    """Widen the two users datetime columns to ``TIMESTAMP WITH TIME ZONE``."""
    if not table_exists("users"):
        return
    with op.batch_alter_table("users") as batch_op:
        for column in _COLUMNS:
            if column_exists("users", column):
                batch_op.alter_column(
                    column,
                    type_=TZDateTime(),
                    existing_type=sa.DateTime(),
                    existing_nullable=True,
                    postgresql_using=f"{column} AT TIME ZONE 'UTC'",
                )


def downgrade() -> None:
    """Narrow the columns back to naive ``TIMESTAMP``."""
    if not table_exists("users"):
        return
    with op.batch_alter_table("users") as batch_op:
        for column in _COLUMNS:
            if column_exists("users", column):
                batch_op.alter_column(
                    column,
                    type_=sa.DateTime(),
                    existing_type=TZDateTime(),
                    existing_nullable=True,
                    postgresql_using=f"{column} AT TIME ZONE 'UTC'",
                )
