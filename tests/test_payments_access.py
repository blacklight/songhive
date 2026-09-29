"""Tests for the payments access resolver (services/payments/access.py)."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest
from sqlalchemy import select

from songhive.models._enums import Visibility
from songhive.models.album import Album
from songhive.models.artist import Artist
from songhive.models.payments import (
    PaymentOrder,
    PaymentOrderItem,
    PurchaseEntitlement,
    RedeemCapability,
    Sale,
)
from songhive.models.stored_file import StoredFile
from songhive.models.track import Track
from songhive.models.user import User
from songhive.services.payments import access


async def _make_artist(session) -> Artist:
    artist = Artist(name="Test Artist")
    session.add(artist)
    await session.flush()
    return artist


async def _make_file(session, owner: Optional[User] = None) -> StoredFile:
    seed = secrets.token_bytes(16)
    sha = hashlib.sha256(seed).hexdigest()
    stored_file = StoredFile(
        storage_path=f"files/{sha[:2]}/{sha[2:4]}/{sha}",
        storage_backend="local",
        content_type="audio/mpeg",
        size=len(seed),
        sha256=sha,
        owner_id=owner.id if owner is not None else None,
        visibility=Visibility.PUBLIC.value,
    )
    session.add(stored_file)
    await session.flush()
    return stored_file


async def _make_track(
    session,
    owner: User,
    visibility: str = Visibility.PUBLIC.value,
    album: Optional[Album] = None,
) -> Track:
    artist = await _make_artist(session)
    audio_file = await _make_file(session, owner)
    track = Track(
        title="Test Track",
        artist_id=artist.id,
        album_id=album.id if album is not None else None,
        owner_id=owner.id,
        visibility=visibility,
        audio_file_id=audio_file.id,
    )
    session.add(track)
    await session.flush()
    return track


async def _make_album(session, owner: User) -> Album:
    artist = await _make_artist(session)
    album = Album(
        title="Test Album",
        artist_id=artist.id,
        owner_id=owner.id,
        visibility=Visibility.PUBLIC.value,
    )
    session.add(album)
    await session.flush()
    return album


async def _make_sale(
    session,
    owner: User,
    *,
    track: Optional[Track] = None,
    album: Optional[Album] = None,
    status: str = "active",
    unpaid_policy: str = "sample",
) -> Sale:
    sale = Sale(
        owner_id=owner.id,
        entity_type="track" if track is not None else "album",
        track_id=track.id if track is not None else None,
        album_id=album.id if album is not None else None,
        status=status,
        price_minor=500,
        currency="USD",
        unpaid_policy=unpaid_policy,
    )
    session.add(sale)
    await session.flush()
    return sale


async def _make_paid_order(
    session,
    buyer: User,
    sale: Sale,
    track: Track,
) -> PaymentOrder:
    order = PaymentOrder(
        kind="purchase",
        sale_id=sale.id,
        buyer_user_id=buyer.id,
        status="paid",
        currency="usd",
        total_minor=sale.price_minor,
        checkout_token=secrets.token_urlsafe(16),
        provider_name="fake",
    )
    session.add(order)
    await session.flush()
    session.add(
        PurchaseEntitlement(
            order_id=order.id,
            sale_id=sale.id,
            track_id=track.id,
            user_id=buyer.id,
            status="active",
        )
    )
    await session.flush()
    return order


class TestEffectiveGate:
    async def test_ungated_track(self, db_session, regular_user, other_user):
        track = await _make_track(db_session, regular_user)
        assert await access.effective_gate(db_session, track) is None

    async def test_track_sale_gates(self, db_session, regular_user):
        track = await _make_track(db_session, regular_user)
        sale = await _make_sale(db_session, regular_user, track=track)
        assert (await access.effective_gate(db_session, track)).id == sale.id

    async def test_draft_sale_does_not_gate(self, db_session, regular_user):
        track = await _make_track(db_session, regular_user)
        await _make_sale(db_session, regular_user, track=track, status="draft")
        assert await access.effective_gate(db_session, track) is None

    async def test_inactive_sale_does_not_gate(self, db_session, regular_user):
        track = await _make_track(db_session, regular_user)
        await _make_sale(db_session, regular_user, track=track, status="inactive")
        assert await access.effective_gate(db_session, track) is None

    async def test_album_sale_gates_member(self, db_session, regular_user):
        album = await _make_album(db_session, regular_user)
        track = await _make_track(db_session, regular_user, album=album)
        sale = await _make_sale(db_session, regular_user, album=album)
        assert (await access.effective_gate(db_session, track)).id == sale.id

    async def test_track_sale_wins_over_album_sale(self, db_session, regular_user):
        album = await _make_album(db_session, regular_user)
        track = await _make_track(db_session, regular_user, album=album)
        await _make_sale(db_session, regular_user, album=album)
        track_sale = await _make_sale(db_session, regular_user, track=track)
        assert (await access.effective_gate(db_session, track)).id == track_sale.id


class TestResolveTrackAccess:
    async def test_no_sale_is_full(self, db_session, regular_user, other_user):
        track = await _make_track(db_session, regular_user)
        result = await access.resolve_track_access(db_session, track, other_user)
        assert result.level == access.ACCESS_FULL
        assert not result.gated

    @pytest.mark.parametrize(
        "policy,level",
        [
            ("full_stream", access.ACCESS_STREAM),
            ("sample", access.ACCESS_SAMPLE),
            ("none", access.ACCESS_NONE),
        ],
    )
    async def test_anonymous_gets_policy_level(self, db_session, regular_user, policy, level):
        track = await _make_track(db_session, regular_user)
        await _make_sale(db_session, regular_user, track=track, unpaid_policy=policy)
        result = await access.resolve_track_access(db_session, track, None)
        assert result.level == level

    async def test_owner_gets_full(self, db_session, regular_user):
        track = await _make_track(db_session, regular_user)
        await _make_sale(db_session, regular_user, track=track, unpaid_policy="none")
        result = await access.resolve_track_access(db_session, track, regular_user)
        assert result.level == access.ACCESS_FULL
        assert result.reason == "owner"

    async def test_admin_gets_full(self, db_session, regular_user, admin_user):
        track = await _make_track(db_session, regular_user)
        await _make_sale(db_session, regular_user, track=track, unpaid_policy="none")
        result = await access.resolve_track_access(db_session, track, admin_user)
        assert result.level == access.ACCESS_FULL

    async def test_buyer_gets_full(self, db_session, regular_user, other_user):
        track = await _make_track(db_session, regular_user)
        sale = await _make_sale(db_session, regular_user, track=track, unpaid_policy="none")
        await _make_paid_order(db_session, other_user, sale, track)
        result = await access.resolve_track_access(db_session, track, other_user)
        assert result.level == access.ACCESS_FULL
        assert result.reason == "entitled"

    async def test_revoked_entitlement_falls_back_to_policy(self, db_session, regular_user, other_user):
        track = await _make_track(db_session, regular_user)
        sale = await _make_sale(db_session, regular_user, track=track, unpaid_policy="none")
        order = await _make_paid_order(db_session, other_user, sale, track)
        entitlement = (
            await db_session.execute(select(PurchaseEntitlement).where(PurchaseEntitlement.order_id == order.id))
        ).scalar_one()
        entitlement.status = "revoked"
        await db_session.flush()
        result = await access.resolve_track_access(db_session, track, other_user)
        assert result.level == access.ACCESS_NONE
        assert not result.can_stream


class TestCapabilityAccess:
    async def test_capability_grants_snapshot_tracks(self, db_session, regular_user, other_user):
        track = await _make_track(db_session, regular_user)
        sale = await _make_sale(db_session, regular_user, track=track, unpaid_policy="none")
        order = await _make_paid_order(db_session, other_user, sale, track)
        capability = RedeemCapability(
            order_id=order.id,
            token_hash=hashlib.sha256(b"tok").hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db_session.add(capability)
        # The capability needs the snapshot item to grant access.
        db_session.add(PaymentOrderItem(order_id=order.id, track_id=track.id, position=0, price_minor=500))
        await db_session.flush()

        result = await access.resolve_track_access(db_session, track, None, download_capability=capability)
        assert result.level == access.ACCESS_FULL
        assert result.reason == "capability"

    async def test_capability_does_not_grant_other_tracks(self, db_session, regular_user, other_user):
        track = await _make_track(db_session, regular_user)
        other_track = await _make_track(db_session, regular_user)
        sale = await _make_sale(db_session, regular_user, track=track, unpaid_policy="none")
        await _make_sale(db_session, regular_user, track=other_track, unpaid_policy="none")
        order = await _make_paid_order(db_session, other_user, sale, track)
        capability = RedeemCapability(
            order_id=order.id,
            token_hash=hashlib.sha256(b"tok2").hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db_session.add(capability)
        db_session.add(PaymentOrderItem(order_id=order.id, track_id=track.id, position=0, price_minor=500))
        await db_session.flush()

        result = await access.resolve_track_access(db_session, other_track, None, download_capability=capability)
        assert result.level == access.ACCESS_NONE


class TestAudioUrlFor:
    async def test_none_policy_suppresses_url(self, db_session, regular_user):
        track = await _make_track(db_session, regular_user)
        await _make_sale(db_session, regular_user, track=track, unpaid_policy="none")
        result = await access.resolve_track_access(db_session, track, None)
        assert access.audio_url_for(track, result) is None

    async def test_stream_policy_uses_stream_endpoint(self, db_session, regular_user):
        track = await _make_track(db_session, regular_user)
        await _make_sale(db_session, regular_user, track=track, unpaid_policy="full_stream")
        result = await access.resolve_track_access(db_session, track, None)
        url = access.audio_url_for(track, result)
        assert url is not None and str(track.id) in url

    async def test_sample_policy_uses_sample_endpoint(self, db_session, regular_user):
        track = await _make_track(db_session, regular_user)
        await _make_sale(db_session, regular_user, track=track, unpaid_policy="sample")
        result = await access.resolve_track_access(db_session, track, None)
        url = access.audio_url_for(track, result)
        assert url is not None and "samples" in url
