"""
Instance membership lifecycle for paid registration.

``is_active`` remains the enforcement primitive everywhere; this module is the
single writer that derives it from payment state:

    active := not admin_suspended
              and (not payments_required or subscription.paid_through + grace > now)

Webhooks, the hourly expiry sweep, and admin controls all funnel through
:func:`sync_user_active_flag` so a late ``invoice.paid`` can never re-activate
an admin-suspended account and clearing ``payments_required`` re-activates
cleanly.

The billing capability is a short-lived Redis credential issued to a user who
authenticated but is inactive for payment reasons — it authorizes *only* the
``/api/v1/payments/membership/*`` endpoints, never anything else.
"""

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...federation import doc_cache
from ...models.notification import NotificationType
from ...models.payments import InstanceSubscription
from ...models.user import User
from ..notifications import create_notification

logger = logging.getLogger(__name__)

_BILLING_KEY_PREFIX = "songhive:billing:"
# Notification source anchors — informational links, never bearer tokens.
_SOURCE_RENEW = "/settings?tab=billing"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Effective-account predicate — the single source of truth for is_active.
# ---------------------------------------------------------------------------


def effective_account_active(
    user: User,
    subscription: Optional[InstanceSubscription],
    *,
    now: Optional[datetime] = None,
    grace_hours: int = 0,
) -> bool:
    """
    Return whether the account should be usable.

    An admin suspension always wins. A ``payments_required`` user is active
    while their provider-confirmed ``paid_through`` (plus the configured
    grace window) is in the future.
    """
    if user.admin_suspended:
        return False
    if not user.payments_required:
        return True
    # Payment alone does not activate an account whose email is unverified —
    # when verification is required the column stays False until confirmed.
    if not user.email_verified:
        return False
    if subscription is None or subscription.paid_through is None:
        return False
    horizon = subscription.paid_through + timedelta(hours=grace_hours)
    return horizon > (now or _utcnow())


async def get_subscription(session: AsyncSession, user_id: str) -> Optional[InstanceSubscription]:
    """Return the user's membership subscription row, if any."""
    result = await session.execute(select(InstanceSubscription).where(InstanceSubscription.user_id == user_id))
    return result.scalar_one_or_none()


async def get_or_create_subscription(session: AsyncSession, user_id: str) -> InstanceSubscription:
    """Return (creating on demand) the user's subscription row."""
    subscription = await get_subscription(session, user_id)
    if subscription is None:
        subscription = InstanceSubscription(user_id=user_id)
        session.add(subscription)
        await session.flush()
    return subscription


async def sync_user_active_flag(
    session: AsyncSession,
    user: User,
    *,
    config: Optional[SonghiveConfig] = None,
    now: Optional[datetime] = None,
    notify: bool = False,
) -> bool:
    """
    Recompute ``user.is_active`` from payment state and write it if it changed.

    This is the *only* function allowed to write ``User.is_active`` for
    payment reasons. ``admin_suspended`` is sticky: deactivation must set it
    (not just ``is_active``) so a later webhook cannot silently re-enable the
    account.
    """
    subscription = await get_subscription(session, user.id)
    grace_hours = config.payments.membership_grace_hours if config is not None else 0
    active = effective_account_active(user, subscription, now=now, grace_hours=grace_hours)
    if bool(user.is_active) == active:
        return active

    was_active = bool(user.is_active)
    user.is_active = active
    await session.flush()

    # A deactivated user stops federating (dereference endpoints 404) —
    # drop every cached document: track/activity/entity documents are not
    # keyed by username.
    doc_cache.clear(session=session)
    logger.info(
        "User %s is_active %s -> %s (payments_required=%s admin_suspended=%s)",
        user.id,
        was_active,
        active,
        user.payments_required,
        user.admin_suspended,
    )

    if notify and was_active and not active:
        await create_notification(
            session,
            user_id=user.id,
            type=NotificationType.MEMBERSHIP,
            source_url=_SOURCE_RENEW,
            payload={"event": "membership_expired"},
        )
    return active


# ---------------------------------------------------------------------------
# Billing capability — a scoped credential for inactive unpaid users.
# ---------------------------------------------------------------------------


def _billing_key(token: str) -> str:
    return f"{_BILLING_KEY_PREFIX}{token}"


async def create_billing_capability(redis: Redis, user: User, config: SonghiveConfig) -> str:
    """Issue a short-lived token authorizing membership billing endpoints."""
    token = secrets.token_urlsafe(32)
    await redis.set(
        _billing_key(token),
        json.dumps({"user_id": str(user.id)}),
        ex=config.payments.billing_capability_ttl_seconds,
    )
    return token


async def resolve_billing_capability(redis: Redis, token: str) -> Optional[str]:
    """Return the user id a billing token authorizes, or ``None``."""
    raw = await redis.get(_billing_key(token))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    user_id = data.get("user_id")
    return str(user_id) if user_id else None


async def revoke_billing_capability(redis: Redis, token: str) -> None:
    """Drop a billing token early (e.g. once the subscription activates)."""
    await redis.delete(_billing_key(token))


# ---------------------------------------------------------------------------
# Provider-driven lifecycle transitions.
# ---------------------------------------------------------------------------


async def apply_invoice_paid(
    session: AsyncSession,
    subscription: InstanceSubscription,
    *,
    paid_until: datetime,
    invoice_id: Optional[str] = None,
) -> None:
    """
    Extend ``paid_through`` from a verified invoice payment.

    ``paid_through`` only ever moves forward — out-of-order or replayed
    invoices with an earlier period end are ignored.
    """
    if subscription.paid_through is not None and paid_until <= subscription.paid_through:
        if invoice_id:
            subscription.last_invoice_id = invoice_id
        return
    subscription.paid_through = paid_until
    subscription.status = "active"
    if invoice_id:
        subscription.last_invoice_id = invoice_id


async def request_cancel(
    session: AsyncSession,
    user: User,
    config: SonghiveConfig,
    *,
    immediate: bool = False,
) -> InstanceSubscription:
    """
    Cancel the user's provider subscription.

    ``immediate=False`` asks the provider to cancel at the paid period end —
    the user keeps access until ``paid_through`` lapses and the expiry sweep
    runs. ``immediate=True`` is for admin deletion paths where billing must
    stop now.
    """
    from .providers import get_provider

    subscription = await get_subscription(session, user.id)
    if subscription is None or not subscription.provider_subscription_id:
        raise LookupError("No subscription to cancel")

    provider = get_provider(config)
    state = await provider.cancel_subscription(subscription.provider_subscription_id, at_period_end=not immediate)
    subscription.status = state.status
    subscription.cancel_at_period_end = state.cancel_at_period_end
    if state.paid_through is not None:
        subscription.paid_through = state.paid_through
    if immediate:
        subscription.paid_through = None
    await session.flush()
    return subscription


async def cancel_for_deactivated_user(
    session: AsyncSession,
    user: User,
    config: SonghiveConfig,
) -> bool:
    """
    Durably cancel billing for a user being deactivated or deleted.

    Returns ``True`` when no provider cancellation remains outstanding —
    either there is nothing to cancel, or the provider confirmed the cancel.
    When the provider call fails the intent is retried by the
    ``cancel_provider_subscription`` task.
    """
    subscription = await get_subscription(session, user.id)
    if subscription is None or not subscription.provider_subscription_id:
        return True
    if subscription.status in ("canceled", "none", "incomplete_expired"):
        return True

    try:
        await request_cancel(session, user, config, immediate=True)
    except Exception as exc:  # noqa: BLE001 — provider errors retry via task
        logger.warning("Provider cancel for user %s deferred to task: %s", user.id, exc)
        _enqueue_cancel_provider_subscription(subscription.provider_subscription_id)
        return False
    return True


def _enqueue_cancel_provider_subscription(subscription_id: str) -> None:
    from ...tasks.payments import cancel_provider_subscription

    try:
        cancel_provider_subscription.delay(subscription_id)  # type: ignore[attr-defined]
    except Exception as exc:  # broker errors must not fail the caller
        logger.error("Could not enqueue subscription cancel for %s: %s", subscription_id, exc)


async def notify_membership(
    session: AsyncSession,
    user_id: str,
    event: str,
    *,
    payload: Optional[dict] = None,
) -> None:
    """Emit a membership lifecycle notification (in-app + email per prefs)."""
    body = {"event": event}
    if payload:
        body.update(payload)
    await create_notification(
        session,
        user_id=user_id,
        type=NotificationType.MEMBERSHIP,
        source_url=_SOURCE_RENEW,
        payload=body,
    )
