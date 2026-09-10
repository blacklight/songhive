"""
normalize share grant user ids

Revision ID: 8dcaed7b2490
Revises: f124667bab8a
Create Date: 2026-09-10 01:27:15.338415

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8dcaed7b2490"
down_revision: Union[str, Sequence[str], None] = "f124667bab8a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Normalize share grant user_id values that were stored as usernames or emails."""
    # Migrate share_grants where user_id is a username.
    op.execute(
        """
        UPDATE share_grants
        SET user_id = (SELECT id FROM users WHERE users.username = share_grants.user_id)
        WHERE user_id IN (SELECT username FROM users)
        """
    )
    # Migrate share_grants where user_id is an email address.
    op.execute(
        """
        UPDATE share_grants
        SET user_id = (SELECT id FROM users WHERE users.email = share_grants.user_id)
        WHERE user_id IN (SELECT email FROM users)
        """
    )


def downgrade() -> None:
    """Downgrade is not meaningful for a data-only user-id normalization."""
    pass
