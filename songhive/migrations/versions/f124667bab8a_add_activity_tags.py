"""add activity tags

Revision ID: f124667bab8a
Revises: 321e6beef670
Create Date: 2026-09-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import table_exists

# revision identifiers, used by Alembic.
revision: str = "f124667bab8a"
down_revision: Union[str, Sequence[str], None] = "321e6beef670"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the ``activity_tags`` association table."""
    if not table_exists("activity_tags"):
        op.create_table(
            "activity_tags",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("activity_id", sa.String(), nullable=False),
            sa.Column("tag_id", sa.String(), nullable=False),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["activity_id"], ["activities.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("activity_id", "tag_id", name="uq_activity_tags_activity_id_tag_id"),
        )
        op.create_index("ix_activity_tags_activity_id", "activity_tags", ["activity_id"])
        op.create_index("ix_activity_tags_tag_id", "activity_tags", ["tag_id"])


def downgrade() -> None:
    """Drop the ``activity_tags`` association table."""
    if table_exists("activity_tags"):
        op.drop_index("ix_activity_tags_tag_id", table_name="activity_tags")
        op.drop_index("ix_activity_tags_activity_id", table_name="activity_tags")
        op.drop_table("activity_tags")
