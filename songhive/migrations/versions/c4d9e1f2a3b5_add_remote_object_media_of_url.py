"""add remote_objects.media_of_url

Adds the ``media_of_url`` column to ``remote_objects`` linking a rendition
object (``Audio``/``Video``) to the canonical URL of the media entity it
renders (the embedded ``track``). The inverse lookup
``media_of_url == track.canonical_url`` resolves metadata-only remote
tracks to a playable rendition through an indexed query instead of a
payload scan. ``/api/v1/remote/objects/{id}/stream`` resolves the media
URL at play time — the seam for providers whose links expire or require
resolution.

Revision ID: c4d9e1f2a3b5
Revises: b7c4a1e9f3d8
Create Date: 2026-02-13 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "c4d9e1f2a3b5"
down_revision: Union[str, Sequence[str], None] = "b7c4a1e9f3d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("remote_objects"):
        return
    if not column_exists("remote_objects", "media_of_url"):
        op.add_column("remote_objects", sa.Column("media_of_url", sa.String(length=512), nullable=True))
    if not index_exists("remote_objects", "ix_remote_objects_media_of_url"):
        op.create_index("ix_remote_objects_media_of_url", "remote_objects", ["media_of_url"])

    # Backfill from payloads already cached: rendition rows embed their
    # entity under ``track.id``.
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "UPDATE remote_objects SET media_of_url = json_extract(payload, '$.track.id') "
            "WHERE payload IS NOT NULL AND object_type IN ('Audio', 'Video') "
            "AND json_extract(payload, '$.track.id') IS NOT NULL"
        )
    elif dialect == "postgresql":
        op.execute(
            "UPDATE remote_objects SET media_of_url = payload #>> '{track,id}' "
            "WHERE payload IS NOT NULL AND object_type IN ('Audio', 'Video') "
            "AND payload #>> '{track,id}' IS NOT NULL"
        )


def downgrade() -> None:
    if not table_exists("remote_objects"):
        return
    if index_exists("remote_objects", "ix_remote_objects_media_of_url"):
        op.drop_index("ix_remote_objects_media_of_url", table_name="remote_objects")
    if column_exists("remote_objects", "media_of_url"):
        op.drop_column("remote_objects", "media_of_url")
