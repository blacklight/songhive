"""add provider catalog entries and external item membership

Creates ``provider_catalog_entries`` — the instance-wide, provider-agnostic
metadata cache used by entity-backed providers (TIDAL is the first) — and
adds ``membership``/``contents_fetched_at``/``contents_error`` to
``external_items`` for saved-vs-referenced tracking and lazy container
contents bookkeeping.

Revision ID: c7d2e8f4a6b1
Revises: 1c3e5f7a9b2d
Create Date: 2026-12-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "c7d2e8f4a6b1"
down_revision: Union[str, Sequence[str], None] = "1c3e5f7a9b2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ``provider_catalog_entries`` and the new ``external_items`` columns."""
    if not table_exists("provider_catalog_entries"):
        op.create_table(
            "provider_catalog_entries",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("provider_type", sa.String(length=32), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("provider_key", sa.String(length=512), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("contents", sa.JSON(), nullable=True),
            sa.Column("contents_etag", sa.String(length=128), nullable=True),
            sa.Column("contents_fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("unavailable_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_type", "kind", "provider_key", name="uq_provider_catalog_entries"),
        )
        op.create_index(
            op.f("ix_provider_catalog_entries_expiry"),
            "provider_catalog_entries",
            ["provider_type", "kind", "expires_at"],
        )

    if not column_exists("external_items", "membership"):
        op.add_column(
            "external_items",
            sa.Column("membership", sa.String(length=16), server_default="saved", nullable=False),
        )
        if not index_exists("ix_external_items_membership", "external_items"):
            op.create_index(op.f("ix_external_items_membership"), "external_items", ["membership"])

    if not column_exists("external_items", "contents_fetched_at"):
        op.add_column(
            "external_items",
            sa.Column("contents_fetched_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not column_exists("external_items", "contents_error"):
        op.add_column(
            "external_items",
            sa.Column("contents_error", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Drop the ``external_items`` columns and ``provider_catalog_entries``."""
    if column_exists("external_items", "contents_error"):
        op.drop_column("external_items", "contents_error")
    if column_exists("external_items", "contents_fetched_at"):
        op.drop_column("external_items", "contents_fetched_at")
    if column_exists("external_items", "membership"):
        if index_exists("ix_external_items_membership", "external_items"):
            op.drop_index("ix_external_items_membership", table_name="external_items")
        op.drop_column("external_items", "membership")

    if table_exists("provider_catalog_entries"):
        op.drop_table("provider_catalog_entries")
