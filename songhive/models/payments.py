"""
Payments models.

Sales describe what is for sale (a track or an album), how much it costs, and
what unpaid listeners get (a full stream, a sample, or nothing). Orders record
a provider checkout attempt; entitlements record the permanent rights a paid
order granted. Provider webhook deliveries are journaled in ``payment_events``
for idempotent processing, guest buyers receive bounded ``redeem_capabilities``,
and the paid-registration membership state lives in ``instance_subscriptions``.

Money is always stored as integer minor units (``*_minor``) paired with an
ISO-4217 currency code. Provider object ids are stored verbatim; bearer tokens
and webhook payloads are never persisted — ``PaymentEvent`` keeps only the
delivery identity and processing status.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TZDateTime

SALE_ENTITY_TYPES = ("track", "album")
SALE_STATUSES = ("draft", "active", "inactive")
UNPAID_POLICIES = ("full_stream", "sample", "none")
SAMPLE_RENDER_POLICIES = ("materialized", "on_demand")
ORDER_KINDS = ("purchase", "membership")
ORDER_STATUSES = (
    "pending",
    "paid",
    "failed",
    "expired",
    "refunded",
    "partially_refunded",
    "disputed",
    "canceled",
)
PAYMENT_EVENT_SCOPES = ("platform", "connect")
PAYMENT_EVENT_STATUSES = ("pending", "processed", "failed", "ignored")
ENTITLEMENT_STATUSES = ("active", "revoked")
CAPABILITY_KINDS = ("download",)
SUBSCRIPTION_STATUSES = (
    "none",
    "incomplete",
    "active",
    "trialing",
    "past_due",
    "canceled",
    "unpaid",
    "incomplete_expired",
)
FULFILLMENT_KINDS = ("guest_email",)
FULFILLMENT_STATUSES = ("pending", "sent", "failed")
SAMPLE_DERIVATIVE_STATUSES = ("pending", "ready", "failed")

_ENTITY_TYPE_CHECK = f"entity_type IN ({', '.join(repr(v) for v in SALE_ENTITY_TYPES)})"
_SALE_STATUS_CHECK = f"status IN ({', '.join(repr(v) for v in SALE_STATUSES)})"
_UNPAID_POLICY_CHECK = f"unpaid_policy IN ({', '.join(repr(v) for v in UNPAID_POLICIES)})"
_SAMPLE_RENDER_CHECK = f"sample_render_policy IN ({', '.join(repr(v) for v in SAMPLE_RENDER_POLICIES)})"
_ORDER_KIND_CHECK = f"kind IN ({', '.join(repr(v) for v in ORDER_KINDS)})"
_ORDER_STATUS_CHECK = f"status IN ({', '.join(repr(v) for v in ORDER_STATUSES)})"
_EVENT_SCOPE_CHECK = f"scope IN ({', '.join(repr(v) for v in PAYMENT_EVENT_SCOPES)})"
_EVENT_STATUS_CHECK = f"status IN ({', '.join(repr(v) for v in PAYMENT_EVENT_STATUSES)})"
_ENTITLEMENT_STATUS_CHECK = f"status IN ({', '.join(repr(v) for v in ENTITLEMENT_STATUSES)})"
_CAPABILITY_KIND_CHECK = f"kind IN ({', '.join(repr(v) for v in CAPABILITY_KINDS)})"
_SUBSCRIPTION_STATUS_CHECK = f"status IN ({', '.join(repr(v) for v in SUBSCRIPTION_STATUSES)})"
_FULFILLMENT_STATUS_CHECK = f"status IN ({', '.join(repr(v) for v in FULFILLMENT_STATUSES)})"
_SAMPLE_STATUS_CHECK = f"status IN ({', '.join(repr(v) for v in SAMPLE_DERIVATIVE_STATUSES)})"


class Sale(Base):
    """
    A published or draft offer for a track or album.

    ``album_snapshot_track_ids`` freezes the membership of an album sale at
    publish time: existing purchases keep their snapshot even when tracks are
    later added to or removed from the album. For track sales the field stays
    ``None``.
    """

    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint(_ENTITY_TYPE_CHECK, name="ck_sales_entity_type"),
        CheckConstraint(_SALE_STATUS_CHECK, name="ck_sales_status"),
        CheckConstraint(_UNPAID_POLICY_CHECK, name="ck_sales_unpaid_policy"),
        CheckConstraint(_SAMPLE_RENDER_CHECK, name="ck_sales_sample_render_policy"),
        CheckConstraint(
            "(entity_type = 'track' AND track_id IS NOT NULL AND album_id IS NULL) OR "
            "(entity_type = 'album' AND album_id IS NOT NULL AND track_id IS NULL)",
            name="ck_sales_entity_ref",
        ),
        CheckConstraint("price_minor >= 0", name="ck_sales_price_minor"),
        CheckConstraint("sample_start_seconds >= 0", name="ck_sales_sample_start"),
        Index("ix_sales_owner_id", "owner_id"),
        Index("ix_sales_track_status", "track_id", "status"),
        Index("ix_sales_album_status", "album_id", "status"),
        Index(
            "uq_sales_active_track",
            "track_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_sales_active_album",
            "album_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(16))
    track_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), nullable=True)
    album_id: Mapped[Optional[str]] = mapped_column(ForeignKey("albums.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    unpaid_policy: Mapped[str] = mapped_column(String(16), default="sample", server_default="sample")
    sample_start_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sample_length_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sample_render_policy: Mapped[str] = mapped_column(String(16), default="materialized", server_default="materialized")
    album_snapshot_track_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    provider_price_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class PaymentOrder(Base):
    """
    A checkout attempt for an artist sale (``purchase``) or an instance
    membership (``membership``).

    ``buyer_email_encrypted`` holds the guest email as a Fernet token keyed by
    ``buyer_email_key_id`` so rotating the encryption key does not strand older
    orders. ``checkout_token`` is an opaque idempotency key embedded in the
    provider session metadata.
    """

    __tablename__ = "payment_orders"
    __table_args__ = (
        CheckConstraint(_ORDER_KIND_CHECK, name="ck_payment_orders_kind"),
        CheckConstraint(_ORDER_STATUS_CHECK, name="ck_payment_orders_status"),
        CheckConstraint("total_minor >= 0", name="ck_payment_orders_total_minor"),
        Index("ix_payment_orders_sale_id", "sale_id"),
        Index("ix_payment_orders_buyer", "buyer_user_id"),
        Index("ix_payment_orders_status_created", "status", "created_at"),
        Index("ix_payment_orders_provider_session", "provider_name", "provider_session_id"),
        Index("ix_payment_orders_provider_payment", "provider_name", "provider_payment_id"),
        UniqueConstraint("provider_name", "checkout_token", name="uq_payment_orders_checkout_token"),
    )

    kind: Mapped[str] = mapped_column(String(16))
    sale_id: Mapped[Optional[str]] = mapped_column(ForeignKey("sales.id", ondelete="SET NULL"), nullable=True)
    buyer_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    buyer_email_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    buyer_email_key_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    currency: Mapped[str] = mapped_column(String(3))
    total_minor: Mapped[int] = mapped_column(Integer)
    fee_minor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    checkout_token: Mapped[str] = mapped_column(String(128))
    provider_name: Mapped[str] = mapped_column(String(32))
    provider_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    fulfillment_started_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)


class PaymentOrderItem(Base):
    """One purchased item: the immutable snapshot of what an order covered."""

    __tablename__ = "payment_order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "track_id", name="uq_payment_order_items_order_track"),
        Index("ix_payment_order_items_track", "track_id"),
    )

    order_id: Mapped[str] = mapped_column(ForeignKey("payment_orders.id", ondelete="CASCADE"))
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    price_minor: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class PaymentEvent(Base):
    """
    A provider webhook delivery accepted for processing.

    Only the event identity, type, scope and processing state are stored —
    never the raw payload, which may contain payer PII. The unique
    ``(provider, provider_event_id)`` pair makes delivery idempotent.
    """

    __tablename__ = "payment_events"
    __table_args__ = (
        CheckConstraint(_EVENT_SCOPE_CHECK, name="ck_payment_events_scope"),
        CheckConstraint(_EVENT_STATUS_CHECK, name="ck_payment_events_status"),
        UniqueConstraint("provider", "provider_event_id", name="uq_payment_events_provider_event"),
        Index("ix_payment_events_status", "status"),
        Index("ix_payment_events_order_id", "order_id"),
    )

    provider: Mapped[str] = mapped_column(String(32))
    provider_event_id: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(128))
    order_id: Mapped[Optional[str]] = mapped_column(ForeignKey("payment_orders.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    # Normalized handler fields (ids/status/money only — never the raw
    # provider payload, which may contain payer PII).
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class PurchaseEntitlement(Base):
    """
    A permanent right to stream and download one purchased track.

    Guests are keyed by ``email_hash`` (SHA-256 of the normalized address — the
    plaintext lives on the order); registered buyers by ``user_id``. Refunds
    and chargebacks flip ``status`` to ``revoked``.
    """

    __tablename__ = "purchase_entitlements"
    __table_args__ = (
        CheckConstraint(_ENTITLEMENT_STATUS_CHECK, name="ck_purchase_entitlements_status"),
        UniqueConstraint("order_id", "track_id", name="uq_purchase_entitlements_order_track"),
        Index("ix_purchase_entitlements_user_track", "user_id", "track_id", "status"),
        Index("ix_purchase_entitlements_track", "track_id", "status"),
    )

    order_id: Mapped[str] = mapped_column(ForeignKey("payment_orders.id", ondelete="CASCADE"))
    sale_id: Mapped[Optional[str]] = mapped_column(ForeignKey("sales.id", ondelete="SET NULL"), nullable=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    email_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    revoke_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class RedeemCapability(Base):
    """
    A bounded download capability granted to a buyer — most commonly the link
    emailed to a guest purchaser.

    Only ``token_hash`` is stored; the raw token exists solely in the emailed
    URL. Redemption is bounded by ``max_redemptions`` and ``expires_at`` and
    stops working once the order's entitlements are revoked.
    """

    __tablename__ = "redeem_capabilities"
    __table_args__ = (
        CheckConstraint(_CAPABILITY_KIND_CHECK, name="ck_redeem_capabilities_kind"),
        Index("ix_redeem_capabilities_order", "order_id"),
    )

    order_id: Mapped[str] = mapped_column(ForeignKey("payment_orders.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16), default="download", server_default="download")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    max_redemptions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    redemption_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_redeemed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)


class ConnectedAccount(Base):
    """A seller's payment-provider account link (Stripe Connect)."""

    __tablename__ = "connected_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_connected_accounts_user"),
        UniqueConstraint("provider", "provider_account_id", name="uq_connected_accounts_provider_account"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32), default="stripe", server_default="stripe")
    provider_account_id: Mapped[str] = mapped_column(String(255))
    charges_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    payouts_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    details_submitted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    onboarded_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)


class InstanceSubscription(Base):
    """
    A user's instance-membership subscription under paid registration.

    ``paid_through`` is the provider-confirmed paid-up timestamp; it is only
    ever moved forward by verified webhook events or provider API reads, never
    by browser redirects.
    """

    __tablename__ = "instance_subscriptions"
    __table_args__ = (
        CheckConstraint(_SUBSCRIPTION_STATUS_CHECK, name="ck_instance_subscriptions_status"),
        UniqueConstraint("user_id", name="uq_instance_subscriptions_user"),
        Index("ix_instance_subscriptions_customer", "provider_customer_id"),
        Index("ix_instance_subscriptions_subscription", "provider_subscription_id"),
        Index("ix_instance_subscriptions_paid_through", "paid_through"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32), default="stripe", server_default="stripe")
    provider_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="none", server_default="none")
    paid_through: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
    last_invoice_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class FulfillmentOutbox(Base):
    """
    Durable record of a fulfillment side effect (e.g. the guest download
    email), so a crashed webhook handler can be retried without losing the
    delivery or duplicating it.
    """

    __tablename__ = "fulfillment_outbox"
    __table_args__ = (
        CheckConstraint(_FULFILLMENT_STATUS_CHECK, name="ck_fulfillment_outbox_status"),
        UniqueConstraint("order_id", "kind", name="uq_fulfillment_outbox_order_kind"),
        Index("ix_fulfillment_outbox_status", "status", "scheduled_at"),
    )

    order_id: Mapped[str] = mapped_column(ForeignKey("payment_orders.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        TZDateTime(), default=lambda: datetime.now(timezone.utc), server_default=text("now()")
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)


class SampleDerivative(Base):
    """
    A materialized audio sample for a sale gated with ``unpaid_policy=sample``.

    Identity is ``(track, source audio sha256, start, length)`` so changing the
    source file or the configured window produces a distinct derivative and
    stale samples can be garbage-collected.
    """

    __tablename__ = "sample_derivatives"
    __table_args__ = (
        CheckConstraint(_SAMPLE_STATUS_CHECK, name="ck_sample_derivatives_status"),
        CheckConstraint("start_seconds >= 0", name="ck_sample_derivatives_start"),
        UniqueConstraint(
            "track_id",
            "source_sha256",
            "start_seconds",
            "length_seconds",
            name="uq_sample_derivatives_identity",
        ),
        Index("ix_sample_derivatives_stored_file", "stored_file_id"),
    )

    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    source_sha256: Mapped[str] = mapped_column(String(64))
    start_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    length_seconds: Mapped[int] = mapped_column(Integer)
    stored_file_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")


class PurchaseArtifact(Base):
    """
    A cached fulfillment artifact — the ZIP a buyer downloads for an album
    purchase. ``snapshot_hash`` fingerprints the order's snapshot so changed
    snapshots invalidate the cache; revoked orders lose their artifacts.
    """

    __tablename__ = "purchase_artifacts"
    __table_args__ = (
        UniqueConstraint("order_id", "snapshot_hash", name="uq_purchase_artifacts_order_snapshot"),
        Index("ix_purchase_artifacts_stored_file", "stored_file_id"),
    )

    order_id: Mapped[str] = mapped_column(ForeignKey("payment_orders.id", ondelete="CASCADE"))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    stored_file_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime(), nullable=True)
