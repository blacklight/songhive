"""add api_tokens table

Revision ID: 194ec56009fe
Revises: d49ed7492551
Create Date: 2026-08-23 17:57:47.093743

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "194ec56009fe"
down_revision: Union[str, Sequence[str], None] = "d49ed7492551"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    """Return True if the table already exists in the database."""
    bind = op.get_bind()
    try:
        return sa.inspect(bind).has_table(table_name)
    except Exception:
        # Offline mode or otherwise no real connection; assume not present.
        return False


def upgrade() -> None:
    """Upgrade schema."""
    if _table_exists("api_tokens"):
        return

    op.create_table(
        "api_tokens",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_api_tokens_user_name"),
    )
    op.create_index(op.f("ix_api_tokens_jti"), "api_tokens", ["jti"], unique=True)
    op.create_index(op.f("ix_api_tokens_user_id"), "api_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    if not _table_exists("api_tokens"):
        return

    op.drop_index(op.f("ix_api_tokens_user_id"), table_name="api_tokens")
    op.drop_index(op.f("ix_api_tokens_jti"), table_name="api_tokens")
    op.drop_table("api_tokens")
