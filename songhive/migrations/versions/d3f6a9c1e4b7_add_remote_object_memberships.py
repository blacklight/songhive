"""
add remote object memberships

Adds a nullable ``remote_object_id`` FK to ``favorites``,
``playlist_tracks`` and ``library_tracks`` so cached remote objects can be
favorited and added to local playlists/libraries without being copied into
the local catalog tables. Each row must reference exactly one entity:

- ``favorites``: ``track_id`` XOR ``remote_object_id``
- ``playlist_tracks``: exactly one of ``track_id`` / ``podcast_episode_id`` /
  ``remote_object_id``
- ``library_tracks``: ``track_id`` XOR ``remote_object_id``

Revision ID: d3f6a9c1e4b7
Revises: c4d9e1f2a3b5
Create Date: 2026-10-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "d3f6a9c1e4b7"
down_revision: Union[str, Sequence[str], None] = "c4d9e1f2a3b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow remote objects in favorites, playlists and libraries."""
    if table_exists("favorites") and table_exists("remote_objects"):
        with op.batch_alter_table("favorites") as batch_op:
            if not column_exists("favorites", "remote_object_id"):
                batch_op.add_column(sa.Column("remote_object_id", sa.String(), nullable=True))
                batch_op.create_index("ix_favorites_remote_object_id", ["remote_object_id"])
                batch_op.create_foreign_key(
                    "fk_favorites_remote_object_id",
                    "remote_objects",
                    ["remote_object_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
                batch_op.create_unique_constraint("uq_user_remote_favorite", ["user_id", "remote_object_id"])
            batch_op.alter_column("track_id", existing_type=sa.String(), nullable=True)
            if not check_constraint_exists("favorites", "ck_favorites_one_item"):
                batch_op.create_check_constraint(
                    "ck_favorites_one_item",
                    "(track_id IS NULL) <> (remote_object_id IS NULL)",
                )

    if table_exists("playlist_tracks") and table_exists("remote_objects"):
        with op.batch_alter_table("playlist_tracks") as batch_op:
            if not column_exists("playlist_tracks", "remote_object_id"):
                batch_op.add_column(sa.Column("remote_object_id", sa.String(), nullable=True))
                batch_op.create_index("ix_playlist_tracks_remote_object_id", ["remote_object_id"])
                batch_op.create_foreign_key(
                    "fk_playlist_tracks_remote_object_id",
                    "remote_objects",
                    ["remote_object_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
            if check_constraint_exists("playlist_tracks", "ck_playlist_tracks_one_item"):
                batch_op.drop_constraint("ck_playlist_tracks_one_item", type_="check")
            batch_op.create_check_constraint(
                "ck_playlist_tracks_one_item",
                "(CASE WHEN track_id IS NOT NULL THEN 1 ELSE 0 END"
                " + CASE WHEN podcast_episode_id IS NOT NULL THEN 1 ELSE 0 END"
                " + CASE WHEN remote_object_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            )

    if table_exists("library_tracks") and table_exists("remote_objects"):
        with op.batch_alter_table("library_tracks") as batch_op:
            if not column_exists("library_tracks", "remote_object_id"):
                batch_op.add_column(sa.Column("remote_object_id", sa.String(), nullable=True))
                batch_op.create_index("ix_library_tracks_remote_object_id", ["remote_object_id"])
                batch_op.create_foreign_key(
                    "fk_library_tracks_remote_object_id",
                    "remote_objects",
                    ["remote_object_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
                batch_op.create_unique_constraint("uq_library_remote_track", ["library_id", "remote_object_id"])
            batch_op.alter_column("track_id", existing_type=sa.String(), nullable=True)
            if not check_constraint_exists("library_tracks", "ck_library_tracks_one_item"):
                batch_op.create_check_constraint(
                    "ck_library_tracks_one_item",
                    "(track_id IS NULL) <> (remote_object_id IS NULL)",
                )


def downgrade() -> None:
    """Remove remote object membership columns."""
    op.execute(sa.text("DELETE FROM favorites WHERE remote_object_id IS NOT NULL"))
    op.execute(sa.text("DELETE FROM playlist_tracks WHERE remote_object_id IS NOT NULL"))
    op.execute(sa.text("DELETE FROM library_tracks WHERE remote_object_id IS NOT NULL"))

    with op.batch_alter_table("favorites") as batch_op:
        if check_constraint_exists("favorites", "ck_favorites_one_item"):
            batch_op.drop_constraint("ck_favorites_one_item", type_="check")
        if column_exists("favorites", "remote_object_id"):
            batch_op.drop_constraint("uq_user_remote_favorite", type_="unique")
            batch_op.drop_constraint("fk_favorites_remote_object_id", type_="foreignkey")
            batch_op.drop_index("ix_favorites_remote_object_id")
            batch_op.drop_column("remote_object_id")
        batch_op.alter_column("track_id", existing_type=sa.String(), nullable=False)

    with op.batch_alter_table("playlist_tracks") as batch_op:
        if check_constraint_exists("playlist_tracks", "ck_playlist_tracks_one_item"):
            batch_op.drop_constraint("ck_playlist_tracks_one_item", type_="check")
        if column_exists("playlist_tracks", "remote_object_id"):
            batch_op.drop_constraint("fk_playlist_tracks_remote_object_id", type_="foreignkey")
            batch_op.drop_index("ix_playlist_tracks_remote_object_id")
            batch_op.drop_column("remote_object_id")
        batch_op.create_check_constraint(
            "ck_playlist_tracks_one_item",
            "(track_id IS NULL) <> (podcast_episode_id IS NULL)",
        )

    with op.batch_alter_table("library_tracks") as batch_op:
        if check_constraint_exists("library_tracks", "ck_library_tracks_one_item"):
            batch_op.drop_constraint("ck_library_tracks_one_item", type_="check")
        if column_exists("library_tracks", "remote_object_id"):
            batch_op.drop_constraint("uq_library_remote_track", type_="unique")
            batch_op.drop_constraint("fk_library_tracks_remote_object_id", type_="foreignkey")
            batch_op.drop_index("ix_library_tracks_remote_object_id")
            batch_op.drop_column("remote_object_id")
        batch_op.alter_column("track_id", existing_type=sa.String(), nullable=False)
