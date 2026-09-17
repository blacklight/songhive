"""merge webmentions and published_at index heads

Revision ID: 36d3bfe1da27
Revises: b3f7a1c9d2e4, e1f2a3b4c5d6
Create Date: 2026-09-17 22:56:48.174960

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "36d3bfe1da27"
down_revision: Union[str, Sequence[str], None] = ("b3f7a1c9d2e4", "e1f2a3b4c5d6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
