"""add collection items

Adds the ``collection_items`` table recording which pieces of content
(libraries, playlists, albums, artists, tracks, radios) a user has
explicitly saved to their own collection.  Saved items appear alongside
owned content whenever a list is filtered by ``collection=1``.

Revision ID: e3f5a7b9c1d2
Revises: d4e6f8a0b2c4
Create Date: 2026-09-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "e3f5a7b9c1d2"
down_revision: Union[str, Sequence[str], None] = "d4e6f8a0b2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the ``collection_items`` table."""
    if not table_exists("collection_items"):
        op.create_table(
            "collection_items",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("item_type", sa.String(length=32), nullable=False),
            sa.Column("item_id", sa.String(length=36), nullable=False),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "item_type", "item_id", name="uq_collection_item"),
        )
        op.create_index("ix_collection_items_user_id", "collection_items", ["user_id"])
        op.create_index("ix_collection_items_item", "collection_items", ["item_type", "item_id"])


def downgrade() -> None:
    """Drop the ``collection_items`` table."""
    if table_exists("collection_items"):
        if index_exists("ix_collection_items_item", "collection_items"):
            op.drop_index("ix_collection_items_item", table_name="collection_items")
        if index_exists("ix_collection_items_user_id", "collection_items"):
            op.drop_index("ix_collection_items_user_id", table_name="collection_items")
        op.drop_table("collection_items")
