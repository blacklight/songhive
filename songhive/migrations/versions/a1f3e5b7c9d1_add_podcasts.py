"""
add podcasts

Adds the ``podcasts`` table (one row per RSS/Atom feed URL, shared
instance-wide), ``podcast_episodes`` (the parsed episode catalog carrying
remote enclosure URLs — audio is streamed from the source, never copied
locally) and ``podcast_subscriptions`` (which local users follow a feed).

Revision ID: a1f3e5b7c9d1
Revises: e3f5a7b9c1d2
Create Date: 2026-09-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "a1f3e5b7c9d1"
down_revision: Union[str, Sequence[str], None] = "e3f5a7b9c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the podcast tables."""
    if not table_exists("podcasts"):
        op.create_table(
            "podcasts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("feed_url", sa.String(length=1024), nullable=False),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("author", sa.String(length=512), nullable=True),
            sa.Column("link", sa.String(length=1024), nullable=True),
            sa.Column("image_url", sa.String(length=2048), nullable=True),
            sa.Column("language", sa.String(length=32), nullable=True),
            sa.Column("categories", sa.Text(), nullable=True),
            sa.Column("explicit", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("etag", sa.String(length=256), nullable=True),
            sa.Column("last_modified", sa.String(length=256), nullable=True),
            sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("feed_url"),
        )
        op.create_index("ix_podcasts_last_fetched_at", "podcasts", ["last_fetched_at"])

    if not table_exists("podcast_episodes"):
        op.create_table(
            "podcast_episodes",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("podcast_id", sa.String(), nullable=False),
            sa.Column("guid", sa.String(length=512), nullable=False),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("link", sa.String(length=1024), nullable=True),
            sa.Column("image_url", sa.String(length=2048), nullable=True),
            sa.Column("audio_url", sa.String(length=2048), nullable=False),
            sa.Column("audio_type", sa.String(length=128), nullable=True),
            sa.Column("audio_length", sa.Integer(), nullable=True),
            sa.Column("duration_seconds", sa.Integer(), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("season_number", sa.Integer(), nullable=True),
            sa.Column("episode_number", sa.Integer(), nullable=True),
            sa.Column("episode_type", sa.String(length=32), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["podcast_id"], ["podcasts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("podcast_id", "guid", name="uq_podcast_episodes_podcast_id_guid"),
        )
        op.create_index("ix_podcast_episodes_podcast_id", "podcast_episodes", ["podcast_id"])
        op.create_index(
            "ix_podcast_episodes_podcast_published",
            "podcast_episodes",
            ["podcast_id", "published_at"],
        )

    if not table_exists("podcast_subscriptions"):
        op.create_table(
            "podcast_subscriptions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("podcast_id", sa.String(), nullable=False),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["podcast_id"], ["podcasts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "podcast_id", name="uq_podcast_subscriptions_user_id_podcast_id"),
        )
        op.create_index("ix_podcast_subscriptions_user_id", "podcast_subscriptions", ["user_id"])
        op.create_index("ix_podcast_subscriptions_podcast_id", "podcast_subscriptions", ["podcast_id"])


def downgrade() -> None:
    """Drop the podcast tables."""
    if table_exists("podcast_subscriptions"):
        if index_exists("ix_podcast_subscriptions_podcast_id", "podcast_subscriptions"):
            op.drop_index("ix_podcast_subscriptions_podcast_id", table_name="podcast_subscriptions")
        if index_exists("ix_podcast_subscriptions_user_id", "podcast_subscriptions"):
            op.drop_index("ix_podcast_subscriptions_user_id", table_name="podcast_subscriptions")
        op.drop_table("podcast_subscriptions")
    if table_exists("podcast_episodes"):
        if index_exists("ix_podcast_episodes_podcast_published", "podcast_episodes"):
            op.drop_index("ix_podcast_episodes_podcast_published", table_name="podcast_episodes")
        if index_exists("ix_podcast_episodes_podcast_id", "podcast_episodes"):
            op.drop_index("ix_podcast_episodes_podcast_id", table_name="podcast_episodes")
        op.drop_table("podcast_episodes")
    if table_exists("podcasts"):
        if index_exists("ix_podcasts_last_fetched_at", "podcasts"):
            op.drop_index("ix_podcasts_last_fetched_at", table_name="podcasts")
        op.drop_table("podcasts")
