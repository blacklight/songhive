"""
add podcast sync tables

Adds ``podcast_sync_configs`` (per-user GPodder-compatible sync settings:
server, credentials, device id, pull/bidirectional mode, sync watermark and
last error) and ``podcast_sync_events`` (the local/remote subscription
change log the sync engine diffs against the last successful sync).

Revision ID: c4d8e2f6a1b3
Revises: b2c4d6e8f0a2
Create Date: 2026-11-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "c4d8e2f6a1b3"
down_revision: Union[str, Sequence[str], None] = "b2c4d6e8f0a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the podcast sync tables."""
    if not table_exists("podcast_sync_configs"):
        op.create_table(
            "podcast_sync_configs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("server_type", sa.String(length=16), nullable=False, server_default="gpodder"),
            sa.Column("server_url", sa.String(length=1024), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=False),
            sa.Column("password", sa.String(length=1024), nullable=True),
            sa.Column("device_id", sa.String(length=255), nullable=False, server_default="songhive"),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="pull"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_sync_timestamp", sa.Float(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", name="uq_podcast_sync_configs_user_id"),
        )
        op.create_index("ix_podcast_sync_configs_user_id", "podcast_sync_configs", ["user_id"])

    if not table_exists("podcast_sync_events"):
        op.create_table(
            "podcast_sync_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("feed_url", sa.String(length=1024), nullable=False),
            sa.Column("action", sa.String(length=16), nullable=False),
            sa.Column("origin", sa.String(length=16), nullable=False, server_default="local"),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_podcast_sync_events_user_id", "podcast_sync_events", ["user_id"])
        op.create_index(
            "ix_podcast_sync_events_user_created",
            "podcast_sync_events",
            ["user_id", "created_at"],
        )


def downgrade() -> None:
    """Drop the podcast sync tables."""
    if table_exists("podcast_sync_events"):
        if index_exists("ix_podcast_sync_events_user_created", "podcast_sync_events"):
            op.drop_index("ix_podcast_sync_events_user_created", table_name="podcast_sync_events")
        if index_exists("ix_podcast_sync_events_user_id", "podcast_sync_events"):
            op.drop_index("ix_podcast_sync_events_user_id", table_name="podcast_sync_events")
        op.drop_table("podcast_sync_events")

    if table_exists("podcast_sync_configs"):
        if index_exists("ix_podcast_sync_configs_user_id", "podcast_sync_configs"):
            op.drop_index("ix_podcast_sync_configs_user_id", table_name="podcast_sync_configs")
        op.drop_table("podcast_sync_configs")
