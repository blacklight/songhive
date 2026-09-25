"""add listening history stats index

Composite ``(user_id, created_at)`` index on ``listening_history`` — the
exact access pattern of every per-user listening-stats query
(``user_id = ? AND created_at BETWEEN ? AND ?``).

Revision ID: b9e4f1a7c3d8
Revises: f6b8d4e2a3c7
Create Date: 2026-09-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

from songhive.migrations.utils import index_exists

# revision identifiers, used by Alembic.
revision: str = "b9e4f1a7c3d8"
down_revision: Union[str, Sequence[str], None] = "f6b8d4e2a3c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if not index_exists("ix_listening_history_user_created_at", "listening_history"):
        op.create_index(
            "ix_listening_history_user_created_at",
            "listening_history",
            ["user_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    if index_exists("ix_listening_history_user_created_at", "listening_history"):
        op.drop_index("ix_listening_history_user_created_at", table_name="listening_history")
