"""collection collaborators

Adds ``share_grants.collaborator`` (per-user grants can be promoted to
collaborator access on playlists and libraries) and
``playlist_tracks.added_by_id`` (per-entry attribution, mirroring
``library_tracks.added_by_id``).  Existing playlist rows are backfilled with
the owning playlist's ``owner_id`` — only owners/admins could add entries
before this feature.

Revision ID: d87cb336b0eb
Revises: e8f1a3b5c7d9
Create Date: 2026-10-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "d87cb336b0eb"
down_revision: Union[str, Sequence[str], None] = "e8f1a3b5c7d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the collaborator flag and playlist-entry attribution column."""
    if table_exists("share_grants") and not column_exists("share_grants", "collaborator"):
        op.add_column(
            "share_grants",
            sa.Column(
                "collaborator",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    if table_exists("playlist_tracks") and not column_exists("playlist_tracks", "added_by_id"):
        # ``recreate="auto"`` keeps SQLite on the copy-based path, which is
        # required to add a foreign-key column there.
        with op.batch_alter_table("playlist_tracks", recreate="auto") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "added_by_id",
                    sa.String(36),
                    sa.ForeignKey(
                        "users.id",
                        ondelete="SET NULL",
                        name="fk_playlist_tracks_added_by_id_users",
                    ),
                    nullable=True,
                )
            )
        # Every pre-existing row was added by the playlist owner: only
        # owners/admins could add entries before collaborators existed.
        op.execute(
            "UPDATE playlist_tracks SET added_by_id = "
            "(SELECT owner_id FROM playlists WHERE playlists.id = playlist_tracks.playlist_id)"
        )


def downgrade() -> None:
    """Drop the collaborator flag and playlist-entry attribution column."""
    if table_exists("playlist_tracks") and column_exists("playlist_tracks", "added_by_id"):
        with op.batch_alter_table("playlist_tracks", recreate="auto") as batch_op:
            batch_op.drop_column("added_by_id")
    if table_exists("share_grants") and column_exists("share_grants", "collaborator"):
        op.drop_column("share_grants", "collaborator")
