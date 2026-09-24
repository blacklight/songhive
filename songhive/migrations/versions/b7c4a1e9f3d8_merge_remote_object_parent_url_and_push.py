"""merge remote_objects.parent_url and push subscription heads

Revision ID: b7c4a1e9f3d8
Revises: 86f018e7d9ad, a4b7c2d9e5f1
Create Date: 2026-02-12 00:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "b7c4a1e9f3d8"
down_revision: Union[str, Sequence[str], None] = ("86f018e7d9ad", "a4b7c2d9e5f1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
