"""add moderation

Adds the moderation tables backing Mastodon-style user and instance
moderation:

- ``user_moderations`` — a local user's mute/block of a local or remote
  actor;
- ``admin_user_moderations`` — an admin's limit/suspend of a local or
  remote actor, with an optional reason;
- ``instance_moderations`` — an admin's per-domain ``defederate`` /
  ``followers_only`` policy, layering over the configured instance
  allow/block lists.

Revision ID: c3d5e7f9a1b2
Revises: b8c9d0e1f2a3
Create Date: 2026-01-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_MODERATION_KINDS = ("mute", "block")
_USER_MODERATION_CHECK = f"kind IN ({', '.join(repr(k) for k in _USER_MODERATION_KINDS)})"
_ADMIN_USER_ACTIONS = ("limit", "suspend")
_ADMIN_USER_ACTION_CHECK = f"action IN ({', '.join(repr(a) for a in _ADMIN_USER_ACTIONS)})"
_INSTANCE_ACTIONS = ("defederate", "followers_only")
_INSTANCE_ACTION_CHECK = f"action IN ({', '.join(repr(a) for a in _INSTANCE_ACTIONS)})"


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the moderation tables."""
    if not table_exists("user_moderations"):
        op.create_table(
            "user_moderations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("target_actor_url", sa.String(length=512), nullable=False),
            sa.Column("target_user_id", sa.String(), nullable=True),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("actor_data", sa.JSON(), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "target_actor_url", "kind", name="uq_user_moderations_user_target_kind"),
            sa.CheckConstraint(_USER_MODERATION_CHECK, name="ck_user_moderations_kind"),
        )
        op.create_index("ix_user_moderations_user_id", "user_moderations", ["user_id"])
        op.create_index("ix_user_moderations_target_user_id", "user_moderations", ["target_user_id"])
        op.create_index("ix_user_moderations_target_actor_url", "user_moderations", ["target_actor_url"])

    if not table_exists("admin_user_moderations"):
        op.create_table(
            "admin_user_moderations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("target_actor_url", sa.String(length=512), nullable=False),
            sa.Column("target_user_id", sa.String(), nullable=True),
            sa.Column("action", sa.String(length=16), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("actor_data", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("target_actor_url", name="uq_admin_user_moderations_target_actor_url"),
            sa.CheckConstraint(_ADMIN_USER_ACTION_CHECK, name="ck_admin_user_moderations_action"),
        )
        op.create_index("ix_admin_user_moderations_target_user_id", "admin_user_moderations", ["target_user_id"])

    if not table_exists("instance_moderations"):
        op.create_table(
            "instance_moderations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("domain", sa.String(length=255), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("domain", name="uq_instance_moderations_domain"),
            sa.CheckConstraint(_INSTANCE_ACTION_CHECK, name="ck_instance_moderations_action"),
        )
        op.create_index("ix_instance_moderations_domain", "instance_moderations", ["domain"])


def downgrade() -> None:
    """Drop the moderation tables."""
    if table_exists("instance_moderations"):
        if index_exists("ix_instance_moderations_domain", "instance_moderations"):
            op.drop_index("ix_instance_moderations_domain", table_name="instance_moderations")
        op.drop_table("instance_moderations")

    if table_exists("admin_user_moderations"):
        if index_exists("ix_admin_user_moderations_target_user_id", "admin_user_moderations"):
            op.drop_index("ix_admin_user_moderations_target_user_id", table_name="admin_user_moderations")
        op.drop_table("admin_user_moderations")

    if table_exists("user_moderations"):
        if index_exists("ix_user_moderations_target_actor_url", "user_moderations"):
            op.drop_index("ix_user_moderations_target_actor_url", table_name="user_moderations")
        if index_exists("ix_user_moderations_target_user_id", "user_moderations"):
            op.drop_index("ix_user_moderations_target_user_id", table_name="user_moderations")
        if index_exists("ix_user_moderations_user_id", "user_moderations"):
            op.drop_index("ix_user_moderations_user_id", table_name="user_moderations")
        op.drop_table("user_moderations")
