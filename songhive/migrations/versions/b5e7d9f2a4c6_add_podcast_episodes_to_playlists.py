"""
add podcast episodes to playlists

Makes ``playlist_tracks.track_id`` nullable and adds a nullable
``podcast_episode_id`` FK so a playlist entry is either a local track or a
podcast episode (exactly one set, enforced by ``ck_playlist_tracks_one_item``).

Revision ID: b5e7d9f2a4c6
Revises: c4d8e2f6a1b3
Create Date: 2026-09-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "b5e7d9f2a4c6"
down_revision: Union[str, Sequence[str], None] = "c4d8e2f6a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow playlist entries to reference podcast episodes."""
    if not table_exists("playlist_tracks") or not table_exists("podcast_episodes"):
        return

    with op.batch_alter_table("playlist_tracks") as batch_op:
        if not column_exists("playlist_tracks", "podcast_episode_id"):
            batch_op.add_column(sa.Column("podcast_episode_id", sa.String(), nullable=True))
            batch_op.create_index("ix_playlist_tracks_podcast_episode_id", ["podcast_episode_id"])
            batch_op.create_foreign_key(
                "fk_playlist_tracks_podcast_episode_id",
                "podcast_episodes",
                ["podcast_episode_id"],
                ["id"],
                ondelete="CASCADE",
            )
        batch_op.alter_column("track_id", existing_type=sa.String(), nullable=True)
        if not check_constraint_exists("playlist_tracks", "ck_playlist_tracks_one_item"):
            batch_op.create_check_constraint(
                "ck_playlist_tracks_one_item",
                "(track_id IS NULL) <> (podcast_episode_id IS NULL)",
            )


def downgrade() -> None:
    """Restore track-only playlist entries."""
    op.execute(sa.text("DELETE FROM playlist_tracks WHERE track_id IS NULL"))
    with op.batch_alter_table("playlist_tracks") as batch_op:
        if check_constraint_exists("playlist_tracks", "ck_playlist_tracks_one_item"):
            batch_op.drop_constraint("ck_playlist_tracks_one_item", type_="check")
        if column_exists("playlist_tracks", "podcast_episode_id"):
            batch_op.drop_constraint("fk_playlist_tracks_podcast_episode_id", type_="foreignkey")
            batch_op.drop_index("ix_playlist_tracks_podcast_episode_id")
            batch_op.drop_column("podcast_episode_id")
        batch_op.alter_column("track_id", existing_type=sa.String(), nullable=False)
