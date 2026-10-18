"""add activities.ap_activity_id

Records the trailing identifier of the ActivityPub envelope ``id`` stored
in ``activities.payload`` (the ``{actor}/activities/{id}`` URL remote
servers saw in delivered ``Create``/``Announce``/``Update`` envelopes),
so delivered activities are dereferenceable at that URL and listable in
the actor's outbox. Rows with stored envelopes are backfilled from
``payload['id']``; payload-less rows need no value — their synthesized
envelope id is ``{actor}/activities/{activity.id}``.

Revision ID: 42b527976ba0
Revises: d87cb336b0eb
Create Date: 2026-10-17 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, index_exists

# revision identifiers, used by Alembic.
revision: str = "42b527976ba0"
down_revision: Union[str, Sequence[str], None] = "d87cb336b0eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the ``ap_activity_id`` column and backfill it from envelopes."""
    if not column_exists("activities", "ap_activity_id"):
        with op.batch_alter_table("activities") as batch_op:
            batch_op.add_column(sa.Column("ap_activity_id", sa.String(length=512), nullable=True))
    if not index_exists("ix_activities_ap_activity_id", "activities"):
        op.create_index("ix_activities_ap_activity_id", "activities", ["ap_activity_id"])

    activities = sa.table(
        "activities",
        sa.column("id", sa.String),
        sa.column("payload", sa.JSON),
        sa.column("ap_activity_id", sa.String),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(activities.c.id, activities.c.payload).where(
            activities.c.payload.isnot(None),
            activities.c.ap_activity_id.is_(None),
        )
    ).all()
    for row in rows:
        envelope_id = row.payload.get("id") if isinstance(row.payload, dict) else None
        if isinstance(envelope_id, str) and "/activities/" in envelope_id:
            bind.execute(
                activities.update()
                .where(activities.c.id == row.id)
                .values(ap_activity_id=envelope_id.rsplit("/", 1)[-1])
            )


def downgrade() -> None:
    """Drop the ``ap_activity_id`` column."""
    if column_exists("activities", "ap_activity_id"):
        if index_exists("ix_activities_ap_activity_id", "activities"):
            op.drop_index("ix_activities_ap_activity_id", table_name="activities")
        with op.batch_alter_table("activities") as batch_op:
            batch_op.drop_column("ap_activity_id")
