"""
Add webmention support

Widens the ``activities.entity_type`` check constraint to include ``radio``
so incoming Webmentions can target radio entities, and widens the
``type`` check constraints on ``notifications`` and
``notification_preferences`` to include ``webmention`` so mention
processing can notify entity owners.

The ``webmentions`` table itself is managed by the bundled
``webmentions.storage.adapters.db`` storage (``Base.metadata.create_all``),
mirroring pubby's ``federation_*`` tables — no Alembic DDL is needed for it.

Revision ID: b3f7a1c9d2e4
Revises: e6b1c4d28a90
Create Date: 2026-11-05 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

from songhive.migrations.utils import check_constraint_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "b3f7a1c9d2e4"
down_revision: Union[str, Sequence[str], None] = "e6b1c4d28a90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENTITY_TYPES = ("track", "album", "artist", "playlist", "library", "user", "radio", "remote")
_ENTITY_TYPES_LEGACY = ("track", "album", "artist", "playlist", "library", "user", "remote")

_NOTIFICATION_TYPES = ("follow", "like", "boost", "quote", "reply", "mention", "share", "webmention")
_NOTIFICATION_TYPES_LEGACY = ("follow", "like", "boost", "quote", "reply", "mention", "share")


def _entity_check(types: tuple) -> str:
    return f"entity_type IN ({', '.join(repr(t) for t in types)})"


def _type_check(types: tuple) -> str:
    return f"type IN ({', '.join(repr(t) for t in types)})"


def _reset_notification_check(table: str, constraint: str, types: tuple) -> None:
    if not table_exists(table):
        return
    with op.batch_alter_table(table, recreate="always") as batch_op:
        if check_constraint_exists(table, constraint):
            batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, _type_check(types))


def upgrade() -> None:
    """Allow ``radio`` activity entities and ``webmention`` notifications."""
    if table_exists("activities"):
        with op.batch_alter_table("activities", recreate="always") as batch_op:
            if check_constraint_exists("activities", "ck_activities_entity_type"):
                batch_op.drop_constraint("ck_activities_entity_type", type_="check")
            batch_op.create_check_constraint("ck_activities_entity_type", _entity_check(_ENTITY_TYPES))

    _reset_notification_check("notifications", "ck_notifications_type", _NOTIFICATION_TYPES)
    _reset_notification_check("notification_preferences", "ck_notification_preferences_type", _NOTIFICATION_TYPES)


def downgrade() -> None:
    """Restore the pre-webmention entity and notification type sets."""
    if table_exists("activities"):
        with op.batch_alter_table("activities", recreate="always") as batch_op:
            if check_constraint_exists("activities", "ck_activities_entity_type"):
                batch_op.drop_constraint("ck_activities_entity_type", type_="check")
            batch_op.create_check_constraint("ck_activities_entity_type", _entity_check(_ENTITY_TYPES_LEGACY))

    _reset_notification_check("notifications", "ck_notifications_type", _NOTIFICATION_TYPES_LEGACY)
    _reset_notification_check(
        "notification_preferences", "ck_notification_preferences_type", _NOTIFICATION_TYPES_LEGACY
    )
