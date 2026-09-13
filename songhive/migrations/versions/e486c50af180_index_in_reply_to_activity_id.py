"""index in_reply_to_activity_id

Revision ID: e486c50af180
Revises: a1b2c3d4e5f6
Create Date: 2026-09-13 19:43:12.330459

"""

from typing import Sequence, Union

from alembic import op

from songhive.migrations.utils import index_exists

# revision identifiers, used by Alembic.
revision: str = "e486c50af180"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if not index_exists("ix_activities_in_reply_to_activity_id", "activities"):
        op.create_index(
            "ix_activities_in_reply_to_activity_id",
            "activities",
            ["in_reply_to_activity_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    if index_exists("ix_activities_in_reply_to_activity_id", "activities"):
        op.drop_index("ix_activities_in_reply_to_activity_id", table_name="activities")
