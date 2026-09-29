"""
Webhook event handlers — translate verified provider events into local state.

Each handler is idempotent: order and subscription transitions check the
current row state before mutating, entitlements are guarded by the
``(order_id, track_id)`` unique constraint semantics in
``fulfillment.mark_order_paid``, and ``paid_through`` only ever moves forward.
Out-of-order delivery is therefore safe — a stale ``subscription.updated``
after a ``subscription.deleted`` reconciles to the provider's reported status.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.payments import (
    ConnectedAccount,
    InstanceSubscription,
    PaymentOrder,
)
from ...models.user import User
from . import fulfillment, membership
from .providers.base import ProviderEvent, connected_account_state

logger = logging.getLogger(__name__)


def _ts(value: Any) -> Optional[datetime]:
    """Convert a unix timestamp to a tz-aware datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


async def _order_by_session(session: AsyncSession, session_id: str) -> Optional[PaymentOrder]:
    result = await session.execute(select(PaymentOrder).where(PaymentOrder.provider_session_id == session_id))
    return result.scalar_one_or_none()


async def _order_by_payment(session: AsyncSession, payment_id: str) -> Optional[PaymentOrder]:
    result = await session.execute(select(PaymentOrder).where(PaymentOrder.provider_payment_id == payment_id))
    return result.scalar_one_or_none()


async def _order_by_metadata(session: AsyncSession, metadata: Dict[str, Any]) -> Optional[PaymentOrder]:
    order_id = metadata.get("songhive_order_id")
    token = metadata.get("songhive_checkout_token")
    if token:
        result = await session.execute(select(PaymentOrder).where(PaymentOrder.checkout_token == str(token)))
        order = result.scalar_one_or_none()
        if order is not None:
            return order
    if order_id:
        return await session.get(PaymentOrder, str(order_id))
    return None


async def _subscription_by_provider_id(session: AsyncSession, subscription_id: str) -> Optional[InstanceSubscription]:
    result = await session.execute(
        select(InstanceSubscription).where(InstanceSubscription.provider_subscription_id == subscription_id)
    )
    return result.scalar_one_or_none()


async def _subscription_by_customer(session: AsyncSession, customer_id: str) -> Optional[InstanceSubscription]:
    result = await session.execute(
        select(InstanceSubscription).where(InstanceSubscription.provider_customer_id == customer_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Purchase / checkout events (Connect scope — the seller's account).
# ---------------------------------------------------------------------------


async def handle_checkout_completed(session: AsyncSession, event: ProviderEvent, config: SonghiveConfig) -> None:
    """
    ``checkout.session.completed`` — the payment-confirmed moment.

    Matches the session to its order by ``provider_session_id`` (persisted at
    checkout creation), verifies ``payment_status == "paid"`` and the paid
    total, then grants entitlements. Membership checkouts attach the new
    provider subscription id to the user's ``InstanceSubscription`` row; the
    actual activation happens on ``invoice.paid``.
    """
    data = event.data
    session_id = str(data.get("id") or "")
    metadata = data.get("metadata") or {}

    order = await _order_by_session(session, session_id)
    if order is None:
        order = await _order_by_metadata(session, metadata)
    if order is None:
        logger.warning("checkout.session.completed for unknown session %s", session_id)
        return

    payment_status = str(data.get("payment_status") or "")
    if payment_status not in ("paid", "no_payment_required"):
        logger.info(
            "Checkout session %s completed without payment (%s); leaving order pending",
            session_id,
            payment_status,
        )
        return

    if order.kind == "membership":
        await _attach_membership_subscription(session, order, data, config)
        return

    paid_minor = data.get("amount_total")
    currency = data.get("currency")
    payment_intent = data.get("payment_intent")
    granted = await fulfillment.mark_order_paid(
        session,
        order,
        provider_payment_id=str(payment_intent) if payment_intent else None,
        paid_minor=int(paid_minor) if paid_minor is not None else None,
        paid_currency=str(currency) if currency else None,
    )
    if granted:
        logger.info("Order %s fulfilled via checkout.session.completed", order.id)


async def _attach_membership_subscription(
    session: AsyncSession,
    order: PaymentOrder,
    data: Dict[str, Any],
    config: SonghiveConfig,
) -> None:
    """Bind the completed membership checkout to the user's subscription row."""
    order.status = "paid"
    if data.get("payment_intent"):
        order.provider_payment_id = str(data["payment_intent"])
    if order.buyer_user_id is None:
        return

    user = await session.get(User, order.buyer_user_id)
    if user is None:
        return
    subscription = await membership.get_or_create_subscription(session, user.id)
    provider_sub = data.get("subscription")
    if provider_sub:
        subscription.provider_subscription_id = str(provider_sub)
    customer = data.get("customer")
    if customer:
        subscription.provider_customer_id = str(customer)
    # Stripe delivers invoice.paid and checkout.session.completed in either
    # order — never downgrade a status the paid invoice already advanced.
    if subscription.status not in ("active", "trialing"):
        subscription.status = "incomplete"
    await session.flush()


async def handle_charge_refunded(session: AsyncSession, event: ProviderEvent, config: SonghiveConfig) -> None:
    """``charge.refunded`` — revoke entitlements when fully refunded."""
    data = event.data
    payment_intent = data.get("payment_intent")
    order = await _order_by_payment(session, str(payment_intent or ""))
    if order is None:
        logger.info("charge.refunded for unknown payment %s", payment_intent)
        return

    amount = int(data.get("amount") or 0)
    amount_refunded = int(data.get("amount_refunded") or 0)
    fully = bool(data.get("refunded")) or (amount > 0 and amount_refunded >= amount)

    revoked = await fulfillment.revoke_order_entitlements(session, order, reason="refund", fully=fully)
    logger.info("Order %s refund: fully=%s revoked=%d entitlements", order.id, fully, revoked)


async def handle_dispute(session: AsyncSession, event: ProviderEvent, config: SonghiveConfig) -> None:
    """
    ``charge.dispute.created``/``closed`` — a lost dispute revokes access.

    A created dispute marks the order ``disputed`` (access preserved while
    contested); a dispute closed in the buyer's favour revokes.
    """
    data = event.data
    payment_intent = data.get("payment_intent")
    order = await _order_by_payment(session, str(payment_intent or ""))
    if order is None:
        logger.info("dispute event for unknown payment %s", payment_intent)
        return

    if event.type == "charge.dispute.closed":
        dispute_status = str(data.get("status") or "")
        if dispute_status in ("lost", "warning_closed"):
            order.status = "disputed"
            await fulfillment.revoke_order_entitlements(session, order, reason="dispute_lost", fully=True)
        else:
            order.status = "paid"  # won — restore
        return
    order.status = "disputed"


# ---------------------------------------------------------------------------
# Membership / subscription events (platform scope — our own account).
# ---------------------------------------------------------------------------


async def handle_invoice_paid(session: AsyncSession, event: ProviderEvent, config: SonghiveConfig) -> None:
    """
    ``invoice.paid`` — extend the member's ``paid_through`` and activate.

    Activation honours email verification: a user who has not verified yet
    keeps ``is_active=False`` until both conditions hold (the flag is
    re-synced on verification via ``sync_user_active_flag``).
    """
    data = event.data
    subscription_id = data.get("subscription")
    customer_id = data.get("customer")

    row = None
    if subscription_id:
        row = await _subscription_by_provider_id(session, str(subscription_id))
    if row is None and customer_id:
        row = await _subscription_by_customer(session, str(customer_id))
    if row is None:
        logger.info("invoice.paid for unknown subscription %s", subscription_id)
        return

    if subscription_id and not row.provider_subscription_id:
        row.provider_subscription_id = str(subscription_id)
    if customer_id and not row.provider_customer_id:
        row.provider_customer_id = str(customer_id)

    period_end = _invoice_period_end(data)
    if period_end is not None:
        await membership.apply_invoice_paid(
            session,
            row,
            paid_until=period_end,
            invoice_id=str(data.get("id") or "") or None,
        )
    else:
        # No period info in the payload — mark the row paid and let
        # reconciliation fill in ``paid_through`` from the provider.
        row.status = "active"
        logger.warning(
            "invoice.paid %s carried no period end; paid_through deferred to reconcile",
            data.get("id"),
        )

    user = await session.get(User, row.user_id)
    if user is not None:
        await membership.sync_user_active_flag(session, user, config=config)
        await membership.notify_membership(session, user.id, "membership_paid")


def _invoice_period_end(invoice: Dict[str, Any]) -> Optional[datetime]:
    """Extract the subscription period end from an invoice payload."""
    lines = invoice.get("lines") or {}
    for line in lines.get("data") or []:
        period = line.get("period") or {}
        end = _ts(period.get("end"))
        if end is not None:
            return end
    return _ts(invoice.get("period_end"))


async def handle_invoice_failed(session: AsyncSession, event: ProviderEvent, config: SonghiveConfig) -> None:
    """``invoice.payment_failed`` — record the failure; access ends at paid_through + grace."""
    data = event.data
    subscription_id = data.get("subscription")
    customer_id = data.get("customer")

    row = None
    if subscription_id:
        row = await _subscription_by_provider_id(session, str(subscription_id))
    if row is None and customer_id:
        row = await _subscription_by_customer(session, str(customer_id))
    if row is None:
        logger.info("invoice.payment_failed for unknown subscription %s", subscription_id)
        return

    row.status = "past_due"
    row.last_invoice_id = str(data.get("id") or "") or row.last_invoice_id
    await session.flush()

    user = await session.get(User, row.user_id)
    if user is not None:
        await membership.sync_user_active_flag(session, user, config=config)
        await membership.notify_membership(session, user.id, "membership_payment_failed")


async def handle_subscription_updated(session: AsyncSession, event: ProviderEvent, config: SonghiveConfig) -> None:
    """``customer.subscription.updated`` — reconcile status, cancel flag, period end."""
    data = event.data
    subscription_id = str(data.get("id") or "")
    row = await _subscription_by_provider_id(session, subscription_id)
    if row is None:
        customer_id = data.get("customer")
        if customer_id:
            row = await _subscription_by_customer(session, str(customer_id))
    if row is None:
        logger.info("subscription.updated for unknown subscription %s", subscription_id)
        return

    row.provider_subscription_id = subscription_id
    customer = data.get("customer")
    if customer:
        row.provider_customer_id = str(customer)
    status = str(data.get("status") or "")
    if status:
        row.status = status
    row.cancel_at_period_end = bool(data.get("cancel_at_period_end"))

    period_end = _subscription_period_end(data)
    if period_end is not None and (row.paid_through is None or period_end > row.paid_through):
        row.paid_through = period_end
    if status in ("canceled", "incomplete_expired", "unpaid"):
        row.paid_through = None if status != "canceled" else row.paid_through
    await session.flush()

    user = await session.get(User, row.user_id)
    if user is not None:
        await membership.sync_user_active_flag(session, user, config=config)
        if row.cancel_at_period_end:
            await membership.notify_membership(session, user.id, "membership_cancel_scheduled")


def _subscription_period_end(subscription: Dict[str, Any]) -> Optional[datetime]:
    """Extract the current period end from a subscription payload."""
    items = subscription.get("items") or {}
    for item in items.get("data") or []:
        end = _ts(item.get("current_period_end"))
        if end is not None:
            return end
    return _ts(subscription.get("current_period_end"))


async def handle_subscription_deleted(session: AsyncSession, event: ProviderEvent, config: SonghiveConfig) -> None:
    """``customer.subscription.deleted`` — the subscription is over."""
    data = event.data
    subscription_id = str(data.get("id") or "")
    row = await _subscription_by_provider_id(session, subscription_id)
    if row is None:
        customer_id = data.get("customer")
        if customer_id:
            row = await _subscription_by_customer(session, str(customer_id))
    if row is None:
        logger.info("subscription.deleted for unknown subscription %s", subscription_id)
        return

    row.status = "canceled"
    row.cancel_at_period_end = False
    # paid_through stays — the member keeps access until the paid window ends.
    await session.flush()

    user = await session.get(User, row.user_id)
    if user is not None:
        await membership.sync_user_active_flag(session, user, config=config)
        await membership.notify_membership(session, user.id, "membership_canceled")


# ---------------------------------------------------------------------------
# Connected account events (Connect scope).
# ---------------------------------------------------------------------------


async def handle_account_updated(session: AsyncSession, event: ProviderEvent, config: SonghiveConfig) -> None:
    """``account.updated`` — refresh a seller's connected-account flags."""
    data = event.data
    account_id = str(data.get("id") or "")
    result = await session.execute(select(ConnectedAccount).where(ConnectedAccount.provider_account_id == account_id))
    account = result.scalar_one_or_none()
    if account is None:
        logger.info("account.updated for unknown connected account %s", account_id)
        return

    state = connected_account_state(account_id, data)
    account.charges_enabled = state.charges_enabled
    account.payouts_enabled = state.payouts_enabled
    account.details_submitted = state.details_submitted
    if account.details_submitted and account.onboarded_at is None:
        account.onboarded_at = datetime.now(timezone.utc)
    await session.flush()


# ---------------------------------------------------------------------------
# Dispatch.
# ---------------------------------------------------------------------------


_HANDLERS = {
    "checkout.session.completed": handle_checkout_completed,
    "charge.refunded": handle_charge_refunded,
    "charge.dispute.created": handle_dispute,
    "charge.dispute.closed": handle_dispute,
    "invoice.paid": handle_invoice_paid,
    "invoice.payment_failed": handle_invoice_failed,
    "customer.subscription.updated": handle_subscription_updated,
    "customer.subscription.deleted": handle_subscription_deleted,
    "account.updated": handle_account_updated,
}


async def dispatch_event(session: AsyncSession, event: ProviderEvent, config: SonghiveConfig) -> bool:
    """
    Route a verified event to its handler.

    Returns ``True`` when a handler ran (even as a no-op), ``False`` for
    event types we do not subscribe to — those are recorded ``ignored``.
    """
    handler = _HANDLERS.get(event.type)
    if handler is None:
        logger.info("Ignoring unhandled payment event type %s", event.type)
        return False
    await handler(session, event, config)
    return True
