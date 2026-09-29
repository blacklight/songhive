"""add payments tables and user payment flags

Adds the sales/orders/entitlements/webhook/membership schema for payments
support, the ``payments_required`` and ``admin_suspended`` columns on
``users``, and widens the notification type check constraints with the
``purchase`` and ``membership`` types.

Revision ID: b3e1a2f4c5d6
Revises: 6661b47fd6c7
Create Date: 2026-09-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from songhive.migrations.utils import column_exists, table_exists

# revision identifiers, used by Alembic.
revision: str = "b3e1a2f4c5d6"
down_revision: Union[str, Sequence[str], None] = "6661b47fd6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_NOTIFICATION_TYPES = (
    "'follow', 'like', 'boost', 'quote', 'reply', 'mention', 'share', " "'webmention', 'activity', 'report', 'download'"
)
_NEW_NOTIFICATION_TYPES = _OLD_NOTIFICATION_TYPES + ", 'purchase', 'membership'"
_OLD_TYPE_CHECK = f"type IN ({_OLD_NOTIFICATION_TYPES})"
_NEW_TYPE_CHECK = f"type IN ({_NEW_NOTIFICATION_TYPES})"


def _id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def _create_sales() -> None:
    if table_exists("sales"):
        return
    op.create_table(
        "sales",
        _id_column(),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("track_id", sa.String(length=36), nullable=True),
        sa.Column("album_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("unpaid_policy", sa.String(length=16), nullable=False, server_default="sample"),
        sa.Column("sample_start_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_length_seconds", sa.Integer(), nullable=True),
        sa.Column("sample_render_policy", sa.String(length=16), nullable=False, server_default="materialized"),
        sa.Column("album_snapshot_track_ids", sa.JSON(), nullable=True),
        sa.Column("provider_price_id", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "entity_type IN ('track', 'album')",
            name="ck_sales_entity_type",
        ),
        sa.CheckConstraint("status IN ('draft', 'active', 'inactive')", name="ck_sales_status"),
        sa.CheckConstraint(
            "unpaid_policy IN ('full_stream', 'sample', 'none')",
            name="ck_sales_unpaid_policy",
        ),
        sa.CheckConstraint(
            "sample_render_policy IN ('materialized', 'on_demand')",
            name="ck_sales_sample_render_policy",
        ),
        sa.CheckConstraint(
            "(entity_type = 'track' AND track_id IS NOT NULL AND album_id IS NULL) OR "
            "(entity_type = 'album' AND album_id IS NOT NULL AND track_id IS NULL)",
            name="ck_sales_entity_ref",
        ),
        sa.CheckConstraint("price_minor >= 0", name="ck_sales_price_minor"),
        sa.CheckConstraint("sample_start_seconds >= 0", name="ck_sales_sample_start"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["album_id"], ["albums.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_owner_id", "sales", ["owner_id"])
    op.create_index("ix_sales_track_status", "sales", ["track_id", "status"])
    op.create_index("ix_sales_album_status", "sales", ["album_id", "status"])
    op.create_index(
        "uq_sales_active_track",
        "sales",
        ["track_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_sales_active_album",
        "sales",
        ["album_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def _create_payment_orders() -> None:
    if table_exists("payment_orders"):
        return
    op.create_table(
        "payment_orders",
        _id_column(),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("sale_id", sa.String(length=36), nullable=True),
        sa.Column("buyer_user_id", sa.String(length=36), nullable=True),
        sa.Column("buyer_email_encrypted", sa.Text(), nullable=True),
        sa.Column("buyer_email_key_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_minor", sa.Integer(), nullable=False),
        sa.Column("fee_minor", sa.Integer(), nullable=True),
        sa.Column("checkout_token", sa.String(length=128), nullable=False),
        sa.Column("provider_name", sa.String(length=32), nullable=False),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("provider_payment_id", sa.String(length=255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfillment_started_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("kind IN ('purchase', 'membership')", name="ck_payment_orders_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'paid', 'failed', 'expired', 'refunded', "
            "'partially_refunded', 'disputed', 'canceled')",
            name="ck_payment_orders_status",
        ),
        sa.CheckConstraint("total_minor >= 0", name="ck_payment_orders_total_minor"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["buyer_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_name", "checkout_token", name="uq_payment_orders_checkout_token"),
    )
    op.create_index("ix_payment_orders_sale_id", "payment_orders", ["sale_id"])
    op.create_index("ix_payment_orders_buyer", "payment_orders", ["buyer_user_id"])
    op.create_index("ix_payment_orders_status_created", "payment_orders", ["status", "created_at"])
    op.create_index(
        "ix_payment_orders_provider_session",
        "payment_orders",
        ["provider_name", "provider_session_id"],
    )
    op.create_index(
        "ix_payment_orders_provider_payment",
        "payment_orders",
        ["provider_name", "provider_payment_id"],
    )


def _create_payment_order_items() -> None:
    if table_exists("payment_order_items"):
        return
    op.create_table(
        "payment_order_items",
        _id_column(),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("track_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_minor", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["order_id"], ["payment_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "track_id", name="uq_payment_order_items_order_track"),
    )
    op.create_index("ix_payment_order_items_track", "payment_order_items", ["track_id"])


def _create_payment_events() -> None:
    if table_exists("payment_events"):
        return
    op.create_table(
        "payment_events",
        _id_column(),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("order_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        # Normalized handler fields (ids/status/money only — never the raw
        # provider payload, which may contain payer PII).
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("scope IN ('platform', 'connect')", name="ck_payment_events_scope"),
        sa.CheckConstraint(
            "status IN ('pending', 'processed', 'failed', 'ignored')",
            name="ck_payment_events_status",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["payment_orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_payment_events_provider_event"),
    )
    op.create_index("ix_payment_events_status", "payment_events", ["status"])
    op.create_index("ix_payment_events_order_id", "payment_events", ["order_id"])


def _create_purchase_entitlements() -> None:
    if table_exists("purchase_entitlements"):
        return
    op.create_table(
        "purchase_entitlements",
        _id_column(),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("sale_id", sa.String(length=36), nullable=True),
        sa.Column("track_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("email_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=32), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_purchase_entitlements_status"),
        sa.ForeignKeyConstraint(["order_id"], ["payment_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "track_id", name="uq_purchase_entitlements_order_track"),
    )
    op.create_index(
        "ix_purchase_entitlements_user_track",
        "purchase_entitlements",
        ["user_id", "track_id", "status"],
    )
    op.create_index("ix_purchase_entitlements_track", "purchase_entitlements", ["track_id", "status"])


def _create_redeem_capabilities() -> None:
    if table_exists("redeem_capabilities"):
        return
    op.create_table(
        "redeem_capabilities",
        _id_column(),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="download"),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("email_hash", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("redemption_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_redeemed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("kind IN ('download')", name="ck_redeem_capabilities_kind"),
        sa.ForeignKeyConstraint(["order_id"], ["payment_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_redeem_capabilities_order", "redeem_capabilities", ["order_id"])


def _create_connected_accounts() -> None:
    if table_exists("connected_accounts"):
        return
    op.create_table(
        "connected_accounts",
        _id_column(),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="stripe"),
        sa.Column("provider_account_id", sa.String(length=255), nullable=False),
        sa.Column("charges_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("payouts_enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("details_submitted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_connected_accounts_user"),
        sa.UniqueConstraint("provider", "provider_account_id", name="uq_connected_accounts_provider_account"),
    )


def _create_instance_subscriptions() -> None:
    if table_exists("instance_subscriptions"):
        return
    op.create_table(
        "instance_subscriptions",
        _id_column(),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="stripe"),
        sa.Column("provider_customer_id", sa.String(length=255), nullable=True),
        sa.Column("provider_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="none"),
        sa.Column("paid_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_invoice_id", sa.String(length=255), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('none', 'incomplete', 'active', 'trialing', 'past_due', "
            "'canceled', 'unpaid', 'incomplete_expired')",
            name="ck_instance_subscriptions_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_instance_subscriptions_user"),
    )
    op.create_index("ix_instance_subscriptions_customer", "instance_subscriptions", ["provider_customer_id"])
    op.create_index(
        "ix_instance_subscriptions_subscription",
        "instance_subscriptions",
        ["provider_subscription_id"],
    )
    op.create_index("ix_instance_subscriptions_paid_through", "instance_subscriptions", ["paid_through"])


def _create_fulfillment_outbox() -> None:
    if table_exists("fulfillment_outbox"):
        return
    op.create_table(
        "fulfillment_outbox",
        _id_column(),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')",
            name="ck_fulfillment_outbox_status",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["payment_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "kind", name="uq_fulfillment_outbox_order_kind"),
    )
    op.create_index("ix_fulfillment_outbox_status", "fulfillment_outbox", ["status", "scheduled_at"])


def _create_sample_derivatives() -> None:
    if table_exists("sample_derivatives"):
        return
    op.create_table(
        "sample_derivatives",
        _id_column(),
        sa.Column("track_id", sa.String(length=36), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("start_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("length_seconds", sa.Integer(), nullable=False),
        sa.Column("stored_file_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed')",
            name="ck_sample_derivatives_status",
        ),
        sa.CheckConstraint("start_seconds >= 0", name="ck_sample_derivatives_start"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stored_file_id"], ["stored_files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "track_id",
            "source_sha256",
            "start_seconds",
            "length_seconds",
            name="uq_sample_derivatives_identity",
        ),
    )
    op.create_index("ix_sample_derivatives_stored_file", "sample_derivatives", ["stored_file_id"])


def _create_purchase_artifacts() -> None:
    if table_exists("purchase_artifacts"):
        return
    op.create_table(
        "purchase_artifacts",
        _id_column(),
        sa.Column("order_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("stored_file_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["order_id"], ["payment_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stored_file_id"], ["stored_files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "snapshot_hash", name="uq_purchase_artifacts_order_snapshot"),
    )
    op.create_index("ix_purchase_artifacts_stored_file", "purchase_artifacts", ["stored_file_id"])


def _add_user_columns() -> None:
    if not column_exists("users", "payments_required"):
        op.add_column(
            "users",
            sa.Column("payments_required", sa.Boolean(), nullable=False, server_default="0"),
        )
    if not column_exists("users", "admin_suspended"):
        op.add_column(
            "users",
            sa.Column("admin_suspended", sa.Boolean(), nullable=False, server_default="0"),
        )


def _widen_notification_types() -> None:
    if table_exists("notifications"):
        with op.batch_alter_table("notifications", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_notifications_type", type_="check")
            batch_op.create_check_constraint("ck_notifications_type", _NEW_TYPE_CHECK)
    if table_exists("notification_preferences"):
        with op.batch_alter_table("notification_preferences", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_notification_preferences_type", type_="check")
            batch_op.create_check_constraint("ck_notification_preferences_type", _NEW_TYPE_CHECK)


def upgrade() -> None:
    """Create the payments schema and user flags."""
    _create_sales()
    _create_payment_orders()
    _create_payment_order_items()
    _create_payment_events()
    _create_purchase_entitlements()
    _create_redeem_capabilities()
    _create_connected_accounts()
    _create_instance_subscriptions()
    _create_fulfillment_outbox()
    _create_sample_derivatives()
    _create_purchase_artifacts()
    _add_user_columns()
    _widen_notification_types()


def downgrade() -> None:
    """Drop the payments schema and user flags."""
    if table_exists("notification_preferences"):
        with op.batch_alter_table("notification_preferences", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_notification_preferences_type", type_="check")
            batch_op.create_check_constraint("ck_notification_preferences_type", _OLD_TYPE_CHECK)
    if table_exists("notifications"):
        with op.batch_alter_table("notifications", recreate="always") as batch_op:
            batch_op.drop_constraint("ck_notifications_type", type_="check")
            batch_op.create_check_constraint("ck_notifications_type", _OLD_TYPE_CHECK)
    for table in (
        "purchase_artifacts",
        "sample_derivatives",
        "fulfillment_outbox",
        "instance_subscriptions",
        "connected_accounts",
        "redeem_capabilities",
        "purchase_entitlements",
        "payment_events",
        "payment_order_items",
        "payment_orders",
        "sales",
    ):
        if table_exists(table):
            op.drop_table(table)
    if column_exists("users", "admin_suspended"):
        op.drop_column("users", "admin_suspended")
    if column_exists("users", "payments_required"):
        op.drop_column("users", "payments_required")
