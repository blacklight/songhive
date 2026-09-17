"""add remote_objects and remote activity entity type

Adds the ``remote_objects`` cache table for explicitly dereferenced remote
(federated) objects and widens the ``activities.entity_type`` check
constraint so standalone remote activities can attach to cached remote
objects via ``entity_type='remote'``.

Revision ID: e6b1c4d28a90
Revises: d4a8c2f15b63
Create Date: 2026-11-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "e6b1c4d28a90"
down_revision: Union[str, Sequence[str], None] = "d4a8c2f15b63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENTITY_TYPES = ("track", "album", "artist", "playlist", "library", "user", "remote")
_ENTITY_TYPES_LEGACY = ("track", "album", "artist", "playlist", "library", "user")


def _entity_check(types: tuple) -> str:
    return f"entity_type IN ({', '.join(repr(t) for t in types)})"


def upgrade() -> None:
    """Create ``remote_objects`` and allow ``remote`` activity entities."""
    if not table_exists("remote_objects"):
        op.create_table(
            "remote_objects",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("canonical_url", sa.String(length=512), nullable=False),
            sa.Column("activity_url", sa.String(length=512), nullable=True),
            sa.Column("domain", sa.String(length=255), nullable=False),
            sa.Column("object_type", sa.String(length=64), nullable=False),
            sa.Column("resource_type", sa.String(length=32), nullable=True),
            sa.Column("actor_url", sa.String(length=512), nullable=False),
            sa.Column("visibility", sa.String(length=16), server_default="public", nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("name", sa.Text(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("image_url", sa.Text(), nullable=True),
            sa.Column("audio_url", sa.Text(), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=True),
            sa.Column("etag", sa.String(length=255), nullable=True),
            sa.Column("last_modified", sa.String(length=255), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("unavailable_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("canonical_url", name="uq_remote_objects_canonical_url"),
        )
        op.create_index("ix_remote_objects_domain", "remote_objects", ["domain"])
        op.create_index("ix_remote_objects_actor_url", "remote_objects", ["actor_url"])
        op.create_index("ix_remote_objects_resource_type_domain", "remote_objects", ["resource_type", "domain"])

    if table_exists("activities"):
        with op.batch_alter_table("activities", recreate="always") as batch_op:
            if check_constraint_exists("activities", "ck_activities_entity_type"):
                batch_op.drop_constraint("ck_activities_entity_type", type_="check")
            batch_op.create_check_constraint("ck_activities_entity_type", _entity_check(_ENTITY_TYPES))


def downgrade() -> None:
    """Drop ``remote_objects`` and the ``remote`` activity entity type."""
    if table_exists("activities"):
        with op.batch_alter_table("activities", recreate="always") as batch_op:
            if check_constraint_exists("activities", "ck_activities_entity_type"):
                batch_op.drop_constraint("ck_activities_entity_type", type_="check")
            batch_op.create_check_constraint("ck_activities_entity_type", _entity_check(_ENTITY_TYPES_LEGACY))

    if table_exists("remote_objects"):
        if index_exists("ix_remote_objects_resource_type_domain", "remote_objects"):
            op.drop_index("ix_remote_objects_resource_type_domain", table_name="remote_objects")
        if index_exists("ix_remote_objects_actor_url", "remote_objects"):
            op.drop_index("ix_remote_objects_actor_url", table_name="remote_objects")
        if index_exists("ix_remote_objects_domain", "remote_objects"):
            op.drop_index("ix_remote_objects_domain", table_name="remote_objects")
        op.drop_table("remote_objects")
