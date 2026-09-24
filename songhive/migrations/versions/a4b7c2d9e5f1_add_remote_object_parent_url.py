"""add remote_objects.parent_url

Adds the ``parent_url`` column to ``remote_objects`` linking a cached
remote object to the canonical URL of its containing remote resource —
the library of a federated upload (Funkwhale ``Audio``), the album of a
federated ``Track``, the artist of a federated ``Album``. It powers the
contents listing on remote library/album/artist pages.

Revision ID: a4b7c2d9e5f1
Revises: a3f9c1d5e7b9
Create Date: 2026-02-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "a4b7c2d9e5f1"
down_revision: Union[str, Sequence[str], None] = "a3f9c1d5e7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("remote_objects"):
        return
    if not column_exists("remote_objects", "parent_url"):
        op.add_column("remote_objects", sa.Column("parent_url", sa.String(length=512), nullable=True))
    if not index_exists("remote_objects", "ix_remote_objects_parent_url"):
        op.create_index("ix_remote_objects_parent_url", "remote_objects", ["parent_url"])


def downgrade() -> None:
    if not table_exists("remote_objects"):
        return
    if index_exists("remote_objects", "ix_remote_objects_parent_url"):
        op.drop_index("ix_remote_objects_parent_url", table_name="remote_objects")
    if column_exists("remote_objects", "parent_url"):
        op.drop_column("remote_objects", "parent_url")
