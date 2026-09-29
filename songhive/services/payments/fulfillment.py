"""
Purchase fulfillment: turning a confirmed payment into durable entitlements.

The only entry point for granting access is :func:`mark_order_paid`, called
from signature-verified webhook handlers or provider-confirmed reconciliation —
never from a browser redirect. Entitlement rows are the durable right; guest
buyers additionally get a bounded :class:`RedeemCapability` whose raw token is
emailed (only the SHA-256 digest is persisted).

All transitions are idempotent: a duplicate ``checkout.session.completed``
finds the order already ``paid`` and the unique ``(order_id, track_id)``
entitlement constraint prevents double grants.
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.notification import NotificationType
from ...models.payments import (
    FulfillmentOutbox,
    PaymentOrder,
    PaymentOrderItem,
    PurchaseArtifact,
    PurchaseEntitlement,
    RedeemCapability,
    Sale,
)
from ...models.stored_file import StoredFile
from ..notifications import create_notification
from .crypto import email_hash, email_key_id, encrypt_email, new_token, token_hash
from .errors import PaymentError

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Order creation (checkout)
# ---------------------------------------------------------------------------


async def order_items(session: AsyncSession, order_id: str) -> List[PaymentOrderItem]:
    """Return an order's snapshot items in position order."""
    result = await session.execute(
        select(PaymentOrderItem).where(PaymentOrderItem.order_id == order_id).order_by(PaymentOrderItem.position)
    )
    return list(result.scalars().all())


async def sale_track_ids(session: AsyncSession, sale: Sale) -> List[str]:
    """Return the track ids a sale currently covers (album snapshot if set)."""
    if sale.entity_type == "track":
        return [str(sale.track_id)]
    snapshot = sale.album_snapshot_track_ids or []
    return [str(tid) for tid in snapshot]


async def create_purchase_order(
    session: AsyncSession,
    config: SonghiveConfig,
    sale: Sale,
    *,
    buyer: Optional[object],
    guest_email: Optional[str],
) -> PaymentOrder:
    """
    Create the ``pending`` order row for a sale checkout.

    For album sales the member tracks are snapshotted into
    ``payment_order_items`` now so later album edits never change what the
    buyer already paid for. The caller commits; :func:`open_checkout_session`
    then talks to the provider.
    """
    from .access import assert_sale_publishable

    await assert_sale_publishable(session, sale, config)

    if buyer is None and not guest_email:
        raise PaymentError("Guest checkout requires an email address", code="guest_email")

    track_ids = await sale_track_ids(session, sale)
    if not track_ids:
        raise PaymentError("Sale has no purchasable content", code="empty")

    order = PaymentOrder(
        kind="purchase",
        sale_id=sale.id,
        buyer_user_id=str(getattr(buyer, "id", "")) if buyer is not None else None,
        buyer_email_encrypted=(encrypt_email(guest_email) if guest_email else None),
        buyer_email_key_id=email_key_id() if guest_email else None,
        currency=sale.currency.lower(),
        total_minor=sale.price_minor,
        checkout_token=new_token(),
        provider_name="stripe",
        expires_at=_utcnow() + timedelta(hours=config.payments.checkout_session_ttl_hours),
    )
    session.add(order)
    await session.flush()

    if sale.entity_type == "album" and not sale.album_snapshot_track_ids:
        # Freeze album membership at first purchase so later edits cannot
        # change what a buyer paid for (or grant new content for free).
        sale.album_snapshot_track_ids = track_ids

    if sale.entity_type == "track":
        per_item = sale.price_minor
    else:
        per_item = sale.price_minor // len(track_ids) if track_ids else 0
    for position, track_id in enumerate(track_ids):
        session.add(
            PaymentOrderItem(
                order_id=order.id,
                track_id=track_id,
                position=position,
                price_minor=per_item,
            )
        )
    await session.flush()
    return order


# ---------------------------------------------------------------------------
# Payment confirmation — the one entitlement-granting path.
# ---------------------------------------------------------------------------


async def mark_order_paid(
    session: AsyncSession,
    order: PaymentOrder,
    *,
    provider_payment_id: Optional[str],
    paid_minor: Optional[int] = None,
    paid_currency: Optional[str] = None,
    now: Optional[datetime] = None,
) -> bool:
    """
    Mark a ``pending`` order paid and grant its entitlements.

    Returns ``False`` when the order is not payable (already terminal or the
    provider-reported amount does not match the order total — a mismatch is
    flagged ``disputed`` for review rather than fulfilled).
    """
    if order.status in ("paid", "refunded", "partially_refunded", "disputed"):
        return True  # already granted — replay is a no-op
    if order.status not in ("pending",):
        return False

    if paid_minor is not None and paid_minor != order.total_minor:
        order.status = "disputed"
        order.fulfillment_started_at = now or _utcnow()
        logger.error(
            "Order %s paid amount %s does not match quoted %s; flagged disputed",
            order.id,
            paid_minor,
            order.total_minor,
        )
        return False
    if paid_currency is not None and paid_currency.lower() != order.currency.lower():
        order.status = "disputed"
        logger.error(
            "Order %s paid currency %r does not match quoted %r; flagged disputed",
            order.id,
            paid_currency,
            order.currency,
        )
        return False

    order.status = "paid"
    order.provider_payment_id = provider_payment_id or order.provider_payment_id
    order.fulfillment_started_at = now or _utcnow()

    guest = email_hash(await _order_email(session, order)) if order.buyer_user_id is None else None
    items = await order_items(session, order.id)
    existing = await session.execute(
        select(PurchaseEntitlement.track_id).where(PurchaseEntitlement.order_id == order.id)
    )
    already = {str(tid) for tid in existing.scalars().all()}
    for item in items:
        if str(item.track_id) in already:
            continue
        session.add(
            PurchaseEntitlement(
                order_id=order.id,
                sale_id=order.sale_id,
                track_id=str(item.track_id),
                user_id=order.buyer_user_id,
                email_hash=guest,
            )
        )

    # Guest buyers need an emailed download link; registered buyers get an
    # in-app notification instead. Outbox rows are durable so a crash between
    # commit and send cannot lose the delivery.
    if order.buyer_user_id is None:
        session.add(FulfillmentOutbox(order_id=order.id, kind="guest_email"))
    else:
        await create_notification(
            session,
            user_id=str(order.buyer_user_id),
            type=NotificationType.PURCHASE,
            source_url="/settings?tab=purchases",
            payload={"order_id": str(order.id), "track_count": len(items)},
        )
    return True


async def _order_email(session: AsyncSession, order: PaymentOrder) -> str:
    from .crypto import decrypt_email

    if not order.buyer_email_encrypted:
        return ""
    return decrypt_email(order.buyer_email_encrypted)


async def order_guest_email(session: AsyncSession, order: PaymentOrder) -> Optional[str]:
    """Return the order's guest email (decrypted), or ``None``."""
    if not order.buyer_email_encrypted:
        return None
    return await _order_email(session, order)


async def revoke_order_entitlements(
    session: AsyncSession,
    order: PaymentOrder,
    *,
    reason: str,
    fully: bool = True,
) -> int:
    """
    Revoke an order's entitlements (refund/chargeback) and its capabilities.

    ``fully=False`` records a partial refund without revoking access.
    Returns the number of entitlements revoked.
    """
    if not fully:
        order.status = "partially_refunded"
        return 0

    now = _utcnow()
    order.status = "refunded"
    order.fulfillment_started_at = order.fulfillment_started_at or now

    entitlements = (
        (
            await session.execute(
                select(PurchaseEntitlement).where(
                    PurchaseEntitlement.order_id == order.id,
                    PurchaseEntitlement.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    for entitlement in entitlements:
        entitlement.status = "revoked"
        entitlement.revoked_at = now
        entitlement.revoke_reason = reason

    # Cached download artifacts for the order are invalidated implicitly —
    # delivery re-checks entitlement state before serving — but delete the
    # rows so the storage can be reclaimed by cleanup.
    artifacts = (
        (await session.execute(select(PurchaseArtifact).where(PurchaseArtifact.order_id == order.id))).scalars().all()
    )
    for artifact in artifacts:
        await session.delete(artifact)

    return len(entitlements)


# ---------------------------------------------------------------------------
# Guest redeem capabilities.
# ---------------------------------------------------------------------------


async def issue_redeem_capability(
    session: AsyncSession,
    order: PaymentOrder,
    config: SonghiveConfig,
    *,
    guest_email: Optional[str] = None,
) -> str:
    """
    Create a bounded download capability for an order and return the raw token.

    Only the token digest is stored; the raw value exists only in the emailed
    URL (and this return value).
    """
    token = new_token()
    capability = RedeemCapability(
        order_id=order.id,
        kind="download",
        token_hash=token_hash(token),
        email_hash=email_hash(guest_email) if guest_email else None,
        expires_at=_utcnow() + timedelta(days=config.payments.redeem_capability_ttl_days),
        max_redemptions=config.payments.guest_redemption_limit,
    )
    session.add(capability)
    await session.flush()
    return token


async def mark_capability_redeemed(session: AsyncSession, capability: RedeemCapability) -> None:
    """Record a redemption (bounded by ``max_redemptions``)."""
    capability.redemption_count += 1
    capability.last_redeemed_at = _utcnow()
    await session.flush()


def redeem_url(base_url: str, token: str) -> str:
    """Build the emailed guest redemption link for a raw capability token."""
    return f"{base_url}/redeem/{token}"


# ---------------------------------------------------------------------------
# Paid album ZIP artifacts.
# ---------------------------------------------------------------------------


def order_snapshot_hash(track_ids: List[str]) -> str:
    """Fingerprint the purchased track-id set (order-independent)."""
    digest = hashlib.sha256()
    for tid in sorted(track_ids):
        digest.update(tid.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


async def get_purchase_artifact(session: AsyncSession, order: PaymentOrder) -> Optional[PurchaseArtifact]:
    """Return a fresh cached ZIP artifact for the order's current snapshot."""
    items = await order_items(session, order.id)
    digest = order_snapshot_hash([str(i.track_id) for i in items])
    result = await session.execute(
        select(PurchaseArtifact).where(
            PurchaseArtifact.order_id == order.id,
            PurchaseArtifact.snapshot_hash == digest,
            PurchaseArtifact.stored_file_id.is_not(None),
        )
    )
    artifact = result.scalar_one_or_none()
    if artifact is None:
        return None
    if artifact.expires_at is not None and artifact.expires_at <= _utcnow():
        return None
    return artifact


async def build_purchase_zip(
    session: AsyncSession,
    order: PaymentOrder,
    storage,
    config: SonghiveConfig,
) -> PurchaseArtifact:
    """
    Build (or reuse) the ZIP containing the order's purchased tracks.

    The artifact is stored as a private ``StoredFile`` keyed on the snapshot
    hash; refund revocation deletes the row so the link goes dead.
    """
    import tempfile
    from pathlib import Path

    from ...models._enums import Visibility
    from ..downloads import ArchiveItem, materialize_archive

    existing = await get_purchase_artifact(session, order)
    if existing is not None:
        return existing

    items = await order_items(session, order.id)
    digest = order_snapshot_hash([str(i.track_id) for i in items])
    archive_items = [ArchiveItem(kind="track", ref=str(item.track_id), title=str(item.track_id)) for item in items]

    with tempfile.TemporaryDirectory(prefix="songhive-purchase-") as tmp:
        zip_path, item_errors = await materialize_archive(
            storage,
            config,
            [item.as_dict() for item in archive_items],
            Path(tmp),
            user_id=order.buyer_user_id,
            allowed_track_ids=frozenset(str(i.track_id) for i in items),
        )
        if item_errors:
            # A paid download must never silently ship a partial archive.
            raise PaymentError(
                f"{len(item_errors)} purchased track(s) could not be packaged",
                code="fulfillment",
            )
        with open(zip_path, "rb") as handle:
            stored = await storage.store_file(
                session,
                handle,
                "application/zip",
                prefix="purchases",
                owner_id=order.buyer_user_id,
                visibility=Visibility.PRIVATE.value,
                original_filename="purchase.zip",
            )

    artifact = PurchaseArtifact(
        order_id=order.id,
        snapshot_hash=digest,
        stored_file_id=stored.id,
        expires_at=_utcnow() + timedelta(hours=config.payments.artifact_ttl_hours),
    )
    session.add(artifact)
    await session.flush()
    return artifact


async def artifact_stored_file(session: AsyncSession, artifact: PurchaseArtifact) -> Optional["StoredFile"]:
    """Load the artifact's backing StoredFile."""
    if artifact.stored_file_id is None:
        return None
    return await session.get(StoredFile, artifact.stored_file_id)


async def order_covers_track(session: AsyncSession, order: PaymentOrder, track_id: str) -> bool:
    """Return whether an order's items include ``track_id``."""
    items = await order_items(session, order.id)
    return any(str(item.track_id) == str(track_id) for item in items)


async def purchase_download_ready(session: AsyncSession, order: PaymentOrder) -> bool:
    """Return whether the order's entitlements still grant downloads."""
    result = await session.execute(
        select(PurchaseEntitlement.id).where(
            PurchaseEntitlement.order_id == order.id,
            PurchaseEntitlement.status == "active",
        )
    )
    return result.first() is not None
