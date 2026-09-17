"""add activities published_at index

Revision ID: e1f2a3b4c5d6
Revises: d4a8c2f15b63
Create Date: 2026-02-14 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

from songhive.migrations.utils import index_exists

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d4a8c2f15b63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if not index_exists("ix_activities_published_at", "activities"):
        op.create_index(
            "ix_activities_published_at",
            "activities",
            ["published_at"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    if index_exists("ix_activities_published_at", "activities"):
        op.drop_index("ix_activities_published_at", table_name="activities")
