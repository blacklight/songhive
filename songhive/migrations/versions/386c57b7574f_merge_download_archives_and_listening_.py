"""merge download archives and listening history stats index

Revision ID: 386c57b7574f
Revises: b6a4d8e2c1f3, b9e4f1a7c3d8
Create Date: 2026-09-25 02:19:33.705719

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "386c57b7574f"
down_revision: Union[str, Sequence[str], None] = ("b6a4d8e2c1f3", "b9e4f1a7c3d8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
