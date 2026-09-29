"""
Seller onboarding and sale lifecycle helpers.

Sale publishing defers activation for ``sample``-policy materialized sales
until every covered track has a ready derivative — a ``sample`` sale that is
not yet fully materialized cannot go live because unpaid visitors would
otherwise see no audio at all.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.payments import ConnectedAccount, Sale
from ...models.track import Track
from ...models.user import User
from .access import assert_sale_publishable
from .errors import PaymentError
from .providers import get_provider
from .samples import ensure_sample

logger = logging.getLogger(__name__)


async def get_connected_account(session: AsyncSession, user_id: str) -> Optional[ConnectedAccount]:
    """Return the seller's connected account row, if any."""
    result = await session.execute(select(ConnectedAccount).where(ConnectedAccount.user_id == user_id))
    return result.scalar_one_or_none()


async def seller_ready(session: AsyncSession, user_id: str) -> bool:
    """Return whether the seller's connected account can take charges."""
    account = await get_connected_account(session, user_id)
    return bool(account and account.charges_enabled)


async def onboard_seller(
    session: AsyncSession,
    config: SonghiveConfig,
    user: User,
    *,
    refresh_url: str,
    return_url: str,
    country: Optional[str] = None,
) -> str:
    """
    Create (or reuse) the seller's connected account and return the hosted
    onboarding URL.

    ``country`` (ISO alpha-2) is required the first time — the provider needs
    it to pick the seller's legal/payout jurisdiction — and ignored for
    existing accounts.
    """
    account = await get_connected_account(session, user.id)
    if account is None:
        if not country:
            raise PaymentError("country is required to connect a payout account", code="country_required")
        provider = get_provider(config)
        account_id = await provider.create_connected_account(
            user_id=str(user.id),
            email=user.email or "",
            country=country,
            display_name=user.username or "",
        )
        account = ConnectedAccount(
            user_id=user.id,
            provider="stripe",
            provider_account_id=account_id,
        )
        session.add(account)
        await session.flush()

    provider = get_provider(config)
    return await provider.create_onboarding_link(
        account_id=account.provider_account_id,
        refresh_url=refresh_url,
        return_url=return_url,
    )


async def refresh_connected_account(
    session: AsyncSession,
    config: SonghiveConfig,
    user: User,
) -> Optional[ConnectedAccount]:
    """Refresh the seller's flags from the provider."""
    account = await get_connected_account(session, user.id)
    if account is None:
        return None
    provider = get_provider(config)
    state = await provider.retrieve_account(account.provider_account_id)
    account.charges_enabled = state.charges_enabled
    account.payouts_enabled = state.payouts_enabled
    account.details_submitted = state.details_submitted
    if state.details_submitted and account.onboarded_at is None:
        account.onboarded_at = datetime.now(timezone.utc)
    await session.flush()
    return account


async def disconnect_seller(session: AsyncSession, user: User) -> bool:
    """Remove the seller's provider link. Existing orders stay fulfillable."""
    account = await get_connected_account(session, user.id)
    if account is None:
        return False
    await session.delete(account)
    # Sales without a payout destination become unpublishable: deactivate.
    result = await session.execute(select(Sale).where(Sale.owner_id == user.id, Sale.status == "active"))
    for sale in result.scalars().all():
        sale.status = "inactive"
    return True


async def get_sale(session: AsyncSession, sale_id: str) -> Optional[Sale]:
    """Fetch a sale by id."""
    return await session.get(Sale, sale_id)


async def active_sale_for(session: AsyncSession, entity_type: str, entity_id: str) -> Optional[Sale]:
    """Return the active sale for a track or album id, if any."""
    column = Sale.track_id if entity_type == "track" else Sale.album_id
    result = await session.execute(select(Sale).where(column == entity_id, Sale.status == "active"))
    return result.scalar_one_or_none()


async def list_owner_sales(session: AsyncSession, owner_id: str) -> List[Sale]:
    """Return all of a seller's sales, newest first."""
    result = await session.execute(select(Sale).where(Sale.owner_id == owner_id).order_by(Sale.created_at.desc()))
    return list(result.scalars().all())


async def covered_tracks(session: AsyncSession, sale: Sale) -> List[Track]:
    """Return the tracks a sale covers (single track, or album members)."""
    if sale.entity_type == "track":
        track = await session.get(Track, sale.track_id)
        return [track] if track is not None else []
    result = await session.execute(select(Track).where(Track.album_id == sale.album_id))
    return list(result.scalars().all())


async def publish_sale(
    session: AsyncSession,
    sale: Sale,
    config: SonghiveConfig,
    *,
    enqueue_samples: bool = True,
) -> Sale:
    """
    Validate and activate a sale.

    For ``sample``-policy sales in ``materialized`` mode the sale stays
    ``draft`` until every covered track has a ready derivative; the
    ``materialize_track_sample`` task activates it via
    ``samples.maybe_activate_sales``. Other policies activate immediately.
    """
    await assert_sale_publishable(session, sale, config)

    if sale.unpaid_policy == "sample" and sale.sample_render_policy == "materialized":
        tracks = await covered_tracks(session, sale)
        ready = True
        for track in tracks:
            derivative = await ensure_sample(session, track, sale, config, enqueue=enqueue_samples)
            if derivative.status != "ready":
                ready = False
        sale.status = "active" if ready else "draft"
    else:
        sale.status = "active"
    return sale


async def deactivate_sale(session: AsyncSession, sale: Sale) -> Sale:
    """Take a sale offline; existing entitlements are unaffected."""
    sale.status = "inactive"
    return sale


async def revalidate_album_sales(session: AsyncSession, album_id: str, config: SonghiveConfig) -> None:
    """
    Re-check active album sales after the album's membership changed.

    A member that fails validation (e.g. became non-public) leaves the sale
    inconsistent — we deactivate it and notify the owner rather than letting
    buyers pay for content the gate can no longer fully cover.
    """
    result = await session.execute(
        select(Sale).where(
            Sale.album_id == album_id,
            Sale.entity_type == "album",
            Sale.status == "active",
        )
    )
    for sale in result.scalars().all():
        try:
            await assert_sale_publishable(session, sale, config)
        except PaymentError as exc:
            logger.warning("Deactivating album sale %s after membership change: %s", sale.id, exc)
            sale.status = "inactive"
            from ...models.notification import NotificationType
            from ..notifications import create_notification

            await create_notification(
                session,
                user_id=sale.owner_id,
                type=NotificationType.PURCHASE,
                source_url="/settings?tab=billing",
                payload={
                    "event": "sale_deactivated",
                    "sale_id": str(sale.id),
                    "reason": str(exc),
                },
            )
