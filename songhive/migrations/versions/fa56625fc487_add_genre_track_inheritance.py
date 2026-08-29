"""add genre track inheritance

Revision ID: fa56625fc487
Revises: e4307588dfd3
Create Date: 2026-08-29 17:34:16.506093

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists

# revision identifiers, used by Alembic.
revision: str = "fa56625fc487"
down_revision: Union[str, Sequence[str], None] = "e4307588dfd3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the ``inherited`` flag to ``genre_tracks``."""
    if not column_exists("genre_tracks", "inherited"):
        op.add_column(
            "genre_tracks",
            sa.Column(
                "inherited",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    """Remove the ``inherited`` flag from ``genre_tracks``."""
    if column_exists("genre_tracks", "inherited"):
        op.drop_column("genre_tracks", "inherited")
