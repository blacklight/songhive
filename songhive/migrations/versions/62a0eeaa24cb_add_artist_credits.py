"""add artist_credits

Adds the ``artist_credits`` table — the MusicBrainz/FEP-be68
``ArtistCredit`` model: an ordered join of ``Artist`` entities to a track
or album carrying the credited display name (``credit``, ``None`` for the
canonical artist name), the ``joinphrase`` emitted after it in the
rendered credit string, and its position (index 0 = primary credit).

Existing rows are backfilled: every track/album gets a primary credit
mirroring its ``artist_id``, and every ``tracks.extra_artists`` name gets
an extra credit against a resolved-or-created ``Artist``. Credits between
names default to ``", "`` with ``""`` on the last credit. The
``extra_artists`` column is kept for backwards compatibility.

Revision ID: 62a0eeaa24cb
Revises: 42b527976ba0
Create Date: 2026-10-17 13:00:00.000000

"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import table_exists

# revision identifiers, used by Alembic.
revision: str = "62a0eeaa24cb"
down_revision: Union[str, Sequence[str], None] = "42b527976ba0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _new_id() -> str:
    return str(uuid.uuid4())


def upgrade() -> None:
    """Create ``artist_credits`` and backfill credits for existing entities."""
    if not table_exists("artist_credits"):
        op.create_table(
            "artist_credits",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("artist_id", sa.String(), nullable=False),
            sa.Column("track_id", sa.String(), nullable=True),
            sa.Column("album_id", sa.String(), nullable=True),
            sa.Column("credit", sa.String(length=256), nullable=True),
            sa.Column("joinphrase", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["album_id"], ["albums.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["artist_id"], ["artists.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("album_id", "artist_id", name="uq_artist_credits_album_artist"),
            sa.UniqueConstraint("track_id", "artist_id", name="uq_artist_credits_track_artist"),
            sa.CheckConstraint(
                "(track_id IS NULL) != (album_id IS NULL)",
                name="ck_artist_credits_one_entity",
            ),
        )
        op.create_index("ix_artist_credits_album_id", "artist_credits", ["album_id"])
        op.create_index("ix_artist_credits_artist_id", "artist_credits", ["artist_id"])
        op.create_index("ix_artist_credits_track_id", "artist_credits", ["track_id"])

    bind = op.get_bind()
    artists_table = sa.table(
        "artists",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
    )
    credits_table = sa.table(
        "artist_credits",
        sa.column("id", sa.String),
        sa.column("artist_id", sa.String),
        sa.column("track_id", sa.String),
        sa.column("album_id", sa.String),
        sa.column("credit", sa.String),
        sa.column("joinphrase", sa.String),
        sa.column("index", sa.Integer),
    )
    tracks_table = sa.table(
        "tracks",
        sa.column("id", sa.String),
        sa.column("artist_id", sa.String),
        sa.column("extra_artists", sa.JSON),
    )
    albums_table = sa.table(
        "albums",
        sa.column("id", sa.String),
        sa.column("artist_id", sa.String),
    )

    # Case-insensitive artist lookup for ``extra_artists`` name backfill.
    artist_by_name: dict = {}
    for row in bind.execute(sa.select(artists_table.c.id, artists_table.c.name)):
        artist_by_name.setdefault(row.name.lower(), row.id)

    def _insert(**values) -> None:
        bind.execute(credits_table.insert().values(id=_new_id(), **values))

    for row in bind.execute(sa.select(tracks_table.c.id, tracks_table.c.artist_id, tracks_table.c.extra_artists)):
        extras = [str(n).strip() for n in (row.extra_artists or []) if str(n).strip()]
        extras = list(dict.fromkeys(extras))
        extras = [n for n in extras if n.lower() not in artist_by_name or artist_by_name[n.lower()] != row.artist_id]
        extras = extras[:20]  # the API's per-track artist cap
        _insert(
            artist_id=row.artist_id,
            track_id=row.id,
            index=0,
            joinphrase=", " if extras else "",
        )
        for index, name in enumerate(extras, start=1):
            artist_id = artist_by_name.get(name.lower())
            if artist_id is None:
                artist_id = _new_id()
                artist_by_name[name.lower()] = artist_id
                bind.execute(artists_table.insert().values(id=artist_id, name=name))
            _insert(
                artist_id=artist_id,
                track_id=row.id,
                index=index,
                joinphrase="" if index == len(extras) else ", ",
            )

    for row in bind.execute(sa.select(albums_table.c.id, albums_table.c.artist_id)):
        _insert(artist_id=row.artist_id, album_id=row.id, index=0, joinphrase="")


def downgrade() -> None:
    """Drop the ``artist_credits`` table."""
    if table_exists("artist_credits"):
        op.drop_index("ix_artist_credits_track_id", table_name="artist_credits")
        op.drop_index("ix_artist_credits_artist_id", table_name="artist_credits")
        op.drop_index("ix_artist_credits_album_id", table_name="artist_credits")
        op.drop_table("artist_credits")
