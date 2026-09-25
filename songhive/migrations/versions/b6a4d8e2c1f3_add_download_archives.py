"""add download archives

Creates the ``download_archives`` table backing asynchronous bulk-download
requests (ZIP archives of tracks, remote objects, and podcast episodes) and
widens the ``type`` check constraints on ``notifications`` and
``notification_preferences`` to include ``download`` so archive-ready
notifications can be delivered.

Revision ID: b6a4d8e2c1f3
Revises: f6b8d4e2a3c7
Create Date: 2026-11-05 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import check_constraint_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "b6a4d8e2c1f3"
down_revision: Union[str, Sequence[str], None] = "f6b8d4e2a3c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOTIFICATION_TYPES = (
    "follow",
    "like",
    "boost",
    "quote",
    "reply",
    "mention",
    "share",
    "webmention",
    "activity",
    "report",
    "download",
)
_NOTIFICATION_TYPES_LEGACY = tuple(t for t in _NOTIFICATION_TYPES if t != "download")

_ARCHIVE_STATUSES = ("pending", "processing", "ready", "failed")
_ARCHIVE_STATUS_CHECK = f"status IN ({', '.join(repr(s) for s in _ARCHIVE_STATUSES)})"


def _type_check(types: tuple) -> str:
    return f"type IN ({', '.join(repr(t) for t in types)})"


def _reset_notification_check(table: str, constraint: str, types: tuple) -> None:
    if not table_exists(table):
        return
    with op.batch_alter_table(table, recreate="always") as batch_op:
        if check_constraint_exists(table, constraint):
            batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, _type_check(types))


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the download_archives table and allow ``download`` notifications."""
    if not table_exists("download_archives"):
        op.create_table(
            "download_archives",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("label", sa.String(length=256), nullable=False),
            sa.Column("items", sa.JSON(), nullable=False),
            sa.Column("archive_file_id", sa.String(), nullable=True),
            sa.Column("item_errors", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["archive_file_id"], ["stored_files.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(_ARCHIVE_STATUS_CHECK, name="ck_download_archives_status"),
        )
        op.create_index("ix_download_archives_user_id", "download_archives", ["user_id"])
        op.create_index("ix_download_archives_status", "download_archives", ["status"])
        op.create_index(
            "ix_download_archives_user_id_created_at",
            "download_archives",
            ["user_id", "created_at"],
        )

    _reset_notification_check("notifications", "ck_notifications_type", _NOTIFICATION_TYPES)
    _reset_notification_check(
        "notification_preferences",
        "ck_notification_preferences_type",
        _NOTIFICATION_TYPES,
    )


def downgrade() -> None:
    """Drop the download_archives table and restore the type checks."""
    if table_exists("download_archives"):
        op.drop_table("download_archives")

    _reset_notification_check("notifications", "ck_notifications_type", _NOTIFICATION_TYPES_LEGACY)
    _reset_notification_check(
        "notification_preferences",
        "ck_notification_preferences_type",
        _NOTIFICATION_TYPES_LEGACY,
    )
