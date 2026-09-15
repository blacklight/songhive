"""add preview cards

Adds the ``preview_cards`` table caching fetched link-preview metadata per
URL, the ``activities.preview_card_id`` link column, and the
``users.preview_cards_enabled`` preference (default on) gating remote
fetches for a user's own posts.

Revision ID: b7e2f91a3c45
Revises: e486c50af180
Create Date: 2026-09-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "b7e2f91a3c45"
down_revision: Union[str, Sequence[str], None] = "e486c50af180"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add preview card storage, activity link, and the user preference."""
    if not table_exists("preview_cards"):
        op.create_table(
            "preview_cards",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("url", sa.String(length=2048), nullable=False),
            sa.Column("title", sa.String(length=512), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("image_url", sa.String(length=2048), nullable=True),
            sa.Column("site_name", sa.String(length=256), nullable=True),
            sa.Column("type", sa.String(length=32), server_default="link", nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_preview_cards_url", "preview_cards", ["url"], unique=True)

    if table_exists("activities") and not column_exists("activities", "preview_card_id"):
        with op.batch_alter_table("activities", recreate="always") as batch_op:
            batch_op.add_column(sa.Column("preview_card_id", sa.String(), nullable=True))
            batch_op.create_foreign_key(
                "fk_activities_preview_card_id",
                "preview_cards",
                ["preview_card_id"],
                ["id"],
                ondelete="SET NULL",
            )

    if table_exists("users") and not column_exists("users", "preview_cards_enabled"):
        op.add_column(
            "users",
            sa.Column(
                "preview_cards_enabled",
                sa.Boolean(),
                server_default="1",
                nullable=False,
            ),
        )


def downgrade() -> None:
    """Drop the preview card feature's schema objects."""
    if table_exists("users") and column_exists("users", "preview_cards_enabled"):
        op.drop_column("users", "preview_cards_enabled")

    if table_exists("activities") and column_exists("activities", "preview_card_id"):
        with op.batch_alter_table("activities", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_activities_preview_card_id", type_="foreignkey")
            batch_op.drop_column("preview_card_id")

    if table_exists("preview_cards"):
        op.drop_table("preview_cards")
