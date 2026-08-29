"""add genres

Revision ID: e4307588dfd3
Revises: 55e5cbed94e8
Create Date: 2026-08-29 14:18:23.424406

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4307588dfd3"
down_revision: Union[str, Sequence[str], None] = "55e5cbed94e8"
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
    """Create a genre-entity association table."""
    op.create_table(
        table,
        sa.Column("id", sa.String(), nullable=False),
        sa.Column(
            "genre_id",
            sa.String(),
            sa.ForeignKey("genres.id", name=f"fk_{table}_genre_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            entity_column,
            sa.String(),
            sa.ForeignKey(f"{entity_table}.id", name=f"fk_{table}_{entity_column}", ondelete="CASCADE"),
            nullable=False,
        ),
        _timestamp_column("created_at"),
        _timestamp_column("updated_at", on_update=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("genre_id", entity_column, name=f"uq_{table}"),
    )
    op.create_index(f"ix_{table}_genre_id", table, ["genre_id"])
    op.create_index(f"ix_{table}_{entity_column}", table, [entity_column])


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "genres",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        _timestamp_column("created_at"),
        _timestamp_column("updated_at", on_update=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_genres_name", "genres", ["name"], unique=True)

    _entity_association_table("genre_tracks", "track_id", "tracks")
    _entity_association_table("genre_albums", "album_id", "albums")

    op.add_column("albums", sa.Column("genre", sa.String(128), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("albums", "genre")
    op.drop_table("genre_albums")
    op.drop_table("genre_tracks")
    op.drop_index("ix_genres_name", table_name="genres")
    op.drop_table("genres")
