"""base

Revision ID: d49ed7492551
Revises:
Create Date: 2026-08-23 17:03:55.115208

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "d49ed7492551"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
