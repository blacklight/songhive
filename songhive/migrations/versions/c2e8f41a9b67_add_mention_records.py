"""add mention records

Revision ID: c2e8f41a9b67
Revises: 36d3bfe1da27
Create Date: 2026-09-18 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import table_exists

# revision identifiers, used by Alembic.
revision: str = "c2e8f41a9b67"
down_revision: Union[str, Sequence[str], None] = "36d3bfe1da27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MENTION_SOURCES = ("local", "activitypub", "webmention")
_MENTION_SOURCE_CHECK = f"source IN ({', '.join(repr(s) for s in _MENTION_SOURCES)})"


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the mention_records table backing the /mentions archive."""
    if not table_exists("mention_records"):
        op.create_table(
            "mention_records",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column("source_url", sa.String(length=1024), nullable=False),
            sa.Column("activity_id", sa.String(), nullable=True),
            sa.Column("actor_url", sa.String(length=512), nullable=True),
            sa.Column("visibility", sa.String(length=16), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "source", "source_url", name="uq_mention_records_user_id_source_url"),
            sa.CheckConstraint(_MENTION_SOURCE_CHECK, name="ck_mention_records_source"),
        )
        op.create_index("ix_mention_records_user_id", "mention_records", ["user_id"])
        op.create_index("ix_mention_records_activity_id", "mention_records", ["activity_id"])
        op.create_index("ix_mention_records_user_id_created_at", "mention_records", ["user_id", "created_at"])


def downgrade() -> None:
    """Drop the mention_records table."""
    if table_exists("mention_records"):
        op.drop_table("mention_records")
