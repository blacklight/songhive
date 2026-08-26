"""add metadata edit columns

Revision ID: 69bc219bdb87
Revises: f854f113ac09
Create Date: 2026-08-26 13:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, index_exists

# revision identifiers, used by Alembic.
revision: str = "69bc219bdb87"
down_revision: Union[str, Sequence[str], None] = "f854f113ac09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fk_name(table_name: str, column_name: str) -> str:
    """Return a deterministic, named foreign key constraint name."""
    return f"fk_{table_name}_{column_name}_stored_files"


def _image_column(table_name: str, column_name: str) -> sa.Column:
    """Build a nullable image/cover column referencing stored_files.id."""
    return sa.Column(
        column_name,
        sa.String(),
        sa.ForeignKey("stored_files.id", name=_fk_name(table_name, column_name)),
        nullable=True,
    )


def _index_name(table_name: str, column_name: str) -> str:
    """Return the standard index name for an image/cover column."""
    return f"ix_{table_name}_{column_name}"


def _add_image_columns(table_name: str) -> None:
    """Add image_file_id and cover_file_id columns to a table if missing."""
    columns_to_add = []
    for column_name in ("image_file_id", "cover_file_id"):
        if not column_exists(table_name, column_name):
            columns_to_add.append((_image_column(table_name, column_name), _index_name(table_name, column_name)))

    if not columns_to_add:
        return

    with op.batch_alter_table(table_name, recreate="always") as batch_op:
        for column, index_name in columns_to_add:
            batch_op.add_column(column)
            batch_op.create_index(op.f(index_name), [column.name])


def upgrade() -> None:
    """Upgrade schema."""
    _add_image_columns("libraries")
    _add_image_columns("playlists")

    if not column_exists("artists", "cover_file_id"):
        with op.batch_alter_table("artists", recreate="always") as batch_op:
            batch_op.add_column(_image_column("artists", "cover_file_id"))
            batch_op.create_index(op.f("ix_artists_cover_file_id"), ["cover_file_id"])

    if not column_exists("tracks", "image_file_id"):
        with op.batch_alter_table("tracks", recreate="always") as batch_op:
            batch_op.add_column(_image_column("tracks", "image_file_id"))
            batch_op.create_index(op.f("ix_tracks_image_file_id"), ["image_file_id"])

    if not column_exists("tracks", "release_year"):
        op.add_column("tracks", sa.Column("release_year", sa.Integer(), nullable=True))


def _drop_image_column(table_name: str, column_name: str) -> None:
    """Drop an image/cover column and its index if the column exists."""
    if not column_exists(table_name, column_name):
        return

    index_name = _index_name(table_name, column_name)
    with op.batch_alter_table(table_name, recreate="always") as batch_op:
        if index_exists(index_name, table_name):
            batch_op.drop_index(op.f(index_name))
        batch_op.drop_column(column_name)


def downgrade() -> None:
    """Downgrade schema."""
    if column_exists("tracks", "release_year"):
        op.drop_column("tracks", "release_year")

    _drop_image_column("tracks", "image_file_id")
    _drop_image_column("artists", "cover_file_id")
    _drop_image_column("playlists", "cover_file_id")
    _drop_image_column("playlists", "image_file_id")
    _drop_image_column("libraries", "cover_file_id")
    _drop_image_column("libraries", "image_file_id")
