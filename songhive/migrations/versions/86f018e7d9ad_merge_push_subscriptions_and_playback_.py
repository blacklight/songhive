"""merge push subscriptions and playback session volume

Revision ID: 86f018e7d9ad
Revises: 0a81bdcc5ae7, a3f9c1d5e7b9
Create Date: 2026-09-23 23:30:22.550686

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "86f018e7d9ad"
down_revision: Union[str, Sequence[str], None] = ("0a81bdcc5ae7", "a3f9c1d5e7b9")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
