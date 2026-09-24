"""
fix inverted artist parent urls

Artists embedded in album/track documents were stamped with the embedding
document as ``parent_url``, inverting the containment edge (albums belong
to artists, not the reverse) and producing artist→album→artist cycles in
the remote-object graph. Clear ``parent_url`` on artist rows that point at
album or track rows; newly cached rows no longer get the inverted link.

Revision ID: e5f7a3b9c2d4
Revises: d3f6a9c1e4b7
Create Date: 2026-10-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import table_exists

# revision identifiers, used by Alembic.
revision: str = "e5f7a3b9c2d4"
down_revision: Union[str, Sequence[str], None] = "d3f6a9c1e4b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not table_exists("remote_objects"):
        return
    op.execute(
        sa.text(
            "UPDATE remote_objects SET parent_url = NULL "
            "WHERE resource_type = 'artist' AND parent_url IN "
            "(SELECT canonical_url FROM remote_objects "
            "WHERE resource_type IN ('album', 'track'))"
        )
    )


def downgrade() -> None:
    pass
