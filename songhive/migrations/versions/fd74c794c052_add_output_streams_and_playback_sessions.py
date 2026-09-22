"""add output streams and playback sessions

Adds ``output_streams``, ``playback_sessions`` and
``playback_session_outputs`` tables for the server-side audio output feature.

Revision ID: fd74c794c052
Revises: d7f3a9c1e5b2
Create Date: 2026-09-22 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import index_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "fd74c794c052"
down_revision: Union[str, Sequence[str], None] = "d7f3a9c1e5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_column(name: str) -> sa.Column:
    """Return a standard timestamp column used by the Songhive ``Base`` model."""
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    """Create the output stream and playback session tables."""
    if not table_exists("output_streams"):
        op.create_table(
            "output_streams",
            sa.Column("output_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("provider_type", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=256), nullable=False),
            sa.Column("config", sa.Text(), nullable=False),
            sa.Column("capabilities", sa.JSON(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_error", sa.Text(), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("output_id"),
        )
        op.create_index(op.f("ix_output_streams_provider_type"), "output_streams", ["provider_type"])
        op.create_index(op.f("ix_output_streams_user_id"), "output_streams", ["user_id"])

    if not table_exists("playback_sessions"):
        op.create_table(
            "playback_sessions",
            sa.Column("session_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("state", sa.String(length=16), nullable=False, server_default="idle"),
            sa.Column("current_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("position_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("position_anchor_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("repeat", sa.String(length=16), nullable=False, server_default="off"),
            sa.Column("shuffle", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("controller_connection_id", sa.String(length=64), nullable=True),
            sa.Column("queue", sa.JSON(), nullable=True),
            sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("session_id"),
            sa.UniqueConstraint("user_id", name="uq_playback_sessions_user_id"),
            sa.CheckConstraint(
                "state IN ('idle', 'playing', 'paused')",
                name="ck_playback_sessions_state",
            ),
            sa.CheckConstraint(
                "repeat IN ('off', 'all', 'one')",
                name="ck_playback_sessions_repeat",
            ),
        )
        op.create_index(op.f("ix_playback_sessions_user_id"), "playback_sessions", ["user_id"], unique=True)

    if not table_exists("playback_session_outputs"):
        op.create_table(
            "playback_session_outputs",
            sa.Column("session_output_id", sa.String(length=36), nullable=False),
            sa.Column("session_id", sa.String(length=36), nullable=False),
            sa.Column("output_kind", sa.String(length=16), nullable=False),
            sa.Column("output_stream_id", sa.String(length=36), nullable=True),
            sa.Column("connection_id", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="connecting"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("latency_offset_ms", sa.Integer(), nullable=False, server_default="0"),
            _timestamp_column("created_at"),
            _timestamp_column("updated_at"),
            sa.ForeignKeyConstraint(["session_id"], ["playback_sessions.session_id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["output_stream_id"], ["output_streams.output_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("session_output_id"),
            sa.UniqueConstraint(
                "session_id",
                "output_kind",
                name="uq_playback_session_outputs_session_kind",
            ),
        )
        op.create_index(
            op.f("ix_playback_session_outputs_output_stream_id"),
            "playback_session_outputs",
            ["output_stream_id"],
        )
        op.create_index(
            op.f("ix_playback_session_outputs_session_id"),
            "playback_session_outputs",
            ["session_id"],
        )


def downgrade() -> None:
    """Drop the output stream and playback session tables."""
    if table_exists("playback_session_outputs"):
        if index_exists(
            op.f("ix_playback_session_outputs_session_id"),
            "playback_session_outputs",
        ):
            op.drop_index(
                op.f("ix_playback_session_outputs_session_id"),
                table_name="playback_session_outputs",
            )
        if index_exists(
            op.f("ix_playback_session_outputs_output_stream_id"),
            "playback_session_outputs",
        ):
            op.drop_index(
                op.f("ix_playback_session_outputs_output_stream_id"),
                table_name="playback_session_outputs",
            )
        op.drop_table("playback_session_outputs")

    if table_exists("playback_sessions"):
        if index_exists(op.f("ix_playback_sessions_user_id"), "playback_sessions"):
            op.drop_index(op.f("ix_playback_sessions_user_id"), table_name="playback_sessions")
        op.drop_table("playback_sessions")

    if table_exists("output_streams"):
        if index_exists(op.f("ix_output_streams_user_id"), "output_streams"):
            op.drop_index(op.f("ix_output_streams_user_id"), table_name="output_streams")
        if index_exists(op.f("ix_output_streams_provider_type"), "output_streams"):
            op.drop_index(op.f("ix_output_streams_provider_type"), table_name="output_streams")
        op.drop_table("output_streams")
