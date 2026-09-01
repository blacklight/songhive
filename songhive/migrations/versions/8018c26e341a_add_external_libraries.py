"""add external libraries

Revision ID: 8018c26e341a
Revises: 3a8c9d2e1f45
Create Date: 2026-09-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "8018c26e341a"
down_revision: Union[str, Sequence[str], None] = "3a8c9d2e1f45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create external library tables and track metadata timestamp columns."""
    if not table_exists("external_libraries"):
        op.create_table(
            "external_libraries",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("library_id", sa.String(), nullable=False),
            sa.Column("provider_type", sa.String(length=32), nullable=False),
            sa.Column("scope", sa.String(length=16), server_default="user", nullable=False),
            sa.Column("name", sa.String(length=256), nullable=True),
            sa.Column("config", sa.JSON(), nullable=True),
            sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
            sa.Column("include_in_library_index", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("sync_enabled", sa.Boolean(), server_default="1", nullable=False),
            sa.Column("sync_interval_seconds", sa.Integer(), nullable=True),
            sa.Column(
                "last_sync_started_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "last_sync_completed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column("last_sync_status", sa.String(length=16), nullable=True),
            sa.Column("last_sync_error", sa.Text(), nullable=True),
            sa.Column("capabilities", sa.JSON(), nullable=True),
            sa.Column("created_by_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["library_id"], ["libraries.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_external_libraries_created_by_id"), "external_libraries", ["created_by_id"])
        op.create_index(op.f("ix_external_libraries_library_id"), "external_libraries", ["library_id"], unique=True)
        op.create_index(op.f("ix_external_libraries_provider_type"), "external_libraries", ["provider_type"])
        op.create_index(op.f("ix_external_libraries_scope"), "external_libraries", ["scope"])

    if not table_exists("external_tracks"):
        op.create_table(
            "external_tracks",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("external_library_id", sa.String(), nullable=False),
            sa.Column("track_id", sa.String(), nullable=True),
            sa.Column("provider_key", sa.String(length=512), nullable=False),
            sa.Column("provider_etag", sa.String(length=128), nullable=True),
            sa.Column("provider_mime_type", sa.String(length=128), nullable=True),
            sa.Column("provider_checksum", sa.String(length=128), nullable=True),
            sa.Column("sha256", sa.String(length=64), nullable=True),
            sa.Column("metadata_fingerprint", sa.String(length=64), nullable=True),
            sa.Column("provider_mtime", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider_size", sa.BigInteger(), nullable=True),
            sa.Column("state", sa.String(length=16), server_default="active", nullable=False),
            sa.Column("sync_error", sa.Text(), nullable=True),
            sa.Column("write_back_error", sa.Text(), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("write_back_pending", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("raw_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["external_library_id"], ["external_libraries.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("external_library_id", "provider_key", name="uq_external_tracks_lib_key"),
        )
        op.create_index(op.f("ix_external_tracks_external_library_id"), "external_tracks", ["external_library_id"])
        op.create_index(op.f("ix_external_tracks_lib_state"), "external_tracks", ["external_library_id", "state"])
        op.create_index(op.f("ix_external_tracks_metadata_fingerprint"), "external_tracks", ["metadata_fingerprint"])
        op.create_index(op.f("ix_external_tracks_sha256"), "external_tracks", ["sha256"])
        op.create_index(op.f("ix_external_tracks_state"), "external_tracks", ["state"])
        op.create_index(op.f("ix_external_tracks_track_id"), "external_tracks", ["track_id"])

    if not table_exists("external_sync_runs"):
        op.create_table(
            "external_sync_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("external_library_id", sa.String(), nullable=False),
            sa.Column("triggered_by", sa.String(length=16), nullable=False),
            sa.Column("triggered_by_user_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("items_seen", sa.Integer(), server_default="0", nullable=False),
            sa.Column("tracks_created", sa.Integer(), server_default="0", nullable=False),
            sa.Column("tracks_updated", sa.Integer(), server_default="0", nullable=False),
            sa.Column("tracks_shadowed", sa.Integer(), server_default="0", nullable=False),
            sa.Column("tracks_tombstoned", sa.Integer(), server_default="0", nullable=False),
            sa.Column("tracks_missing", sa.Integer(), server_default="0", nullable=False),
            sa.Column("tracks_failed", sa.Integer(), server_default="0", nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("details", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["external_library_id"], ["external_libraries.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_external_sync_runs_external_library_id"),
            "external_sync_runs",
            ["external_library_id"],
        )
        op.create_index(op.f("ix_external_sync_runs_status"), "external_sync_runs", ["status"])

    if not column_exists("tracks", "metadata_updated_at"):
        op.add_column(
            "tracks",
            sa.Column("metadata_updated_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not index_exists("ix_tracks_metadata_updated_at", "tracks"):
        op.create_index(op.f("ix_tracks_metadata_updated_at"), "tracks", ["metadata_updated_at"])

    if not column_exists("tracks", "external_metadata_synced_at"):
        op.add_column(
            "tracks",
            sa.Column("external_metadata_synced_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    """Remove external library tables and track metadata timestamp columns."""
    if column_exists("tracks", "external_metadata_synced_at"):
        op.drop_column("tracks", "external_metadata_synced_at")

    if column_exists("tracks", "metadata_updated_at"):
        if index_exists("ix_tracks_metadata_updated_at", "tracks"):
            op.drop_index(op.f("ix_tracks_metadata_updated_at"), table_name="tracks")
        op.drop_column("tracks", "metadata_updated_at")

    if table_exists("external_sync_runs"):
        op.drop_table("external_sync_runs")

    if table_exists("external_tracks"):
        op.drop_table("external_tracks")

    if table_exists("external_libraries"):
        op.drop_table("external_libraries")
