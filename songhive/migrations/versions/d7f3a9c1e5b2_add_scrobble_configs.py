"""
add scrobble configs table

Adds ``scrobble_configs``: per-user Audioscrobbler-compatible scrobbling
settings — the target service (``lastfm``/``librefm``), the remote username,
the Fernet-encrypted session key, the enabled flag, the listen thresholds
(``min_seconds``/``min_percent``) and the last submission error/timestamp.

Revision ID: d7f3a9c1e5b2
Revises: 999abe81b768
Create Date: 2026-09-21 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "d7f3a9c1e5b2"
down_revision: Union[str, Sequence[str], None] = "999abe81b768"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the scrobble config table."""
    if not table_exists("scrobble_configs"):
        op.create_table(
            "scrobble_configs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("service", sa.String(length=16), nullable=False),
            sa.Column("username", sa.String(length=255), nullable=False),
            sa.Column("session_key", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("min_seconds", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("min_percent", sa.Integer(), nullable=False, server_default="25"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("last_scrobbled_at", sa.DateTime(timezone=True), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", name="uq_scrobble_configs_user_id"),
        )
        op.create_index("ix_scrobble_configs_user_id", "scrobble_configs", ["user_id"])


def downgrade() -> None:
    """Drop the scrobble config table."""
    if table_exists("scrobble_configs"):
        if index_exists("ix_scrobble_configs_user_id", "scrobble_configs"):
            op.drop_index("ix_scrobble_configs_user_id", table_name="scrobble_configs")
        op.drop_table("scrobble_configs")
