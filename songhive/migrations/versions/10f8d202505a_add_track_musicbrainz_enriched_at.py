"""add track musicbrainz enriched at

Revision ID: 10f8d202505a
Revises: 194ec56009fe
Create Date: 2026-08-26 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists

# revision identifiers, used by Alembic.
revision: str = "10f8d202505a"
down_revision: Union[str, Sequence[str], None] = "194ec56009fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if not column_exists("tracks", "musicbrainz_enriched_at"):
        op.add_column(
            "tracks",
            sa.Column("musicbrainz_enriched_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    """Downgrade schema."""
    if column_exists("tracks", "musicbrainz_enriched_at"):
        op.drop_column("tracks", "musicbrainz_enriched_at")
