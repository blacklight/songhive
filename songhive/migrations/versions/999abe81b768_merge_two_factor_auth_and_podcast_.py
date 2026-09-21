"""merge two-factor auth and podcast playlist heads

Revision ID: 999abe81b768
Revises: 2b89e764c774, b5e7d9f2a4c6
Create Date: 2026-09-21 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "999abe81b768"
down_revision: Union[str, Sequence[str], None] = ("2b89e764c774", "b5e7d9f2a4c6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
