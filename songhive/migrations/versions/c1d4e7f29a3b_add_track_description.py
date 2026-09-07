"""add track description

Revision ID: c1d4e7f29a3b
Revises: 8018c26e341a
Create Date: 2026-02-17 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists

# revision identifiers, used by Alembic.
revision: str = "c1d4e7f29a3b"
down_revision: Union[str, Sequence[str], None] = "8018c26e341a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add a free-text description column to tracks."""
    if not column_exists("tracks", "description"):
        op.add_column("tracks", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove the track description column."""
    if column_exists("tracks", "description"):
        op.drop_column("tracks", "description")
