"""rename hashtags to tags

Revision ID: 321e6beef670
Revises: 4adb5fbea9d6
Create Date: 2026-09-09 11:14:59.507511

"""

from typing import Sequence, Union

from alembic import op

from songhive.migrations.utils import column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "321e6beef670"
down_revision: Union[str, Sequence[str], None] = "4adb5fbea9d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Old table names (pre-rename) and the new ones.
_TABLE_RENAMES = {
    "hashtags": "tags",
    "hashtag_tracks": "tag_tracks",
    "hashtag_albums": "tag_albums",
    "hashtag_artists": "tag_artists",
    "hashtag_playlists": "tag_playlists",
    "hashtag_libraries": "tag_libraries",
}

# Association tables whose "hashtag_id" column must be renamed to "tag_id".
_ASSOC_TABLES = ["tag_tracks", "tag_albums", "tag_artists", "tag_playlists", "tag_libraries"]


def _table_exists(name: str) -> bool:
    """Return whether the table exists in the current database."""
    try:
        return table_exists(name)
    except Exception:
        return False


def _column_exists(table: str, column: str) -> bool:
    """Return whether the column exists on the given table."""
    try:
        return column_exists(table, column)
    except Exception:
        return False


def upgrade() -> None:
    """Upgrade schema: rename hashtag tables/columns to tag."""
    # If the new schema already exists, there is nothing to do.  This covers
    # fresh installs (where SQLAlchemy metadata already created the new tables)
    # and re-running the migration against an up-to-date database.
    if _table_exists("tags"):
        return

    # Rename the main and association tables from the old names to the new ones.
    for old_name, new_name in _TABLE_RENAMES.items():
        if _table_exists(old_name) and not _table_exists(new_name):
            op.rename_table(old_name, new_name)

    # Rename the foreign-key column in each association table.
    for table in _ASSOC_TABLES:
        if _column_exists(table, "hashtag_id") and not _column_exists(table, "tag_id"):
            op.alter_column(table, "hashtag_id", new_column_name="tag_id")

    # Update audit log rows that used the old "hashtag" target type and action
    # so the admin audit view can still enrich and display them.
    if _table_exists("audit_log"):
        op.execute("UPDATE audit_log SET target_type = 'tag' WHERE target_type = 'hashtag'")
        op.execute("UPDATE audit_log SET action = 'tag.add' WHERE action = 'hashtag.add'")
        op.execute("UPDATE audit_log SET action = 'tag.remove' WHERE action = 'hashtag.remove'")
        op.execute("UPDATE audit_log SET action = 'tag.delete' WHERE action = 'hashtag.delete'")


def downgrade() -> None:
    """Downgrade schema: rename tag tables/columns back to hashtag."""
    if _table_exists("hashtags"):
        return

    for table in _ASSOC_TABLES:
        if _column_exists(table, "tag_id") and not _column_exists(table, "hashtag_id"):
            op.alter_column(table, "tag_id", new_column_name="hashtag_id")

    for old_name, new_name in _TABLE_RENAMES.items():
        if _table_exists(new_name) and not _table_exists(old_name):
            op.rename_table(new_name, old_name)

    if _table_exists("audit_log"):
        op.execute("UPDATE audit_log SET target_type = 'hashtag' WHERE target_type = 'tag'")
        op.execute("UPDATE audit_log SET action = 'hashtag.add' WHERE action = 'tag.add'")
        op.execute("UPDATE audit_log SET action = 'hashtag.remove' WHERE action = 'tag.remove'")
        op.execute("UPDATE audit_log SET action = 'hashtag.delete' WHERE action = 'tag.delete'")
