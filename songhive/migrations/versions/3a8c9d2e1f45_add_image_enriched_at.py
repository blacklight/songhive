"""add image enriched at

Revision ID: 3a8c9d2e1f45
Revises: fa56625fc487
Create Date: 2026-08-29 17:35:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists

# revision identifiers, used by Alembic.
revision: str = "3a8c9d2e1f45"
down_revision: Union[str, Sequence[str], None] = "fa56625fc487"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add image enrichment timestamp columns to artists and albums."""
    if not column_exists("artists", "image_enriched_at"):
        op.add_column(
            "artists",
            sa.Column("image_enriched_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not column_exists("albums", "cover_enriched_at"):
        op.add_column(
            "albums",
            sa.Column("cover_enriched_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    """Remove image enrichment timestamp columns."""
    if column_exists("albums", "cover_enriched_at"):
        op.drop_column("albums", "cover_enriched_at")
    if column_exists("artists", "image_enriched_at"):
        op.drop_column("artists", "image_enriched_at")
