"""add external items and track extra artists

Revision ID: 1c3e5f7a9b2d
Revises: 386c57b7574f
Create Date: 2026-09-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "1c3e5f7a9b2d"
down_revision: Union[str, Sequence[str], None] = "386c57b7574f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``external_items`` entity-reference table and the
    ``tracks.extra_artists`` column."""
    if not table_exists("external_items"):
        op.create_table(
            "external_items",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("external_library_id", sa.String(), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("provider_key", sa.String(length=512), nullable=False),
            sa.Column("track_id", sa.String(), nullable=True),
            sa.Column("album_id", sa.String(), nullable=True),
            sa.Column("artist_id", sa.String(), nullable=True),
            sa.Column("playlist_id", sa.String(), nullable=True),
            sa.Column("provider_etag", sa.String(length=128), nullable=True),
            sa.Column("provider_mtime", sa.DateTime(timezone=True), nullable=True),
            sa.Column("state", sa.String(length=16), server_default="active", nullable=False),
            sa.Column("raw_metadata", sa.JSON(), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sync_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["external_library_id"], ["external_libraries.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["album_id"], ["albums.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["artist_id"], ["artists.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["playlist_id"], ["playlists.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("external_library_id", "kind", "provider_key", name="uq_external_items_lib_kind_key"),
            sa.CheckConstraint(
                "(CASE WHEN track_id IS NOT NULL THEN 1 ELSE 0 END"
                " + CASE WHEN album_id IS NOT NULL THEN 1 ELSE 0 END"
                " + CASE WHEN artist_id IS NOT NULL THEN 1 ELSE 0 END"
                " + CASE WHEN playlist_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
                name="ck_external_items_one_entity",
            ),
        )
        op.create_index(op.f("ix_external_items_album_id"), "external_items", ["album_id"])
        op.create_index(op.f("ix_external_items_artist_id"), "external_items", ["artist_id"])
        op.create_index(
            op.f("ix_external_items_external_library_id"),
            "external_items",
            ["external_library_id"],
        )
        op.create_index(op.f("ix_external_items_kind"), "external_items", ["kind"])
        op.create_index(
            op.f("ix_external_items_lib_state"),
            "external_items",
            ["external_library_id", "state"],
        )
        op.create_index(op.f("ix_external_items_playlist_id"), "external_items", ["playlist_id"])
        op.create_index(op.f("ix_external_items_state"), "external_items", ["state"])
        op.create_index(op.f("ix_external_items_track_id"), "external_items", ["track_id"])

    if not column_exists("tracks", "extra_artists"):
        op.add_column(
            "tracks",
            sa.Column("extra_artists", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    """Drop ``tracks.extra_artists`` and the ``external_items`` table."""
    if column_exists("tracks", "extra_artists"):
        op.drop_column("tracks", "extra_artists")

    if table_exists("external_items"):
        op.drop_table("external_items")
