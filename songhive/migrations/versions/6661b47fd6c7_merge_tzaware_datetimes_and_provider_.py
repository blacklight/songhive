"""merge tzaware datetimes and provider catalog heads

Revision ID: 6661b47fd6c7
Revises: c1e2eae3572c, c7d2e8f4a6b1
Create Date: 2026-09-26 23:24:29.983691

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "6661b47fd6c7"
down_revision: Union[str, Sequence[str], None] = ("c1e2eae3572c", "c7d2e8f4a6b1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
