"""
Payment access resolver — the single source of truth for sale gating.

``services.acl`` decides *metadata/visibility* access (public/local/private,
share grants, share tokens). This module adds the payment layer on top: when
an active ``Sale`` gates a track, byte-level access is additionally bounded
by the sale's ``unpaid_policy`` unless the requester is the owner, an admin,
a registered buyer with a live entitlement, or the holder of a valid guest
redeem capability. Share grants and tokens deliberately never upgrade
payment rights.

Tracks without an active sale keep their existing ACL-only behavior — the
resolver returns ``full`` so callers can skip extra work.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models._enums import Visibility
from ...models.album import Album
from ...models.payments import PurchaseEntitlement, RedeemCapability, Sale
from ...models.stored_file import StoredFile
from ...models.track import Track
from ...models.transcoded_file import TranscodedFile
from ...models.user import User
from .crypto import token_hash
from .errors import PaymentError

logger = logging.getLogger(__name__)

# Access levels, ordered from least to most permissive.
ACCESS_NONE = "none"
ACCESS_SAMPLE = "sample"
ACCESS_STREAM = "stream"
ACCESS_FULL = "full"


@dataclass(frozen=True)
class TrackAccess:
    """
    The payment-level media access a requester has for a track.

    ``level`` is one of ``full`` (entitled/owner/admin — stream and download),
    ``stream`` (full playback, no download — ``unpaid_policy=full_stream``),
    ``sample`` (only the sale's configured sample) or ``none``.
    ``sale`` is the gating sale, ``None`` when the track is not gated.
    """

    level: str
    sale: Optional[Sale] = None
    reason: str = "no_sale"

    @property
    def gated(self) -> bool:
        return self.sale is not None

    @property
    def can_stream(self) -> bool:
        return self.level in (ACCESS_FULL, ACCESS_STREAM, ACCESS_SAMPLE)

    @property
    def can_stream_full(self) -> bool:
        return self.level in (ACCESS_FULL, ACCESS_STREAM)

    @property
    def can_download(self) -> bool:
        return self.level == ACCESS_FULL


def track_is_external(track: Track) -> bool:
    """Return whether the track is backed by an external-library item."""
    for attr in ("external_track", "external_item"):
        ref = getattr(track, attr, None)
        if ref is not None and getattr(ref, "state", None) == "active":
            return True
    return False


async def effective_gate(session: AsyncSession, track: Track) -> Optional[Sale]:
    """
    Return the active sale gating ``track``, if any.

    A direct track sale wins over an album sale covering the track's current
    album. Album sales gate the album's *current* members; what a buyer
    purchased is recorded separately on the order's item snapshot.
    """
    result = await session.execute(
        select(Sale).where(
            Sale.status == "active",
            Sale.track_id == track.id,
        )
    )
    sale = result.scalar_one_or_none()
    if sale is not None:
        return sale
    if track.album_id is None:
        return None
    result = await session.execute(
        select(Sale).where(
            Sale.status == "active",
            Sale.album_id == track.album_id,
        )
    )
    return result.scalar_one_or_none()


async def effective_gates(session: AsyncSession, tracks: Sequence[Track]) -> Dict[str, Sale]:
    """
    Return ``{track_id: gating Sale}`` for a batch of tracks without N+1.

    Two queries total: active track sales on the ids, and active album sales
    on the tracks' albums.
    """
    track_ids = [t.id for t in tracks]
    album_ids = {t.album_id for t in tracks if t.album_id is not None}
    filters = []
    if track_ids:
        filters.append(Sale.track_id.in_(track_ids))
    if album_ids:
        filters.append(Sale.album_id.in_(album_ids))
    if not filters:
        return {}
    result = await session.execute(select(Sale).where(Sale.status == "active", or_(*filters)))
    track_sales: Dict[str, Sale] = {}
    album_sales: Dict[str, Sale] = {}
    for sale in result.scalars().all():
        if sale.track_id is not None:
            track_sales[sale.track_id] = sale
        elif sale.album_id is not None:
            album_sales[sale.album_id] = sale
    gates: Dict[str, Sale] = {}
    for track in tracks:
        if track.id in track_sales:
            gates[str(track.id)] = track_sales[track.id]
        elif track.album_id is not None and track.album_id in album_sales:
            gates[str(track.id)] = album_sales[track.album_id]
    return gates


async def has_entitlement(session: AsyncSession, user: User, track_id: str) -> bool:
    """Return whether ``user`` holds an active entitlement for ``track_id``."""
    result = await session.execute(
        select(PurchaseEntitlement.id).where(
            PurchaseEntitlement.user_id == user.id,
            PurchaseEntitlement.track_id == track_id,
            PurchaseEntitlement.status == "active",
        )
    )
    return result.first() is not None


async def entitled_track_ids(
    session: AsyncSession,
    user: User,
    track_ids: Iterable[str],
) -> Set[str]:
    """Return the subset of ``track_ids`` the user holds active entitlements for."""
    ids = list(track_ids)
    if not ids:
        return set()
    result = await session.execute(
        select(PurchaseEntitlement.track_id).where(
            PurchaseEntitlement.user_id == user.id,
            PurchaseEntitlement.track_id.in_(ids),
            PurchaseEntitlement.status == "active",
        )
    )
    return {str(row) for row in result.scalars().all()}


async def resolve_capability(
    session: AsyncSession,
    token: str,
    *,
    kind: str = "download",
) -> Optional[RedeemCapability]:
    """
    Look up a redeem capability by its raw token.

    Returns the row only when the capability is unexpired and has redemptions
    left. Entitlement validity is checked separately by
    :func:`capability_track_ids` so a refunded order's links go dead.
    """
    now = datetime.now(timezone.utc)
    digest = token_hash(token)
    result = await session.execute(
        select(RedeemCapability).where(
            RedeemCapability.token_hash == digest,
            RedeemCapability.kind == kind,
        )
    )
    capability = result.scalar_one_or_none()
    if capability is None:
        return None
    if capability.expires_at is not None and capability.expires_at <= now:
        return None
    if capability.max_redemptions and capability.redemption_count >= capability.max_redemptions:
        return None
    return capability


async def capability_track_ids(session: AsyncSession, capability: RedeemCapability) -> Set[str]:
    """
    Return the track ids a capability currently unlocks.

    The set is the order's purchased snapshot intersected with entitlements
    that are still active — a refunded or revoked purchase yields the empty
    set, killing the link.
    """
    result = await session.execute(
        select(PurchaseEntitlement.track_id).where(
            PurchaseEntitlement.order_id == capability.order_id,
            PurchaseEntitlement.status == "active",
        )
    )
    return {str(row) for row in result.scalars().all()}


async def resolve_track_access(
    session: AsyncSession,
    track: Track,
    user: Optional[User],
    *,
    download_capability: Optional[RedeemCapability] = None,
    capability_tracks: Optional[Set[str]] = None,
) -> TrackAccess:
    """
    Resolve the payment-level media access ``user`` has for ``track``.

    ``download_capability`` is an already-validated :class:`RedeemCapability`;
    ``capability_tracks`` optionally carries its precomputed track-id set (to
    share one query across a batch of resolves). A capability grants ``full``
    only to the purchased snapshot.
    """
    sale = await effective_gate(session, track)
    return await resolve_with_sale(
        session,
        track,
        user,
        sale,
        download_capability=download_capability,
        capability_tracks=capability_tracks,
    )


async def resolve_with_sale(
    session: AsyncSession,
    track: Track,
    user: Optional[User],
    sale: Optional[Sale],
    *,
    download_capability: Optional[RedeemCapability] = None,
    capability_tracks: Optional[Set[str]] = None,
) -> TrackAccess:
    """
    The policy core of :func:`resolve_track_access` with the gate pre-resolved.

    Callers that already ran :func:`effective_gate`/:func:`effective_gates`
    (batch response builders) pass the sale through instead of paying a
    second query per track.
    """
    if sale is None:
        return TrackAccess(ACCESS_FULL, None, "no_sale")

    if user is not None and (user.is_admin or track.owner_id == user.id or sale.owner_id == user.id):
        return TrackAccess(ACCESS_FULL, sale, "owner")

    if download_capability is not None:
        tracks = capability_tracks
        if tracks is None:
            tracks = await capability_track_ids(session, download_capability)
        if str(track.id) in tracks:
            return TrackAccess(ACCESS_FULL, sale, "capability")

    if user is not None and await has_entitlement(session, user, str(track.id)):
        return TrackAccess(ACCESS_FULL, sale, "entitled")

    if sale.unpaid_policy == "full_stream":
        return TrackAccess(ACCESS_STREAM, sale, "policy")
    if sale.unpaid_policy == "sample":
        return TrackAccess(ACCESS_SAMPLE, sale, "policy")
    return TrackAccess(ACCESS_NONE, sale, "denied")


async def file_download_access(
    session: AsyncSession,
    stored_file_id: str,
    user: Optional[User],
) -> bool:
    """
    Return whether ``user`` may download ``stored_file_id`` as raw bytes.

    A stored file is downloadable when at least one referencing entity grants
    it: an ungated (ACL-visible — already enforced upstream) track, or a gated
    track the requester has ``full`` access to. Files referenced only by
    sample derivatives are never downloadable through this path.
    """
    track_ids: Set[str] = set()

    rows = await session.execute(select(Track.id).where(Track.audio_file_id == stored_file_id))
    track_ids.update(str(row) for row in rows.scalars().all())

    rows = await session.execute(select(TranscodedFile.track_id).where(TranscodedFile.stored_file_id == stored_file_id))
    track_ids.update(str(row) for row in rows.scalars().all())

    if not track_ids:
        # Not track audio: cover art, archives and other files keep the
        # existing ACL-only behavior (the caller already passed ACL).
        return True

    result = await session.execute(select(Track).where(Track.id.in_(track_ids)))
    tracks = list(result.scalars().all())
    gates = await effective_gates(session, tracks)
    gated_ids = {tid for tid in track_ids if tid in gates}

    if not gated_ids:
        return True

    if user is not None and user.is_admin:
        return True

    for track in tracks:
        if str(track.id) not in gated_ids:
            # An ungated public/accessible alias exists — the bytes are
            # already reachable through it, so gating this file is moot.
            return True

    entitled = await entitled_track_ids(session, user, gated_ids) if user is not None else set()
    for track in tracks:
        tid = str(track.id)
        if tid not in gated_ids:
            continue
        sale = gates[tid]
        if user is not None and (track.owner_id == user.id or sale.owner_id == user.id):
            return True
        if tid in entitled:
            return True
    return False


async def _album_member_tracks(session: AsyncSession, album_id: str) -> List[Track]:
    result = await session.execute(select(Track).where(Track.album_id == album_id))
    return list(result.scalars().all())


async def _uncontrolled_alias_exists(
    session: AsyncSession,
    track_ids: Sequence[str],
) -> Optional[str]:
    """
    Return an offending track id when a sale-covered track's audio bytes are
    also reachable through a public track that is not itself sale-gated.

    Sharing the same ``sha256`` (same or different ``StoredFile`` rows) is
    what makes an alias: if any *other* public track shares the hash and has
    no active gate, selling this track would be a paywall around content that
    is already free — reject the publish.
    """
    if not track_ids:
        return None
    rows = await session.execute(
        select(Track.id, StoredFile.sha256)
        .join(StoredFile, Track.audio_file_id == StoredFile.id)
        .where(Track.id.in_(track_ids))
    )
    hashes = {sha for _, sha in rows.all() if sha}
    if not hashes:
        return None
    rows = await session.execute(
        select(Track)
        .join(StoredFile, Track.audio_file_id == StoredFile.id)
        .where(
            StoredFile.sha256.in_(hashes),
            Track.id.notin_(track_ids),
            Track.visibility == Visibility.PUBLIC.value,
        )
    )
    candidates = list(rows.scalars().all())
    if not candidates:
        return None
    gated = await effective_gates(session, candidates)
    for candidate in candidates:
        if str(candidate.id) not in gated:
            return str(candidate.id)
    return None


async def assert_sale_publishable(
    session: AsyncSession,
    sale: Sale,
    config: SonghiveConfig,
) -> None:
    """
    Raise :class:`PaymentError` unless ``sale`` may be published.

    Validates the target entity (exists, owned by the seller, public, local —
    external/provider tracks cannot be sold), price/currency bounds, sample
    window bounds, and the shared-content-alias rule.
    """
    if sale.entity_type == "track":
        result = await session.execute(select(Track).where(Track.id == sale.track_id))
        track = result.scalar_one_or_none()
        if track is None:
            raise PaymentError("Track not found", status_code=404)
        tracks = [track]
        if track.visibility != Visibility.PUBLIC.value:
            raise PaymentError("Only public tracks can be sold", code="visibility")
        if track_is_external(track):
            raise PaymentError("Externally hosted tracks cannot be sold", code="external")
    elif sale.entity_type == "album":
        result = await session.execute(select(Album).where(Album.id == sale.album_id))
        album = result.scalar_one_or_none()
        if album is None:
            raise PaymentError("Album not found", status_code=404)
        if album.owner_id != sale.owner_id:
            raise PaymentError("Sale owner does not match album owner", code="owner")
        if album.visibility != Visibility.PUBLIC.value:
            raise PaymentError("Only public albums can be sold", code="visibility")
        tracks = await _album_member_tracks(session, str(album.id))
        if not tracks:
            raise PaymentError("Album has no tracks to sell", code="empty")
        for track in tracks:
            if track.owner_id != sale.owner_id:
                raise PaymentError("Album contains tracks owned by other users", code="owner")
            if track_is_external(track):
                raise PaymentError("Album contains externally hosted tracks", code="external")
    else:
        raise PaymentError(f"Unknown sale entity type: {sale.entity_type!r}")

    if sale.entity_type == "track":
        track = tracks[0]
        if track.owner_id != sale.owner_id:
            raise PaymentError("Sale owner does not match track owner", code="owner")

    if sale.price_minor < config.payments.min_price_minor:
        raise PaymentError(
            f"Price below minimum ({config.payments.min_price_minor} minor units)",
            code="price",
        )
    if sale.price_minor > config.payments.max_price_minor:
        raise PaymentError(
            f"Price above maximum ({config.payments.max_price_minor} minor units)",
            code="price",
        )
    if sale.currency.lower() not in config.payments.supported_currencies:
        raise PaymentError(f"Unsupported currency: {sale.currency!r}", code="currency")

    if sale.unpaid_policy == "sample":
        if sale.sample_start_seconds < 0:
            raise PaymentError("Sample start cannot be negative", code="sample")
        length = sale.sample_length_seconds
        if length is None:
            length = config.payments.default_sample_seconds
        if length <= 0:
            raise PaymentError("Sample length must be positive", code="sample")
        if length > config.payments.sample_max_seconds:
            raise PaymentError(
                f"Sample length exceeds the instance maximum " f"({config.payments.sample_max_seconds}s)",
                code="sample",
            )

    track_ids = [str(t.id) for t in tracks]
    alias = await _uncontrolled_alias_exists(session, track_ids)
    if alias is not None:
        raise PaymentError(
            "The same audio is already publicly available through another track; "
            "gate or unpublish that copy before selling",
            code="shared_content",
        )


async def gated_access(
    session: AsyncSession,
    track: Track,
    user: Optional[User],
    *,
    gate_cache: Optional[Dict[str, Optional[Sale]]] = None,
) -> Optional[TrackAccess]:
    """
    Return the :class:`TrackAccess` for a *gated* track, or ``None``.

    ``None`` signals "no sale gates this track" — callers keep their normal
    (ACL-only) behavior and skip all payment overlay work. ``gate_cache``
    optionally maps ``str(track_id) -> Optional[Sale]``; entries populated by
    :func:`prewarm_gate_cache` are reused so batch builders don't N+1.
    """
    tid = str(track.id)
    if gate_cache is not None and tid in gate_cache:
        sale = gate_cache[tid]
    else:
        sale = await effective_gate(session, track)
        if gate_cache is not None:
            gate_cache[tid] = sale
    if sale is None:
        return None
    return await resolve_with_sale(session, track, user, sale)


async def prewarm_gate_cache(
    session: AsyncSession,
    tracks: Sequence[Track],
    cache: Dict[str, Optional[Sale]],
) -> None:
    """Fill ``cache`` with ``track_id -> gating Sale or None`` in two queries."""
    gates = await effective_gates(session, tracks)
    for track in tracks:
        cache[str(track.id)] = gates.get(str(track.id))


def audio_url_for(track: Track, track_access: TrackAccess) -> Optional[str]:
    """
    Return the playback URL to advertise for a *gated* track.

    ``full``/``stream`` levels route through the Tornado stream endpoint
    (which re-resolves access itself); ``sample`` routes through the sample
    endpoint; ``none`` advertises nothing. Only call for gated tracks —
    ungated tracks keep their normal URL logic.
    """
    if track_access.can_stream_full:
        return f"/api/v1/stream/{track.id}"
    if track_access.level == ACCESS_SAMPLE:
        return f"/api/v1/payments/samples/{track.id}"
    return None


async def downloadable_track_ids(
    session: AsyncSession,
    track_ids: Iterable[str],
    user: Optional[User],
) -> Set[str]:
    """
    Return the subset of ``track_ids`` the requester may download as originals.

    Ungated tracks always pass; gated ones require ``full`` access
    (owner/seller/admin or an active entitlement). Guest redeem capabilities
    are deliberately not consulted — they have their own download surface.
    """
    ids = {str(t) for t in track_ids}
    if not ids:
        return set()
    result = await session.execute(select(Track).where(Track.id.in_(ids)))
    tracks = list(result.scalars().all())
    gates = await effective_gates(session, tracks)
    gated = {str(t.id) for t in tracks if str(t.id) in gates}
    allowed = ids - gated
    if not gated:
        return allowed
    entitled = await entitled_track_ids(session, user, gated) if user is not None else set()
    for track in tracks:
        tid = str(track.id)
        if tid not in gated:
            continue
        sale = gates[tid]
        if (
            user is not None and (user.is_admin or track.owner_id == user.id or sale.owner_id == user.id)
        ) or tid in entitled:
            allowed.add(tid)
    return allowed


async def downloadable_file_ids(
    session: AsyncSession,
    stored_file_ids: Iterable[str],
    user: Optional[User],
) -> Set[str]:
    """
    Batch version of :func:`file_download_access` for response builders.

    A stored file is downloadable when no *gated* track claims its bytes: an
    unreferenced file, or a file also reachable through an ungated track,
    stays open; a file referenced only by gated tracks requires ``full``
    access to at least one of them.
    """
    ids = {str(f) for f in stored_file_ids}
    if not ids:
        return set()

    file_tracks: Dict[str, Set[str]] = {}
    rows = await session.execute(select(Track.audio_file_id, Track.id).where(Track.audio_file_id.in_(ids)))
    for fid, tid in rows.all():
        if fid is not None:
            file_tracks.setdefault(str(fid), set()).add(str(tid))
    rows = await session.execute(
        select(TranscodedFile.stored_file_id, TranscodedFile.track_id).where(TranscodedFile.stored_file_id.in_(ids))
    )
    for fid, tid in rows.all():
        if fid is not None:
            file_tracks.setdefault(str(fid), set()).add(str(tid))

    all_track_ids = {tid for tids in file_tracks.values() for tid in tids}
    if not all_track_ids:
        return set(ids)

    result = await session.execute(select(Track).where(Track.id.in_(all_track_ids)))
    tracks = list(result.scalars().all())
    gates = await effective_gates(session, tracks)
    if not gates:
        return set(ids)

    gated_ids = set(gates)
    entitled = await entitled_track_ids(session, user, gated_ids) if user is not None else set()
    full_ids: Set[str] = set()
    for track in tracks:
        tid = str(track.id)
        if tid not in gated_ids:
            continue
        sale = gates[tid]
        if (
            user is not None and (user.is_admin or track.owner_id == user.id or sale.owner_id == user.id)
        ) or tid in entitled:
            full_ids.add(tid)

    allowed: Set[str] = set()
    for fid in ids:
        tids = file_tracks.get(fid)
        if not tids:
            allowed.add(fid)
            continue
        if any(tid not in gated_ids for tid in tids) or any(tid in full_ids for tid in tids):
            allowed.add(fid)
    return allowed
