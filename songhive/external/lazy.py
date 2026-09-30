"""
Lazy provider-contents refresh.

Adapters that advertise ``limits["lazy_contents"]`` (e.g. TIDAL playlists and
albums) enumerate container metadata during sync but never their tracks. The
first load of the entity calls :func:`ensure_contents`, which enqueues the
``refresh_external_contents`` Celery task when the cached contents are missing,
stale, or expired — deduplicated by a Redis ``SET NX`` lock so concurrent
requests enqueue at most one refresh.

Stale-while-revalidate: the cached ``PlaylistTrack`` rows keep being served
while the refresh runs; clients reload on the ``external_contents_refreshed``
WebSocket event the task publishes to the library owner.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload, selectinload

from ..models.external_item import ExternalItem
from ..services.secrets import decrypt_json

logger = logging.getLogger(__name__)

_LOCK_PREFIX = "songhive:external-contents:"
_LOCK_TTL_SECONDS = 300
_WAIT_POLL_SECONDS = 0.25

# ``ExternalItem.contents_error`` doubles as the staleness marker written by
# the playlist/album sync passes when the provider's ``lastUpdated`` advanced.
_STALE_MARKER = "stale"

_KIND_ENTITY_COLUMN = {
    "track": ExternalItem.track_id,
    "album": ExternalItem.album_id,
    "artist": ExternalItem.artist_id,
    "playlist": ExternalItem.playlist_id,
}


@dataclass
class ProviderSyncStatus:
    """Lazy-contents state reported on playlist/album responses."""

    provider_type: str
    state: str  # "fresh" | "refreshing" | "never_fetched" | "error"
    fetched_at: Optional[datetime] = None
    ttl_seconds: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "provider_type": self.provider_type,
            "state": self.state,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "ttl_seconds": self.ttl_seconds,
            "error": self.error,
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def contents_lock_key(external_library_id: str, kind: str, provider_key: str) -> str:
    return f"{_LOCK_PREFIX}{external_library_id}:{kind}:{provider_key}"


def _provider_instance_config(provider_type: str):
    try:
        from ..config.loader import load_config

        return getattr(load_config([]).external_libraries, provider_type, None)
    except Exception:
        return None


def _contents_ttl(provider_type: str, kind: str, config: dict) -> Optional[int]:
    """Resolve the contents TTL for a container kind.

    ``playlist``: library override ``playlist_ttl_seconds`` clamped to the
    instance minimum, defaulting to the instance TTL (6 h for TIDAL).
    ``album``: ``album_contents_ttl_seconds`` (30 days for TIDAL).
    """
    instance = _provider_instance_config(provider_type)
    if kind == "playlist":
        default = getattr(instance, "playlist_ttl_seconds", None) or 21600
        minimum = getattr(instance, "minimum_playlist_ttl_seconds", None) or 0
        raw = config.get("playlist_ttl_seconds")
        try:
            value = int(raw) if raw is not None else default
        except (TypeError, ValueError):
            value = default
        return max(minimum, value)
    if kind == "album":
        default = getattr(instance, "album_contents_ttl_seconds", None) or 2592000
        raw = config.get("album_contents_ttl_seconds")
        try:
            return int(raw) if raw is not None else default
        except (TypeError, ValueError):
            return default
    return None


def lazy_wait_seconds(provider_type: str) -> float:
    """How long non-WS clients (Subsonic) may wait for an in-flight refresh."""
    instance = _provider_instance_config(provider_type)
    return float(getattr(instance, "lazy_contents_wait_seconds", None) or 10)


async def find_external_item(
    session: AsyncSession,
    kind: str,
    entity_id: str,
) -> Optional[ExternalItem]:
    """Return the ``ExternalItem`` binding ``entity_id`` to a lazy provider."""
    column = _KIND_ENTITY_COLUMN.get(kind)
    if column is None:
        return None
    from ..models.external_library import ExternalLibrary

    result = await session.execute(
        select(ExternalItem)
        .join(ExternalLibrary, ExternalItem.external_library_id == ExternalLibrary.id)
        .where(
            column == str(entity_id),
            ExternalItem.kind == kind,
            ExternalLibrary.enabled.is_(True),
        )
        .options(
            selectinload(ExternalItem.external_library),
            raiseload(ExternalItem.track),
            raiseload(ExternalItem.album),
            raiseload(ExternalItem.artist),
            raiseload(ExternalItem.playlist),
        )
        .order_by(ExternalItem.last_seen_at.desc().nullslast())
        .limit(1)
    )
    return result.scalars().first()


async def _try_enqueue(item: ExternalItem, kind: str, *, redis) -> bool:
    """Acquire the dedupe lock and enqueue the refresh task."""
    if redis is None:
        from ..config.loader import load_config
        from ..services.redis import get_redis_client

        redis = get_redis_client(load_config([]))
    if redis is None:
        return False

    key = contents_lock_key(str(item.external_library_id), kind, item.provider_key)
    try:
        acquired = await redis.set(key, "1", nx=True, ex=_LOCK_TTL_SECONDS)
    except Exception:
        logger.debug("Could not acquire contents refresh lock %s", key, exc_info=True)
        acquired = None
    if not acquired:
        return False

    from ..tasks.external_libraries import refresh_external_contents_task

    refresh_external_contents_task.delay(
        str(item.external_library_id),
        kind,
        item.provider_key,
    )
    return True


async def ensure_contents(
    session: AsyncSession,
    entity_kind: str,
    entity_id: str,
    *,
    force: bool = False,
    redis=None,
) -> Optional[ProviderSyncStatus]:
    """
    Ensure a provider-backed entity's contents are fresh, refreshing if needed.

    Returns ``None`` for entities that are not provider-backed or whose
    provider does not advertise lazy contents for ``entity_kind``.
    """
    item = await find_external_item(session, entity_kind, entity_id)
    if item is None:
        return None

    external_library = item.external_library
    if external_library is None or not external_library.enabled:
        return None

    lazy_kinds = (external_library.capabilities or {}).get("limits", {}).get("lazy_contents") or []
    if entity_kind not in lazy_kinds:
        return None

    raw_config = external_library.config
    if isinstance(raw_config, str):
        try:
            config = decrypt_json(raw_config)
        except Exception:
            config = {}
    else:
        config = dict(raw_config or {})

    ttl = _contents_ttl(external_library.provider_type, entity_kind, config)
    fetched_at = item.contents_fetched_at
    error = item.contents_error
    if error == _STALE_MARKER:
        error = None

    never_fetched = fetched_at is None
    expired = fetched_at is not None and ttl is not None and (_utcnow() - fetched_at).total_seconds() >= ttl
    marked_stale = item.contents_error == _STALE_MARKER
    needs_refresh = force or never_fetched or expired or marked_stale or bool(error)
    if item.state != "active" and not force:
        # The provider reported the object gone (contents refresh marked it
        # missing) or it dropped out of the collection listing — retrying on
        # every view just fails again. A listing sync re-activates the item;
        # an explicit force refresh still retries.
        needs_refresh = False

    if needs_refresh:
        await _try_enqueue(item, entity_kind, redis=redis)

    if never_fetched:
        state = "never_fetched"
    elif error is not None:
        state = "error"
    elif needs_refresh:
        state = "refreshing"
    else:
        state = "fresh"

    return ProviderSyncStatus(
        provider_type=external_library.provider_type,
        state=state,
        fetched_at=fetched_at,
        ttl_seconds=ttl,
        error=error,
    )


async def wait_for_contents(
    session: AsyncSession,
    item: ExternalItem,
    timeout_seconds: float,
) -> None:
    """
    Bounded wait for an in-flight contents refresh (Subsonic path).

    Polls ``contents_fetched_at`` rather than joining the Celery result. The
    lock disappearing without a fetch timestamp means the task failed — stop
    early instead of waiting the full timeout.
    """
    if timeout_seconds <= 0:
        return
    started_fetched_at = item.contents_fetched_at
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    lock = contents_lock_key(str(item.external_library_id), item.kind, item.provider_key)

    from ..config.loader import load_config
    from ..services.redis import get_redis_client

    redis = get_redis_client(load_config([]))

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(_WAIT_POLL_SECONDS)
        try:
            await session.refresh(item)
        except Exception:
            return
        if item.contents_fetched_at is not None and item.contents_fetched_at != started_fetched_at:
            return
        if redis is not None:
            try:
                if not await redis.exists(lock):
                    return
            except Exception:
                return
