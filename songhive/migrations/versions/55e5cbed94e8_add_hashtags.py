"""add hashtags

Revision ID: 55e5cbed94e8
Revises: 8ad99ccdcef2
Create Date: 2026-08-28 18:11:01.096286

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "55e5cbed94e8"
down_revision: Union[str, Sequence[str], None] = "8ad99ccdcef2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str, on_update: bool = False) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    kwargs: dict = {
        "nullable": False,
        "server_default": sa.text("now()"),
    }
    if on_update:
        kwargs["onupdate"] = sa.text("now()")
    return sa.Column(name, sa.DateTime(timezone=True), **kwargs)


def _entity_association_table(table: str, entity_column: str, entity_table: str) -> None:
    """Create one of the five hashtag-entity association tables."""
    op.create_table(
        table,
        sa.Column("id", sa.String(), nullable=False),
        sa.Column(
            "hashtag_id",
            sa.String(),
            sa.ForeignKey("hashtags.id", name=f"fk_{table}_hashtag_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            entity_column,
            sa.String(),
            sa.ForeignKey(f"{entity_table}.id", name=f"fk_{table}_{entity_column}", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", name=f"fk_{table}_user_id", ondelete="SET NULL"),
            nullable=True,
        ),
        _timestamp_column("created_at"),
        _timestamp_column("updated_at", on_update=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hashtag_id", entity_column, name=f"uq_{table}"),
    )
    op.create_index(f"ix_{table}_hashtag_id", table, ["hashtag_id"])
    op.create_index(f"ix_{table}_{entity_column}", table, [entity_column])


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "hashtags",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        _timestamp_column("created_at"),
        _timestamp_column("updated_at", on_update=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hashtags_name", "hashtags", ["name"], unique=True)

    _entity_association_table("hashtag_tracks", "track_id", "tracks")
    _entity_association_table("hashtag_albums", "album_id", "albums")
    _entity_association_table("hashtag_artists", "artist_id", "artists")
    _entity_association_table("hashtag_playlists", "playlist_id", "playlists")
    _entity_association_table("hashtag_libraries", "library_id", "libraries")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_hashtags_name", table_name="hashtags")
    op.drop_table("hashtag_libraries")
    op.drop_table("hashtag_playlists")
    op.drop_table("hashtag_artists")
    op.drop_table("hashtag_albums")
    op.drop_table("hashtag_tracks")
    op.drop_table("hashtags")
