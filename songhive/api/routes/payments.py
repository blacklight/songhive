"""
Payments API: seller Connect onboarding, sale management, purchase checkout,
guest redemption, paid-download delivery, membership billing, and provider
webhooks.

Every endpoint that moves money or changes access is ``payments_enabled``-
gated and fails closed without a canonical HTTPS public URL. Webhook routes
are the only unauthenticated writes — they are CSRF-exempt (see
``api/middleware/csrf.py``) and authorized solely by the provider signature.
Browser redirects and client-supplied ids never grant access; fulfillment
happens exclusively inside verified webhook handling.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.audit_log import AuditTargetType
from ...models.payments import (
    PaymentEvent,
    PaymentOrder,
    Sale,
)
from ...models.track import Track
from ...models.user import User
from ...services import acl, audit
from ...services.payments import (
    access,
    fulfillment,
    membership,
    payments_enabled,
    public_base_url,
)
from ...services.payments import sales as sales_service
from ...services.payments import samples as samples_service
from ...services.payments.crypto import new_token, normalize_email
from ...services.payments.errors import PaymentError
from ...services.payments.providers import get_provider
from ...services.payments.providers.base import (
    CheckoutSessionSpec,
    ProviderError,
    SignatureVerificationError,
)
from ...services.storage import StorageService
from .._common import client_ip
from ..cookies import set_auth_cookies
from ..deps import (
    get_config,
    get_current_user,
    get_current_user_optional,
    get_db,
    get_redis,
    get_storage_service,
    require_admin,
)
from ..middleware.rate_limit import rate_limit, rate_limit_account, rate_limit_user_or_ip
from .auth import _token_pair_response
from .files import _download_stored_file_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments")
admin_router = APIRouter(prefix="/admin/payments")

# How many bytes of webhook body we are willing to read. Provider events are
# small; a larger POST is either an attack or a misconfiguration — refuse.
_WEBHOOK_MAX_BODY = 256 * 1024

_GATED_HEADERS = {
    "Cache-Control": "private, no-store",
    "Vary": "Cookie, Authorization",
    "Referrer-Policy": "no-referrer",
}


def _raise(exc: PaymentError) -> HTTPException:
    """Map a PaymentError to an HTTPException carrying its status code."""
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _require_payments(config: SonghiveConfig) -> None:
    """404 everything when the instance does not run payments."""
    if not payments_enabled(config):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _require_base_url(config: SonghiveConfig) -> str:
    """Return the canonical public URL or fail closed with 503."""
    base_url = public_base_url(config)
    if base_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payments are not fully configured (no public URL)",
        )
    return base_url


# ---------------------------------------------------------------------------
# Request/response schemas.
# ---------------------------------------------------------------------------


class ConnectOnboardRequest(BaseModel):
    """Return/refresh paths the provider redirects the seller to post-onboarding."""

    return_path: str = Field(default="/settings?tab=billing", max_length=512)
    refresh_path: str = Field(default="/settings?tab=billing", max_length=512)
    # ISO 3166-1 alpha-2 — the seller's country of operation. Required only
    # when a connected account is first created (existing accounts skip it).
    country: Optional[str] = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")


class ConnectStatusResponse(BaseModel):
    """Seller readiness for the current user."""

    connected: bool
    charges_enabled: bool = False
    payouts_enabled: bool = False
    details_submitted: bool = False
    onboarded: bool = False


class SaleUpsertRequest(BaseModel):
    """Create-or-publish payload for a track/album sale."""

    entity_type: str = Field(pattern="^(track|album)$")
    entity_id: str = Field(min_length=1, max_length=64)
    price_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    unpaid_policy: str = Field(default="sample", pattern="^(full_stream|sample|none)$")
    sample_start_seconds: int = Field(default=0, ge=0)
    sample_length_seconds: Optional[int] = Field(default=None, ge=1)
    sample_render_policy: str = Field(default="materialized", pattern="^(materialized|on_demand)$")
    publish: bool = True


class SalePatchRequest(BaseModel):
    """Partial update payload; ``publish`` toggles activation."""

    price_minor: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    unpaid_policy: Optional[str] = Field(default=None, pattern="^(full_stream|sample|none)$")
    sample_start_seconds: Optional[int] = Field(default=None, ge=0)
    sample_length_seconds: Optional[int] = Field(default=None, ge=1)
    sample_render_policy: Optional[str] = Field(default=None, pattern="^(materialized|on_demand)$")
    publish: Optional[bool] = None


class CheckoutRequest(BaseModel):
    """Purchase checkout for a sale entity; guests must supply an email."""

    item_type: str = Field(pattern="^(track|album)$")
    item_id: str = Field(min_length=1, max_length=64)
    guest_email: Optional[str] = Field(default=None, max_length=320)


class RedeemRequest(BaseModel):
    """The emailed redemption token (raw)."""

    token: str = Field(min_length=8, max_length=256)


class ResendRedeemRequest(BaseModel):
    """Ask for a fresh redemption email for an existing guest order."""

    order_id: str = Field(min_length=1, max_length=64)


class MembershipCancelRequest(BaseModel):
    """Cancel the caller's subscription (at period end by default)."""

    immediate: bool = False


def _sale_public(sale: Sale) -> dict:
    """Serialize the public offer view of a sale (what a buyer sees)."""
    return {
        "id": str(sale.id),
        "entity_type": sale.entity_type,
        "entity_id": str(sale.track_id or sale.album_id),
        "status": sale.status,
        "price_minor": sale.price_minor,
        "currency": sale.currency.upper(),
        "unpaid_policy": sale.unpaid_policy,
        "sample_start_seconds": sale.sample_start_seconds,
        "sample_length_seconds": sale.sample_length_seconds,
    }


def _order_public(order: PaymentOrder, tracks: Optional[list] = None) -> dict:
    """Serialize an order for its buyer — never contains PII or tokens."""
    return {
        "id": str(order.id),
        "kind": order.kind,
        "status": order.status,
        "sale_id": str(order.sale_id) if order.sale_id else None,
        "currency": order.currency.upper(),
        "total_minor": order.total_minor,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "tracks": tracks if tracks is not None else [],
    }


async def _order_track_summaries(session: AsyncSession, order: PaymentOrder) -> list:
    """Return ``[{track_id, title, position}]`` for the order's snapshot items."""
    items = await fulfillment.order_items(session, order.id)
    result = await session.execute(select(Track).where(Track.id.in_([i.track_id for i in items])))
    by_id = {str(t.id): t for t in result.scalars().all()}
    return [
        {
            "track_id": str(item.track_id),
            "title": by_id[str(item.track_id)].title if str(item.track_id) in by_id else None,
            "position": item.position,
        }
        for item in items
    ]


def _order_for_buyer(order: PaymentOrder, user: User) -> None:
    """Raise 404 unless ``order`` belongs to ``user``."""
    if order.buyer_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


# ---------------------------------------------------------------------------
# Seller Connect onboarding.
# ---------------------------------------------------------------------------


@router.post("/connect/onboard", dependencies=[Depends(rate_limit_account)])
async def connect_onboard(
    payload: ConnectOnboardRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Start (or resume) hosted provider onboarding for the current seller."""
    _require_payments(config)
    base_url = _require_base_url(config)
    try:
        url = await sales_service.onboard_seller(
            db,
            config,
            user,
            refresh_url=f"{base_url}{payload.refresh_path}",
            return_url=f"{base_url}{payload.return_path}",
            country=payload.country,
        )
    except PaymentError as exc:
        raise _raise(exc) from exc
    except ProviderError as exc:
        logger.error("connect onboarding failed for user %s: %s", user.id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Payment provider error") from exc
    await audit.log_action(
        db,
        actor_id=user.id,
        action="payments.connect_onboard",
        target_type=AuditTargetType.PAYMENT,
        target_id=user.id,
        ip_address=client_ip(request),
    )
    await db.commit()
    return {"onboarding_url": url}


@router.get("/connect/status", response_model=ConnectStatusResponse)
async def connect_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Return whether the current user's seller account can take charges."""
    _require_payments(config)
    try:
        account = await sales_service.refresh_connected_account(db, config, user)
    except ProviderError as exc:
        logger.error("connect status refresh failed for user %s: %s", user.id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Payment provider error") from exc
    await db.commit()
    if account is None:
        return ConnectStatusResponse(connected=False)
    return ConnectStatusResponse(
        connected=True,
        charges_enabled=account.charges_enabled,
        payouts_enabled=account.payouts_enabled,
        details_submitted=account.details_submitted,
        onboarded=account.details_submitted,
    )


@router.post("/connect/disconnect", dependencies=[Depends(rate_limit_account)])
async def connect_disconnect(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Remove the seller's provider link; active sales go offline."""
    _require_payments(config)
    removed = await sales_service.disconnect_seller(db, user)
    await audit.log_action(
        db,
        actor_id=user.id,
        action="payments.connect_disconnect",
        target_type=AuditTargetType.PAYMENT,
        target_id=user.id,
        ip_address=client_ip(request),
    )
    await db.commit()
    return {"disconnected": removed}


# ---------------------------------------------------------------------------
# Sale management.
# ---------------------------------------------------------------------------


async def _load_owned_entity(session: AsyncSession, entity_type: str, entity_id: str, user: User):
    """Load the target track/album and require the caller to manage it."""
    item = await acl.get_item(session, entity_type, entity_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_manage(session, user, entity_type, entity_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return item


@router.post(
    "/sales",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def create_sale(
    payload: SaleUpsertRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Create (and optionally publish) a sale for a track or album."""
    _require_payments(config)
    await _load_owned_entity(db, payload.entity_type, payload.entity_id, user)

    existing = await sales_service.active_sale_for(db, payload.entity_type, payload.entity_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active sale already exists for this item",
        )

    sale = Sale(
        owner_id=user.id,
        entity_type=payload.entity_type,
        track_id=payload.entity_id if payload.entity_type == "track" else None,
        album_id=payload.entity_id if payload.entity_type == "album" else None,
        status="draft",
        price_minor=payload.price_minor,
        currency=payload.currency.upper(),
        unpaid_policy=payload.unpaid_policy,
        sample_start_seconds=payload.sample_start_seconds,
        sample_length_seconds=payload.sample_length_seconds,
        sample_render_policy=payload.sample_render_policy,
    )
    db.add(sale)
    await db.flush()

    try:
        if payload.publish:
            await sales_service.publish_sale(db, sale, config)
        else:
            await access.assert_sale_publishable(db, sale, config)
    except PaymentError as exc:
        raise _raise(exc) from exc

    await audit.log_action(
        db,
        actor_id=user.id,
        action="payments.sale_create",
        target_type=AuditTargetType.PAYMENT,
        target_id=sale.id,
        details={
            "entity_type": sale.entity_type,
            "price_minor": sale.price_minor,
            "currency": sale.currency,
            "status": sale.status,
        },
        ip_address=client_ip(request),
    )
    await db.commit()
    return _sale_public(sale)


@router.get("/sales/mine")
async def list_my_sales(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List the current user's sales."""
    _require_payments(config)
    return [_sale_public(s) for s in await sales_service.list_owner_sales(db, user.id)]


@router.get("/sales/offer/{entity_type}/{entity_id}")
async def sale_offer(
    entity_type: str,
    entity_id: str,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Return the public offer for a sellable track or album (404 if none)."""
    _require_payments(config)
    if entity_type not in ("track", "album"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    sale = await sales_service.active_sale_for(db, entity_type, entity_id)
    if sale is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _sale_public(sale)


@router.patch("/sales/{sale_id}", dependencies=[Depends(rate_limit_account)])
async def update_sale(
    sale_id: str,
    payload: SalePatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Update a sale's pricing/policy; republication re-validates."""
    _require_payments(config)
    sale = await sales_service.get_sale(db, sale_id)
    if sale is None or sale.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    for field_name, value in payload.model_dump(exclude_unset=True).items():
        if field_name == "publish":
            continue
        if field_name == "currency" and isinstance(value, str):
            value = value.upper()
        setattr(sale, field_name, value)

    try:
        if payload.publish is False:
            sale.status = "inactive"
        elif payload.publish or sale.status == "active":
            await sales_service.publish_sale(db, sale, config)
    except PaymentError as exc:
        raise _raise(exc) from exc

    await audit.log_action(
        db,
        actor_id=user.id,
        action="payments.sale_update",
        target_type=AuditTargetType.PAYMENT,
        target_id=sale.id,
        details={"status": sale.status},
        ip_address=client_ip(request),
    )
    await db.commit()
    return _sale_public(sale)


@router.delete("/sales/{sale_id}", dependencies=[Depends(rate_limit_account)])
async def delete_sale(
    sale_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Deactivate a sale. Existing buyer entitlements are unaffected."""
    _require_payments(config)
    sale = await sales_service.get_sale(db, sale_id)
    if sale is None or sale.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await sales_service.deactivate_sale(db, sale)
    await audit.log_action(
        db,
        actor_id=user.id,
        action="payments.sale_delete",
        target_type=AuditTargetType.PAYMENT,
        target_id=sale.id,
        ip_address=client_ip(request),
    )
    await db.commit()
    return {"status": "inactive"}


# ---------------------------------------------------------------------------
# Samples and the gated stream bridge.
# ---------------------------------------------------------------------------


async def _serve_sample(
    session: AsyncSession,
    track: Track,
    sale: Sale,
    config: SonghiveConfig,
    storage: StorageService,
    redis: Optional[Redis] = None,
) -> FileResponse:
    """Serve the ready sample derivative for ``track`` (never original bytes)."""
    stored = await samples_service.sample_stored_file(
        session,
        track,
        sale,
        config,
        storage=storage,
        redis=redis,
    )
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample not ready")
    try:
        path = await storage.backend.retrieve(stored.storage_path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample not ready") from exc
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample not ready")
    return FileResponse(path, media_type=stored.content_type or "audio/mpeg", headers=_GATED_HEADERS)


@router.get("/samples/{track_id}", dependencies=[Depends(rate_limit_user_or_ip)])
async def stream_sample(
    track_id: str,
    token: Optional[str] = Query(None),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    config: SonghiveConfig = Depends(get_config),
    redis: Redis = Depends(get_redis),
):
    """
    Serve the configured audio sample for a ``sample``-gated track.

    Anonymous requests are allowed — the whole point is unpaid preview — but
    the resolver must report the sample level (or better). ``none``-policy
    and ungated tracks 403/404 respectively so the endpoint cannot be probed
    into serving original bytes. A share ``token`` widens ACL visibility to
    the shared item but never upgrades payment rights.
    """
    _require_payments(config)
    track = await db.get(Track, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_access(db, user, "track", track_id, share_token=token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    track_access = await access.resolve_track_access(db, track, user)
    if track_access.level == access.ACCESS_SAMPLE and track_access.sale is not None:
        return await _serve_sample(db, track, track_access.sale, config, storage, redis)
    if track_access.level == access.ACCESS_FULL and track_access.gated:
        # Buyers/owners may preview the sample too.
        assert track_access.sale is not None
        if track_access.sale.unpaid_policy == "sample":
            return await _serve_sample(db, track, track_access.sale, config, storage, redis)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


@router.get("/stream/{track_id}", dependencies=[Depends(rate_limit_user_or_ip)])
async def gated_stream(
    track_id: str,
    token: Optional[str] = Query(None),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Bridge endpoint advertised for gated tracks: 302s to the Tornado stream
    handler, which performs its own resolver check before serving bytes.

    The redirect is *not* an authorization grant — it only saves the client
    a round-trip of policy bookkeeping; ``/api/v1/stream/{id}`` re-resolves
    access independently. A share ``token`` widens ACL visibility only and is
    forwarded so the target can re-validate it.
    """
    _require_payments(config)
    track = await db.get(Track, track_id)
    if track is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not await acl.can_access(db, user, "track", track_id, share_token=token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    track_access = await access.resolve_track_access(db, track, user)
    if track_access.level == access.ACCESS_NONE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    suffix = f"?token={token}" if token else ""
    if track_access.level == access.ACCESS_SAMPLE:
        return RedirectResponse(
            url=f"/api/v1/payments/samples/{track_id}{suffix}",
            status_code=status.HTTP_302_FOUND,
            headers=_GATED_HEADERS,
        )
    return RedirectResponse(
        url=f"/api/v1/stream/{track_id}{suffix}",
        status_code=status.HTTP_302_FOUND,
        headers=_GATED_HEADERS,
    )


# ---------------------------------------------------------------------------
# Purchase checkout + orders.
# ---------------------------------------------------------------------------


async def _create_checkout(
    session: AsyncSession,
    config: SonghiveConfig,
    order: PaymentOrder,
    sale: Sale,
    *,
    buyer: Optional[User],
    guest_email: Optional[str],
    base_url: str,
) -> str:
    """Open the provider checkout session for a purchase order."""
    seller_account = await sales_service.get_connected_account(session, sale.owner_id)
    if seller_account is None or not seller_account.charges_enabled:
        raise PaymentError("The seller is not ready to take payments", status_code=422)

    provider = get_provider(config)
    spec = CheckoutSessionSpec(
        kind="purchase",
        order_id=str(order.id),
        checkout_token=order.checkout_token,
        destination_account_id=seller_account.provider_account_id,
        line_items=[
            {
                "price_data": {
                    "currency": sale.currency.lower(),
                    "unit_amount": sale.price_minor,
                    "product_data": {"name": f"Songhive {sale.entity_type} {sale.id}"},
                },
                "quantity": 1,
            }
        ],
        customer_email=(buyer.email if buyer is not None else guest_email) or None,
        success_url=f"{base_url}/checkout/success?order={order.checkout_token}",
        cancel_url=f"{base_url}/checkout/cancel?order={order.checkout_token}",
        metadata={
            "songhive_order_id": str(order.id),
            "songhive_checkout_token": order.checkout_token,
        },
    )
    result = await provider.create_checkout_session(spec)
    order.provider_session_id = result.session_id
    order.provider_name = provider.name
    if result.expires_at is not None:
        order.expires_at = result.expires_at
    return result.url


@router.post("/checkout", status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit_user_or_ip)])
async def checkout(
    payload: CheckoutRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Open a hosted checkout for an active sale.

    Returns the provider URL to send the buyer to. Nothing is granted by
    visiting the success URL — entitlements only materialize from the
    verified ``checkout.session.completed`` webhook.
    """
    _require_payments(config)
    base_url = _require_base_url(config)

    sale = await sales_service.active_sale_for(db, payload.item_type, payload.item_id)
    if sale is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    guest_email = normalize_email(payload.guest_email) if payload.guest_email else None
    if user is None and not guest_email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Guest checkout requires an email address",
        )

    try:
        order = await fulfillment.create_purchase_order(db, config, sale, buyer=user, guest_email=guest_email)
        url = await _create_checkout(db, config, order, sale, buyer=user, guest_email=guest_email, base_url=base_url)
    except PaymentError as exc:
        raise _raise(exc) from exc

    await db.commit()
    return {"order_id": str(order.id), "checkout_url": url}


@router.get("/orders/mine")
async def list_my_orders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List the current user's purchase orders with their track snapshots."""
    _require_payments(config)
    result = await db.execute(
        select(PaymentOrder)
        .where(PaymentOrder.buyer_user_id == user.id, PaymentOrder.kind == "purchase")
        .order_by(PaymentOrder.created_at.desc())
    )
    orders = list(result.scalars().all())
    return [_order_public(order, tracks=await _order_track_summaries(db, order)) for order in orders]


@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    user: Optional[User] = Depends(get_current_user_optional),
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Return an order's status for its buyer.

    Session-authenticated buyers match on ``buyer_user_id``; guests may pass
    the ``checkout_token`` (the same opaque value embedded in the provider
    redirect) to poll status — it reveals no PII and grants no access.
    """
    _require_payments(config)
    order = await db.get(PaymentOrder, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    # Only the buyer or admin may poll the order status.
    if not (
        (user is not None and order.buyer_user_id == user.id)
        or (token and secrets.compare_digest(token, order.checkout_token))
        or (user is not None and user.is_admin)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return _order_public(order, tracks=await _order_track_summaries(db, order))


@router.get("/orders/{order_id}/archive", dependencies=[Depends(rate_limit_user_or_ip)])
async def download_order_archive(
    order_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    config: SonghiveConfig = Depends(get_config),
):
    """Download the ZIP of every track a paid order covered (buyer only)."""
    _require_payments(config)
    order = await db.get(PaymentOrder, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    _order_for_buyer(order, user)
    if order.status != "paid":
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Unpaid")
    if not await fulfillment.purchase_download_ready(db, order):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access revoked")

    try:
        artifact = await fulfillment.build_purchase_zip(db, order, storage, config)
    except PaymentError as exc:
        raise _raise(exc) from exc
    stored = await fulfillment.artifact_stored_file(db, artifact)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.commit()

    response = await _download_stored_file_response(stored, "attachment", storage)
    for key, value in _GATED_HEADERS.items():
        response.headers[key] = value
    return response


# ---------------------------------------------------------------------------
# Guest redemption + capability downloads.
# ---------------------------------------------------------------------------


@router.post("/redeem", dependencies=[Depends(rate_limit)])
async def redeem(
    payload: RedeemRequest,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Exchange an emailed redemption token for order details + a download token.

    The emailed capability is redeemed (single-use); the returned
    ``download_token`` authorizes the bounded ``/payments/download/{token}``
    endpoints.
    """
    _require_payments(config)
    capability = await access.resolve_capability(db, payload.token)
    if capability is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    order = await db.get(PaymentOrder, capability.order_id)
    if order is None or order.status != "paid":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    tracks = await access.capability_track_ids(db, capability)
    if not tracks:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access revoked")

    await fulfillment.mark_capability_redeemed(db, capability)
    download_token = await fulfillment.issue_redeem_capability(db, order, config)
    await db.commit()

    return {
        "order": _order_public(order, tracks=await _order_track_summaries(db, order)),
        "download_token": download_token,
        "expires_in_days": config.payments.redeem_capability_ttl_days,
    }


@router.post("/redeem/resend", dependencies=[Depends(rate_limit)])
async def resend_redeem(
    payload: ResendRedeemRequest,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Queue a fresh redemption email for a still-valid guest order.

    Deliberately accepts only the order id — the stored guest email is the
    sole destination, so posting an address cannot redirect the link.
    """
    _require_payments(config)
    order = await db.get(PaymentOrder, payload.order_id)
    if order is None or order.kind != "purchase" or order.status != "paid":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if order.buyer_user_id is not None or not order.buyer_email_encrypted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    from ...models.payments import FulfillmentOutbox

    pending = await db.execute(
        select(FulfillmentOutbox).where(
            FulfillmentOutbox.order_id == order.id,
            FulfillmentOutbox.kind == "guest_email",
            FulfillmentOutbox.status == "pending",
        )
    )
    if pending.first() is None:
        db.add(FulfillmentOutbox(order_id=order.id, kind="guest_email"))
        await db.commit()
    # Always 202-shaped: existence of the order is the response.
    return {"status": "queued"}


async def _load_capability_order(session: AsyncSession, token: str) -> tuple[PaymentOrder, set]:
    """Validate a download capability and return ``(order, covered track ids)``."""
    capability = await access.resolve_capability(session, token)
    if capability is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    order = await session.get(PaymentOrder, capability.order_id)
    if order is None or order.status != "paid":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    tracks = await access.capability_track_ids(session, capability)
    if not tracks:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access revoked")
    return order, tracks


@router.get("/download/{token}", dependencies=[Depends(rate_limit)])
async def capability_order_downloads(
    token: str,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List the purchased tracks a download capability unlocks."""
    _require_payments(config)
    order, _ = await _load_capability_order(db, token)
    return _order_public(order, tracks=await _order_track_summaries(db, order))


@router.get("/download/{token}/track/{track_id}", dependencies=[Depends(rate_limit)])
async def capability_track_download(
    token: str,
    track_id: str,
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve one purchased original file to a valid download capability."""
    _require_payments(config)
    _, tracks = await _load_capability_order(db, token)
    if track_id not in tracks:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    from ...services.streaming import resolve_track_file

    stored = await resolve_track_file(db, track_id)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    response = await _download_stored_file_response(stored, "attachment", storage)
    for key, value in _GATED_HEADERS.items():
        response.headers[key] = value
    return response


@router.get("/download/{token}/archive", dependencies=[Depends(rate_limit)])
async def capability_archive_download(
    token: str,
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    config: SonghiveConfig = Depends(get_config),
):
    """Serve the purchase ZIP to a valid download capability."""
    _require_payments(config)
    order, _ = await _load_capability_order(db, token)
    try:
        artifact = await fulfillment.build_purchase_zip(db, order, storage, config)
    except PaymentError as exc:
        raise _raise(exc) from exc
    stored = await fulfillment.artifact_stored_file(db, artifact)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await db.commit()
    response = await _download_stored_file_response(stored, "attachment", storage)
    for key, value in _GATED_HEADERS.items():
        response.headers[key] = value
    return response


# ---------------------------------------------------------------------------
# Membership billing.
# ---------------------------------------------------------------------------


async def _billing_user(
    request: Request,
    redis: Redis,
    db: AsyncSession,
    token: Optional[str],
    order_token: Optional[str] = None,
) -> Tuple[User, str]:
    """
    Resolve the caller for membership endpoints: a signed-in user, the
    holder of a short-lived billing capability issued at registration/login,
    or the opaque checkout token embedded in the provider's success redirect
    (``order`` query param), so the return page keeps working even when the
    billing capability has expired.

    Returns ``(user, via)`` where ``via`` is ``"session"``, ``"billing"``, or
    ``"order"`` — callers use it to apply stricter checks to order-token
    access, which sits in redirect URLs and browser history.
    """
    from ..deps import _get_current_user, bearer_scheme  # local auth probe

    credentials = await bearer_scheme(request)
    user = await _get_current_user(request, db, credentials)
    if user is not None:
        return user, "session"

    billing_token = token or request.headers.get("X-Billing-Token")
    if billing_token:
        user_id = await membership.resolve_billing_capability(redis, billing_token)
        if user_id:
            resolved = await db.get(User, user_id)
            if resolved is not None:
                return resolved, "billing"

    if order_token:
        result = await db.execute(
            select(PaymentOrder).where(
                PaymentOrder.checkout_token == order_token,
                PaymentOrder.kind == "membership",
            )
        )
        order = result.scalar_one_or_none()
        if order is not None and order.buyer_user_id:
            resolved = await db.get(User, order.buyer_user_id)
            if resolved is not None:
                return resolved, "order"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _require_fresh_order_token(
    db: AsyncSession,
    token: str,
    ttl_seconds: int,
) -> None:
    """
    Enforce the order-token freshness window used for sensitive membership
    actions (session mint, portal, cancel): the token may only act within
    ``ttl_seconds`` of the membership order's last update — i.e. payment
    confirmation — matching the billing capability's lifetime.
    """
    result = await db.execute(
        select(PaymentOrder.updated_at).where(
            PaymentOrder.checkout_token == token,
            PaymentOrder.kind == "membership",
        )
    )
    updated_at = result.scalar_one_or_none()
    if updated_at is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid order")
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if updated_at + timedelta(seconds=ttl_seconds) < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Credential expired",
        )


@router.get("/membership/quote", dependencies=[Depends(get_db)])
async def membership_quote(config: SonghiveConfig = Depends(get_config)):
    """Return the configured membership price (public)."""
    _require_payments(config)
    return {
        "price_minor": config.payments.default_membership_amount_minor,
        "currency": config.payments.membership_currency.upper(),
        "interval": config.payments.membership_interval,
    }


@router.post("/membership/checkout", status_code=status.HTTP_201_CREATED)
async def membership_checkout(
    request: Request,
    billing_token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Open a hosted subscription checkout for the caller's membership.

    The caller is a signed-in user or a billing-capability holder. Creates a
    ``kind="membership"`` order and provisions the recurring Price for the
    instance's configured amount/currency/interval.
    """
    _require_payments(config)
    base_url = _require_base_url(config)
    provider = get_provider(config)
    user, _ = await _billing_user(request, redis, db, billing_token)
    subscription = await membership.get_or_create_subscription(db, user.id)
    if not subscription.provider_customer_id:
        subscription.provider_customer_id = await provider.get_or_create_customer(
            email=user.email or "", user_id=str(user.id)
        )

    price_id = await provider.ensure_membership_price(
        lookup_key=config.payments.membership_price_lookup_key,
        product_name=config.payments.membership_product_name,
        amount_minor=config.payments.default_membership_amount_minor,
        currency=config.payments.membership_currency,
        interval=config.payments.membership_interval,
    )

    order = PaymentOrder(
        kind="membership",
        buyer_user_id=user.id,
        currency=config.payments.membership_currency.lower(),
        total_minor=config.payments.default_membership_amount_minor,
        checkout_token=new_token(),
        provider_name=provider.name,
        expires_at=None,
    )
    db.add(order)
    await db.flush()

    # Billing-capability holders are inactive accounts: forward the token
    # so the provider redirect lands back on /billing still authorized.
    bcap = f"&bcap={billing_token}" if billing_token else ""
    cancel_q = f"?bcap={billing_token}" if billing_token else ""
    spec = CheckoutSessionSpec(
        kind="membership",
        order_id=str(order.id),
        checkout_token=order.checkout_token,
        customer_id=subscription.provider_customer_id,
        price_id=price_id,
        success_url=f"{base_url}/billing/success?order={order.checkout_token}{bcap}",
        cancel_url=f"{base_url}/billing/cancel{cancel_q}",
        metadata={
            "songhive_order_id": str(order.id),
            "songhive_checkout_token": order.checkout_token,
            "songhive_user_id": str(user.id),
        },
    )
    result = await provider.create_checkout_session(spec)
    order.provider_session_id = result.session_id
    await db.commit()
    return {"order_id": str(order.id), "checkout_url": result.url}


@router.get("/membership/status")
async def membership_status(
    request: Request,
    billing_token: Optional[str] = Query(default=None),
    order: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    config: SonghiveConfig = Depends(get_config),
):
    """Return the caller's own subscription state."""
    _require_payments(config)
    user, _ = await _billing_user(request, redis, db, billing_token, order_token=order)
    subscription = await membership.get_subscription(db, user.id)
    account = {
        "is_active": bool(user.is_active),
        "email_verified": bool(user.email_verified),
        "payments_required": bool(user.payments_required),
    }
    if subscription is None:
        return {
            "status": "none",
            "paid_through": None,
            "cancel_at_period_end": False,
            **account,
        }
    return {
        "status": subscription.status,
        "paid_through": subscription.paid_through.isoformat() if subscription.paid_through else None,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        **account,
    }


class MembershipSessionRequest(BaseModel):
    """Scoped post-checkout credentials exchangeable for a session."""

    billing_token: Optional[str] = None
    order: Optional[str] = None


@router.post("/membership/session", dependencies=[Depends(rate_limit_user_or_ip)])
async def membership_session(
    request: Request,
    response: Response,
    body: MembershipSessionRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Exchange a post-checkout scoped credential for a full session.

    The billing success page calls this once the webhook-confirmed status
    shows the account active. Scoped credentials (``billing_token`` or the
    ``order`` checkout token embedded in the provider redirect URL) mint a
    session only once — the URL tokens live in browser history and server
    logs, so replaying them must not keep yielding sessions. The order token
    additionally stays valid only for a short window after the order's last
    update (payment confirmation), matching the billing capability's TTL.
    """
    _require_payments(config)

    from ...users.tokens import issue_token_pair
    from ..deps import _get_current_user, bearer_scheme  # local auth probe

    credentials = await bearer_scheme(request)
    user = await _get_current_user(request, db, credentials)
    scoped_credentials = [c for c in (body.billing_token, body.order) if c]
    via = "session"
    if user is None:
        user, via = await _billing_user(request, redis, db, body.billing_token, order_token=body.order)
    else:
        # Already session-authenticated — no scoped credential is exercised.
        scoped_credentials = []

    if not user.is_active or user.admin_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Membership is not active yet",
        )

    if scoped_credentials:
        if via == "order":
            assert body.order is not None  # via == "order" implies a token
            await _require_fresh_order_token(db, body.order, config.payments.billing_capability_ttl_seconds)
        for credential in scoped_credentials:
            marker = "songhive:payments:minted:" + hashlib.sha256(credential.encode()).hexdigest()
            if not await redis.set(marker, "1", nx=True, ex=86400):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Credential already used",
                )

    user.last_login = datetime.now(timezone.utc)
    token_pair = await issue_token_pair(
        user,
        config,
        redis,
        ip_address=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    set_auth_cookies(response, config, token_pair)
    return _token_pair_response(token_pair)


@router.post("/membership/portal", dependencies=[Depends(rate_limit_user_or_ip)])
async def membership_portal(
    request: Request,
    billing_token: Optional[str] = Query(default=None),
    order: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    config: SonghiveConfig = Depends(get_config),
):
    """Return a hosted billing-portal URL for managing the subscription."""
    _require_payments(config)
    base_url = _require_base_url(config)
    user, via = await _billing_user(request, redis, db, billing_token, order_token=order)
    if via == "order":
        assert order is not None
        await _require_fresh_order_token(db, order, config.payments.billing_capability_ttl_seconds)
    subscription = await membership.get_subscription(db, user.id)
    if subscription is None or not subscription.provider_customer_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No billing account")
    provider = get_provider(config)
    url = await provider.create_portal_session(
        customer_id=subscription.provider_customer_id,
        return_url=f"{base_url}/billing",
    )
    return {"portal_url": url}


@router.post("/membership/cancel")
async def membership_cancel(
    payload: MembershipCancelRequest,
    request: Request,
    billing_token: Optional[str] = Query(default=None),
    order: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    config: SonghiveConfig = Depends(get_config),
):
    """Cancel the caller's subscription — at period end by default."""
    _require_payments(config)
    user, via = await _billing_user(request, redis, db, billing_token, order_token=order)
    if via == "order":
        assert order is not None
        await _require_fresh_order_token(db, order, config.payments.billing_capability_ttl_seconds)
    try:
        subscription = await membership.request_cancel(db, user, config, immediate=payload.immediate)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No subscription") from exc
    await membership.sync_user_active_flag(db, user, config=config)
    await audit.log_action(
        db,
        actor_id=user.id,
        action="payments.membership_cancel",
        target_type=AuditTargetType.PAYMENT,
        target_id=user.id,
        details={
            "immediate": payload.immediate,
            "paid_through": subscription.paid_through.isoformat() if subscription.paid_through else None,
        },
        ip_address=client_ip(request),
    )
    await db.commit()
    return {
        "status": subscription.status,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "paid_through": subscription.paid_through.isoformat() if subscription.paid_through else None,
        "is_active": bool(user.is_active),
        "email_verified": bool(user.email_verified),
        "payments_required": bool(user.payments_required),
    }


# ---------------------------------------------------------------------------
# Webhooks — signature-verified only, CSRF-exempt.
# ---------------------------------------------------------------------------


async def _event_order_id(session: AsyncSession, data: dict) -> Optional[str]:
    """Best-effort link from a normalized event to its order row."""
    session_id = data.get("id") if isinstance(data.get("id"), str) else None
    metadata = data.get("metadata") or {}
    if session_id and str(session_id).startswith("cs_"):
        result = await session.execute(select(PaymentOrder.id).where(PaymentOrder.provider_session_id == session_id))
        row = result.scalar_one_or_none()
        if row is not None:
            return str(row)
    token = metadata.get("songhive_checkout_token")
    if token:
        result = await session.execute(select(PaymentOrder.id).where(PaymentOrder.checkout_token == str(token)))
        row = result.scalar_one_or_none()
        if row is not None:
            return str(row)
    return None


async def _receive_webhook(
    request: Request,
    db: AsyncSession,
    config: SonghiveConfig,
    *,
    scope: str,
) -> dict:
    """
    Verify, persist, and enqueue a provider webhook event.

    The event row is committed before the processing task is queued so a
    crash after ``200`` cannot drop the delivery; ``process_payment_event``
    re-loads the row and is idempotent on ``(provider, event_id)``.
    """
    _require_payments(config)

    body = await request.body()
    if len(body) > _WEBHOOK_MAX_BODY:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Too large")

    signature = request.headers.get("Stripe-Signature") or request.headers.get("X-Signature") or ""
    provider = get_provider(config)
    try:
        event = provider.verify_webhook(body, signature, scope=scope)
    except SignatureVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature") from exc

    async def _load_event() -> Optional[PaymentEvent]:
        result = await db.execute(
            select(PaymentEvent).where(
                PaymentEvent.provider == event.provider,
                PaymentEvent.provider_event_id == event.event_id,
            )
        )
        return result.scalar_one_or_none()

    row = await _load_event()
    if row is None:
        row = PaymentEvent(
            provider=event.provider,
            provider_event_id=event.event_id,
            scope=event.scope,
            type=event.type,
            data=event.data,
            order_id=await _event_order_id(db, event.data),
            status="pending",
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # A concurrent duplicate delivery won the insert race — reload
            # the winner's row and fall through to normal dedupe handling.
            await db.rollback()
            row = await _load_event()
            if row is None:
                raise
    if row.status == "processed":
        return {"received": True, "duplicate": True}

    await db.commit()

    from ...tasks.payments import process_payment_event

    try:
        process_payment_event.delay(str(row.id))  # type: ignore[attr-defined]
    except Exception as exc:  # broker down — the reconcile sweep retries
        logger.error("Could not enqueue payment event %s: %s", row.id, exc)

    return {"received": True, "duplicate": row.status == "processed"}


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Platform-account webhook: membership invoices and subscriptions."""
    return await _receive_webhook(request, db, config, scope="platform")


@router.post("/webhooks/stripe-connect")
async def stripe_connect_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """Connect webhook: purchase checkouts, refunds, disputes, account updates."""
    return await _receive_webhook(request, db, config, scope="connect")


# ---------------------------------------------------------------------------
# Admin diagnostics.
# ---------------------------------------------------------------------------


@admin_router.get("/orders", dependencies=[Depends(require_admin)])
async def admin_list_orders(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """List payment orders for support/audit (no buyer PII returned)."""
    _require_payments(config)
    stmt = select(PaymentOrder).order_by(PaymentOrder.created_at.desc())
    if status_filter:
        stmt = stmt.where(PaymentOrder.status == status_filter)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    orders = list(result.scalars().all())
    return [_order_public(order) for order in orders]


@admin_router.post("/orders/{order_id}/refund", dependencies=[Depends(rate_limit_account)])
async def admin_refund_order(
    order_id: str,
    amount_minor: Optional[int] = Query(default=None, ge=1),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    Refund a paid order through the provider.

    The provider's ``charge.refunded`` webhook performs the actual
    entitlement revocation — this call only initiates, keeping webhook truth
    the single path into access changes.
    """
    _require_payments(config)
    order = await db.get(PaymentOrder, order_id)
    if order is None or order.kind != "purchase":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if order.status != "paid" or not order.provider_payment_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Not refundable")

    sale = await db.get(Sale, order.sale_id) if order.sale_id else None
    account_id = None
    if sale is not None:
        seller = await sales_service.get_connected_account(db, sale.owner_id)
        if seller is not None:
            account_id = seller.provider_account_id

    provider = get_provider(config)
    refund = await provider.create_refund(
        payment_id=order.provider_payment_id,
        amount_minor=amount_minor,
        account_id=account_id,
    )

    from ...services.audit import log_action

    await log_action(
        db,
        actor_id=admin.id,
        action="payments.order.refund",
        target_id=str(order.id),
        details={"amount_minor": amount_minor, "refund_id": refund.get("id")},
    )
    await db.commit()
    return {"refund_id": refund.get("id"), "status": "initiated"}
