"""
add podcast episode plays

Adds the ``podcast_episode_plays`` table recording which episodes a user
has played — the basis for the per-podcast unplayed counters shown on the
/podcasts page. Episodes stream from remote enclosure URLs and never become
``tracks`` rows, so they cannot reuse ``listening_history``.

Revision ID: b2c4d6e8f0a2
Revises: a1f3e5b7c9d1
Create Date: 2026-10-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "b2c4d6e8f0a2"
down_revision: Union[str, Sequence[str], None] = "a1f3e5b7c9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the podcast_episode_plays table."""
    if not table_exists("podcast_episode_plays"):
        op.create_table(
            "podcast_episode_plays",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("episode_id", sa.String(), nullable=False),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["episode_id"], ["podcast_episodes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "episode_id", name="uq_podcast_episode_plays_user_episode"),
        )
        op.create_index("ix_podcast_episode_plays_user_id", "podcast_episode_plays", ["user_id"])
        op.create_index("ix_podcast_episode_plays_episode_id", "podcast_episode_plays", ["episode_id"])


def downgrade() -> None:
    """Drop the podcast_episode_plays table."""
    if table_exists("podcast_episode_plays"):
        if index_exists("ix_podcast_episode_plays_episode_id", "podcast_episode_plays"):
            op.drop_index("ix_podcast_episode_plays_episode_id", table_name="podcast_episode_plays")
        if index_exists("ix_podcast_episode_plays_user_id", "podcast_episode_plays"):
            op.drop_index("ix_podcast_episode_plays_user_id", table_name="podcast_episode_plays")
        op.drop_table("podcast_episode_plays")
