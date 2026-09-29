"""
Payments Celery tasks: sample rendering, order expiry, membership sweep,
guest-fulfillment outbox delivery, provider-cancel retries, and cleanup.

All tasks follow the worker convention: ``asyncio.run`` a coroutine that uses
``get_session()`` and always ends with ``dispose_and_reset()`` (plus
``close_redis_client()`` when a Redis client was opened), because engine and
client connections are bound to the task's event loop.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..models.base import dispose_and_reset, get_session, init_db
from ..models.payments import (
    FulfillmentOutbox,
    InstanceSubscription,
    PaymentEvent,
    PaymentOrder,
    PurchaseArtifact,
    SampleDerivative,
)
from ..models.user import User
from .celery import celery_app

logger = logging.getLogger(__name__)

_OUTBOX_MAX_ATTEMPTS = 5
_OUTBOX_RETRY_MINUTES = 15


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Sample materialization (bulk queue — ffmpeg work).
# ---------------------------------------------------------------------------


@celery_app.task(name="songhive.tasks.payments.materialize_track_sample")
def materialize_track_sample(track_id: str) -> bool:
    """
    Render pending sample derivatives for a track and activate ready sales.

    Idempotent — re-runs find no ``pending`` derivatives and no-op.
    """
    from ..config import load_config

    config = load_config([])
    init_db(config.database.url)
    try:
        return asyncio.run(_materialize_track_sample(track_id, config))
    except Exception as exc:
        logger.exception("materialize_track_sample failed for %s: %s", track_id, exc)
        return False


async def _materialize_track_sample(track_id: str, config) -> bool:
    from ..services.payments import samples
    from ..services.redis import close_redis_client, get_redis_client
    from ..services.storage import StorageService
    from ..storage import get_storage

    backend = get_storage(config.storage)
    storage = StorageService(backend, config.storage)
    redis = get_redis_client(config)

    if not await samples.acquire_sample_lock(redis, track_id, config.payments.fulfillment_lock_ttl_seconds):
        logger.info("Sample build for track %s already in progress; skipping", track_id)
        return True

    try:
        async with get_session() as session:
            built = await samples.materialize_track_samples(session, track_id, storage, config)
            await session.commit()
            logger.info("Materialized %d sample(s) for track %s", built, track_id)
            return True
    finally:
        await samples.release_sample_lock(redis, track_id)
        await close_redis_client()
        await dispose_and_reset()


# ---------------------------------------------------------------------------
# Webhook event processing — the inbox drained one row at a time.
# ---------------------------------------------------------------------------


@celery_app.task(
    name="songhive.tasks.payments.process_payment_event",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=6,
)
def process_payment_event(event_row_id: str) -> bool:
    """
    Dispatch one persisted ``PaymentEvent`` to its handler.

    Handlers are idempotent; a ``processed`` row is a no-op on replay.
    ``failed`` rows are retried by celery and can be re-driven by the
    reconcile sweep.
    """
    from ..config import load_config

    config = load_config([])
    init_db(config.database.url)
    try:
        return asyncio.run(_process_payment_event(event_row_id, config))
    except Exception as exc:
        logger.exception("process_payment_event failed for %s: %s", event_row_id, exc)
        return False


async def _process_payment_event(event_row_id: str, config) -> bool:
    from ..services.payments import webhooks as webhook_service
    from ..services.payments.providers.base import ProviderEvent

    try:
        async with get_session() as session:
            row = await session.get(PaymentEvent, event_row_id)
            if row is None or row.status == "processed":
                return True

            row.attempts += 1
            row.processing_started_at = _utcnow()
            try:
                # The event row stores only the normalized fields — handlers
                # never see the raw provider payload.
                handled = await webhook_service.dispatch_event(
                    session,
                    ProviderEvent(
                        provider=row.provider,
                        event_id=row.provider_event_id,
                        type=row.type,
                        scope=row.scope,
                        data=_event_data(row),
                    ),
                    config,
                )
                row.status = "processed" if handled else "ignored"
            except Exception as exc:  # noqa: BLE001 — record and retry
                logger.exception("Payment event %s processing failed", row.id)
                row.last_error = str(exc)
                row.status = "failed" if row.attempts >= 6 else "pending"
                await session.commit()
                raise
            await session.commit()
            return True
    finally:
        await dispose_and_reset()


def _event_data(row: PaymentEvent) -> dict:
    """Rebuild a handler payload from the stored event row."""
    return dict(row.data) if row.data else {}


# ---------------------------------------------------------------------------
# Deferred provider subscription cancellation (user deactivate/delete paths).
# ---------------------------------------------------------------------------


@celery_app.task(
    name="songhive.tasks.payments.cancel_provider_subscription",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=8,
)
def cancel_provider_subscription(subscription_id: str) -> bool:
    """
    Retry a provider subscription cancellation that failed inline.

    Called when a user is deactivated or deleted and the provider call could
    not be completed — billing must not silently continue.
    """
    from ..config import load_config

    config = load_config([])
    init_db(config.database.url)
    try:
        return asyncio.run(_cancel_provider_subscription(subscription_id, config))
    except Exception as exc:
        logger.exception("cancel_provider_subscription failed for %s: %s", subscription_id, exc)
        return False


async def _cancel_provider_subscription(subscription_id: str, config) -> bool:
    from ..services.payments.providers import get_provider

    provider = get_provider(config)
    await provider.cancel_subscription(subscription_id, at_period_end=False)

    async with get_session() as session:
        result = await session.execute(
            select(InstanceSubscription).where(InstanceSubscription.provider_subscription_id == subscription_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.status = "canceled"
            row.paid_through = None
            await session.commit()
    await dispose_and_reset()
    return True


# ---------------------------------------------------------------------------
# Safety-net sweeps (beat-scheduled).
# ---------------------------------------------------------------------------


@celery_app.task(name="songhive.tasks.payments.sweep_membership_expirations")
def sweep_membership_expirations() -> int:
    """
    Hourly safety net: re-sync ``is_active`` for every payments-required user.

    Webhooks are authoritative but lossy transports exist; this catches a
    missed ``invoice.payment_failed``/subscription end so lapsed members go
    inactive, and re-syncs users whose ``paid_through`` advanced while they
    were inactive (e.g. paid during suspension clears).
    """
    from ..config import load_config

    config = load_config([])
    init_db(config.database.url)
    return asyncio.run(_sweep_membership_expirations(config))


async def _sweep_membership_expirations(config) -> int:
    from ..services.payments import membership

    changed = 0
    try:
        async with get_session() as session:
            result = await session.execute(select(User).where(User.payments_required.is_(True)))
            users = list(result.scalars().all())
            for user in users:
                before = bool(user.is_active)
                await membership.sync_user_active_flag(session, user, config=config)
                if bool(user.is_active) != before:
                    changed += 1
            await session.commit()
    finally:
        await dispose_and_reset()
    return changed


@celery_app.task(name="songhive.tasks.payments.expire_pending_orders")
def expire_pending_orders() -> int:
    """Expire pending checkout orders past their TTL and close provider sessions."""
    from ..config import load_config

    config = load_config([])
    init_db(config.database.url)
    return asyncio.run(_expire_pending_orders(config))


async def _expire_pending_orders(config) -> int:
    from ..services.payments.providers import get_provider

    provider = get_provider(config)
    now = _utcnow()
    expired = 0
    try:
        async with get_session() as session:
            result = await session.execute(
                select(PaymentOrder).where(
                    PaymentOrder.status == "pending",
                    PaymentOrder.expires_at.is_not(None),
                    PaymentOrder.expires_at <= now,
                )
            )
            for order in result.scalars().all():
                order.status = "expired"
                if order.provider_session_id:
                    try:
                        await provider.expire_checkout_session(order.provider_session_id)
                    except Exception as exc:  # noqa: BLE001 — expiry is best-effort
                        logger.info(
                            "Could not expire provider session %s: %s",
                            order.provider_session_id,
                            exc,
                        )
                expired += 1
            await session.commit()
    finally:
        await dispose_and_reset()
    return expired


@celery_app.task(name="songhive.tasks.payments.reconcile_pending_orders")
def reconcile_pending_orders() -> int:
    """
    Provider-confirmed fulfillment for ``pending`` orders.

    Webhooks are authoritative but lossy — a dropped ``checkout.session.completed``
    (or a connect webhook destination not subscribed to connected-account
    events) leaves a paid checkout pending forever. Poll the provider for each
    pending order's session and feed paid sessions through the same handler the
    webhook path uses; provider-expired sessions flip the order to ``expired``.
    """
    from ..config import load_config

    config = load_config([])
    init_db(config.database.url)
    return asyncio.run(_reconcile_pending_orders(config))


_RECONCILE_GRACE = timedelta(minutes=2)


async def _reconcile_pending_orders(config) -> int:
    from ..services.payments import payments_enabled
    from ..services.payments.providers import get_provider

    if not payments_enabled(config):
        return 0

    provider = get_provider(config)
    cutoff = _utcnow() - _RECONCILE_GRACE
    reconciled = 0
    try:
        async with get_session() as session:
            result = await session.execute(
                select(PaymentOrder).where(
                    PaymentOrder.status == "pending",
                    PaymentOrder.provider_session_id.is_not(None),
                    PaymentOrder.created_at <= cutoff,
                )
            )
            orders = list(result.scalars().all())
            for order in orders:
                try:
                    reconciled += await _reconcile_order(session, provider, order, config)
                except Exception as exc:  # noqa: BLE001 — per-order isolation
                    logger.warning(
                        "Reconcile: could not fetch session %s for order %s: %s",
                        order.provider_session_id,
                        order.id,
                        exc,
                    )
            await session.commit()
    finally:
        await dispose_and_reset()
    return reconciled


async def _reconcile_order(session, provider, order: PaymentOrder, config) -> int:
    """Poll one order's provider session; fulfill or expire it. Returns 1 on change."""
    from ..models.payments import Sale
    from ..services.payments import sales as sales_service
    from ..services.payments import webhooks as webhook_service
    from ..services.payments.providers.base import ProviderEvent

    account_id = None
    if order.kind == "purchase" and order.sale_id:
        # Purchase sessions are direct charges on the seller's connected
        # account — retrieval requires the connected-account context.
        sale = await session.get(Sale, order.sale_id)
        if sale is not None:
            seller = await sales_service.get_connected_account(session, sale.owner_id)
            if seller is not None:
                account_id = seller.provider_account_id

    data = await provider.retrieve_checkout_session(order.provider_session_id, account_id=account_id)
    if not data:
        return 0
    status = str(data.get("status") or "")
    if status == "expired":
        order.status = "expired"
        return 1
    if status != "complete":
        return 0

    before = order.status
    await webhook_service.handle_checkout_completed(
        session,
        ProviderEvent(
            provider=order.provider_name,
            event_id=f"reconcile:{order.provider_session_id}",
            type="checkout.session.completed",
            scope="connect" if order.kind == "purchase" else "platform",
            data=data,
        ),
        config,
    )
    changed = int(order.status != before)

    if order.kind == "membership" and order.status == "paid" and data.get("subscription"):
        # A lost ``invoice.paid`` leaves paid_through unset and the member
        # inactive — pull the authoritative subscription state directly.
        await _reconcile_membership_subscription(session, order, data, provider, config)
        changed = 1
    return changed


async def _reconcile_membership_subscription(
    session, order: PaymentOrder, session_data: dict, provider, config
) -> None:
    """Apply provider subscription state when the invoice.paid event was lost."""
    from ..services.payments import membership

    subscription_id = str(session_data.get("subscription") or "")
    if not subscription_id or order.buyer_user_id is None:
        return
    row = await membership.get_or_create_subscription(session, order.buyer_user_id)
    if not row.provider_subscription_id:
        row.provider_subscription_id = subscription_id
    customer = session_data.get("customer")
    if customer and not row.provider_customer_id:
        row.provider_customer_id = str(customer)
    if row.paid_through is not None:
        return  # webhook already applied invoice data — nothing stale to fix
    state = await provider.retrieve_subscription(subscription_id)
    row.status = state.status
    row.cancel_at_period_end = state.cancel_at_period_end
    if state.paid_through is not None:
        row.paid_through = state.paid_through
    user = await session.get(User, order.buyer_user_id)
    if user is not None:
        await membership.sync_user_active_flag(session, user, config=config)


@celery_app.task(name="songhive.tasks.payments.process_fulfillment_outbox")
def process_fulfillment_outbox() -> int:
    """
    Deliver pending fulfillment side effects (currently: guest redeem emails).

    Each pending row is issued a fresh redeem capability, emailed to the
    decrypted guest address, and marked ``sent``. Failures retry with a
    bounded attempt count before going ``failed``.
    """
    from ..config import load_config

    config = load_config([])
    init_db(config.database.url)
    return asyncio.run(_process_fulfillment_outbox(config))


async def _process_fulfillment_outbox(config) -> int:
    from ..services import email as email_service
    from ..services.email import EmailNotConfiguredError
    from ..services.payments import fulfillment, payments_enabled, public_base_url

    if not payments_enabled(config):
        return 0

    base_url = public_base_url(config)
    now = _utcnow()
    delivered = 0
    try:
        async with get_session() as session:
            result = await session.execute(
                select(FulfillmentOutbox).where(
                    FulfillmentOutbox.status == "pending",
                    FulfillmentOutbox.scheduled_at <= now,
                )
            )
            rows = list(result.scalars().all())
            for row in rows:
                row.attempts += 1
                try:
                    order = await session.get(PaymentOrder, row.order_id)
                    if order is None or order.status != "paid":
                        row.status = "failed"
                        row.last_error = "order not paid"
                        continue
                    guest_email = await fulfillment.order_guest_email(session, order)
                    if not guest_email or base_url is None:
                        row.status = "failed"
                        row.last_error = "no guest email or public base URL"
                        continue
                    token = await fulfillment.issue_redeem_capability(session, order, config, guest_email=guest_email)
                    title = await _order_title(session, order)
                    sent = email_service.send_purchase_redeem_email(
                        config,
                        guest_email,
                        fulfillment.redeem_url(base_url, token),
                        title,
                        config.payments.redeem_capability_ttl_days,
                    )
                    if not sent:
                        # SMTP failure returns False rather than raising —
                        # schedule a retry instead of reporting delivery.
                        raise RuntimeError("redeem email was not accepted by the SMTP server")
                    row.status = "sent"
                    row.completed_at = now
                    delivered += 1
                except EmailNotConfiguredError as exc:
                    row.last_error = str(exc)
                    if row.attempts >= _OUTBOX_MAX_ATTEMPTS:
                        row.status = "failed"
                except Exception as exc:  # noqa: BLE001 — per-row failure isolation
                    logger.warning("Fulfillment row %s failed: %s", row.id, exc)
                    row.last_error = str(exc)
                    row.scheduled_at = now + timedelta(minutes=_OUTBOX_RETRY_MINUTES)
                    if row.attempts >= _OUTBOX_MAX_ATTEMPTS:
                        row.status = "failed"
            await session.commit()
    finally:
        await dispose_and_reset()
    return delivered


async def _order_title(session, order: PaymentOrder) -> str:
    """Human-readable label for a purchased order (item title or fallback)."""
    from ..models.album import Album
    from ..models.payments import Sale
    from ..models.track import Track

    if order.sale_id:
        sale = await session.get(Sale, order.sale_id)
        if sale is not None:
            if sale.entity_type == "track" and sale.track_id:
                track = await session.get(Track, sale.track_id)
                if track is not None:
                    return f'"{track.title}"'
            if sale.entity_type == "album" and sale.album_id:
                album = await session.get(Album, sale.album_id)
                if album is not None:
                    return f'"{album.title}"'
    return "your purchase"


@celery_app.task(name="songhive.tasks.payments.cleanup_payments")
def cleanup_payments() -> int:
    """
    Reclaim storage held by expired purchase artifacts and failed samples.

    Artifact ``StoredFile`` rows are content-addressed — deleting the artifact
    row and file is safe; failed/stale ``SampleDerivative`` rows get their
    stored file removed then the row deleted.
    """
    from ..config import load_config

    config = load_config([])
    init_db(config.database.url)
    return asyncio.run(_cleanup_payments(config))


async def _cleanup_payments(config) -> int:
    from ..services.storage import StorageService
    from ..storage import get_storage

    backend = get_storage(config.storage)
    storage = StorageService(backend, config.storage)
    now = _utcnow()
    removed = 0
    try:
        async with get_session() as session:
            artifacts = (
                (
                    await session.execute(
                        select(PurchaseArtifact).where(
                            PurchaseArtifact.expires_at.is_not(None),
                            PurchaseArtifact.expires_at <= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for artifact in artifacts:
                if artifact.stored_file_id:
                    await _delete_stored_file(session, storage, artifact.stored_file_id)
                await session.delete(artifact)
                removed += 1

            stale_samples = (
                (await session.execute(select(SampleDerivative).where(SampleDerivative.status == "failed")))
                .scalars()
                .all()
            )
            for derivative in stale_samples:
                if derivative.stored_file_id:
                    await _delete_stored_file(session, storage, derivative.stored_file_id)
                await session.delete(derivative)
                removed += 1
            await session.commit()
    finally:
        await dispose_and_reset()
    return removed


async def _delete_stored_file(session, storage, stored_file_id: str) -> None:
    """Delete a ``StoredFile`` row + backend object, ignoring missing files."""
    from ..models.stored_file import StoredFile

    stored_file = await session.get(StoredFile, stored_file_id)
    if stored_file is None:
        return
    try:
        await storage.backend.delete(stored_file.storage_path)
    except Exception as exc:  # noqa: BLE001 — a gone object is still cleaned
        logger.info("Could not delete stored file %s: %s", stored_file_id, exc)
    await session.delete(stored_file)
