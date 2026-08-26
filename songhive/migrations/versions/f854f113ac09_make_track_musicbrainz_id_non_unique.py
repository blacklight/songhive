"""make track musicbrainz_id non unique

Revision ID: f854f113ac09
Revises: 10f8d202505a
Create Date: 2026-08-26 14:25:20.218809

"""

from typing import Sequence, Union

from alembic import op

from songhive.migrations.utils import index_exists

# revision identifiers, used by Alembic.
revision: str = "f854f113ac09"
down_revision: Union[str, Sequence[str], None] = "10f8d202505a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the unique track musicbrainz_id index and recreate it as non-unique."""
    if index_exists("ix_tracks_musicbrainz_id", "tracks"):
        op.drop_index("ix_tracks_musicbrainz_id", table_name="tracks")
    op.create_index(
        op.f("ix_tracks_musicbrainz_id"),
        "tracks",
        ["musicbrainz_id"],
        unique=False,
    )


def downgrade() -> None:
    """Recreate the unique track musicbrainz_id index."""
    if index_exists("ix_tracks_musicbrainz_id", "tracks"):
        op.drop_index("ix_tracks_musicbrainz_id", table_name="tracks")
    op.create_index(
        op.f("ix_tracks_musicbrainz_id"),
        "tracks",
        ["musicbrainz_id"],
        unique=True,
    )
