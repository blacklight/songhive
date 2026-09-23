"""add playback session volume

Adds the ``playback_sessions.volume`` column (0.0–1.0, default 1.0) so output
gain is part of the persisted control-plane state: every controller sees the
same volume, and the stream worker restores it when it claims a session.

Revision ID: a3f9c1d5e7b9
Revises: fd74c794c052
Create Date: 2026-10-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "a3f9c1d5e7b9"
down_revision: Union[str, Sequence[str], None] = "fd74c794c052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the ``playback_sessions.volume`` column."""
    if not table_exists("playback_sessions"):
        return
    if not column_exists("playback_sessions", "volume"):
        op.add_column(
            "playback_sessions",
            sa.Column(
                "volume",
                sa.Float(),
                nullable=False,
                server_default="1",
            ),
        )


def downgrade() -> None:
    """Drop the ``playback_sessions.volume`` column."""
    if not table_exists("playback_sessions"):
        return
    with op.batch_alter_table("playback_sessions", recreate="always") as batch_op:
        if column_exists("playback_sessions", "volume"):
            batch_op.drop_column("volume")
