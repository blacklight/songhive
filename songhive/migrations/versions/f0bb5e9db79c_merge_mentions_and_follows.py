"""merge mention records and follows heads

Revision ID: f0bb5e9db79c
Revises: c2e8f41a9b67, f2a9c1d4e7b8
Create Date: 2026-09-18 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "f0bb5e9db79c"
down_revision: Union[str, Sequence[str], None] = ("c2e8f41a9b67", "f2a9c1d4e7b8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
