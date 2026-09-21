"""add two-factor authentication

Adds the ``users.totp_secret`` column (Fernet-encrypted TOTP shared secret)
plus the ``webauthn_credentials`` and ``recovery_codes`` tables.

Revision ID: 2b89e764c774
Revises: e3f5a7b9c1d2
Create Date: 2026-09-20 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "2b89e764c774"
down_revision: Union[str, Sequence[str], None] = "e3f5a7b9c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add two-factor authentication storage."""
    if table_exists("users") and not column_exists("users", "totp_secret"):
        op.add_column("users", sa.Column("totp_secret", sa.Text(), nullable=True))

    if not table_exists("webauthn_credentials"):
        op.create_table(
            "webauthn_credentials",
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("credential_id", sa.String(length=255), nullable=False),
            sa.Column("credential_data", sa.LargeBinary(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=True),
            sa.Column("transports", sa.String(length=255), nullable=True),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("credential_id"),
        )
        op.create_index(
            op.f("ix_webauthn_credentials_credential_id"),
            "webauthn_credentials",
            ["credential_id"],
            unique=True,
        )
        op.create_index(
            op.f("ix_webauthn_credentials_user_id"),
            "webauthn_credentials",
            ["user_id"],
            unique=False,
        )

    if not table_exists("recovery_codes"):
        op.create_table(
            "recovery_codes",
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("code_hash", sa.String(length=64), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", sa.String(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_recovery_codes_user_id"),
            "recovery_codes",
            ["user_id"],
            unique=False,
        )


def downgrade() -> None:
    """Remove two-factor authentication storage."""
    if table_exists("recovery_codes"):
        op.drop_index(op.f("ix_recovery_codes_user_id"), table_name="recovery_codes")
        op.drop_table("recovery_codes")

    if table_exists("webauthn_credentials"):
        op.drop_index(op.f("ix_webauthn_credentials_user_id"), table_name="webauthn_credentials")
        op.drop_index(
            op.f("ix_webauthn_credentials_credential_id"),
            table_name="webauthn_credentials",
        )
        op.drop_table("webauthn_credentials")

    if table_exists("users") and column_exists("users", "totp_secret"):
        with op.batch_alter_table("users", recreate="always") as batch_op:
            batch_op.drop_column("totp_secret")
