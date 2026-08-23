"""
Shared utilities for Alembic migrations.
"""

import sqlalchemy as sa
from alembic import op


def table_exists(table_name: str) -> bool:
    """
    Return True if the table already exists in the database.

    This is useful for idempotent migrations that can be run multiple times
    without error. For example, migrations can check if a table exists before
    creating it, allowing the migration to be safely re-run.

    Example:
        >>> if not table_exists("my_table"):
        ...     op.create_table("my_table", ...)
    """
    try:
        bind = op.get_bind()
        return sa.inspect(bind).has_table(table_name)
    except Exception:
        # Offline mode or otherwise no real connection; assume not present.
        return False


def column_exists(table_name: str, column_name: str) -> bool:
    """
    Return True if the column exists in the specified table.

    This is useful for idempotent migrations that add columns conditionally.

    Example:
        >>> if not column_exists("users", "email_verified"):
        ...     op.add_column("users", sa.Column("email_verified", sa.Boolean()))
    """
    try:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if not inspector.has_table(table_name):
            return False
        columns = [col["name"] for col in inspector.get_columns(table_name)]
        return column_name in columns
    except Exception:
        # Offline mode or otherwise no real connection; assume not present.
        return False


def index_exists(index_name: str, table_name: str) -> bool:
    """
    Return True if the index exists on the specified table.

    This is useful for idempotent migrations that create indexes conditionally.

    Example:
        >>> if not index_exists("ix_users_email", "users"):
        ...     op.create_index("ix_users_email", "users", ["email"])
    """
    try:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        if not inspector.has_table(table_name):
            return False
        indexes = [idx["name"] for idx in inspector.get_indexes(table_name)]
        return index_name in indexes
    except Exception:
        # Offline mode or otherwise no real connection; assume not present.
        return False
