"""
Single-entity materialization for entity-backed external libraries.

Powers the "add to my collection" action on provider-search results: the
entity is resolved catalog-first (no provider round-trip when the payload is
already cached) and materialized through the same ``_apply_entity_*`` path
the sync pipeline uses — ``membership="saved"`` so the track lands in the
user's library.
"""

import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.album import Album
from ..models.artist import Artist
from ..models.external_library import ExternalLibrary
from ..models.external_sync_run import ExternalSyncRun
from ..models.playlist import Playlist
from ..models.track import Track
from ..models.user import User
from ..services import provider_catalog
from .errors import ExternalLibraryError, UnsupportedExternalOperation
from .registry import get_external_adapter
from .sync import (
    RunCounters,
    _apply_entity_album,
    _apply_entity_artist,
    _apply_entity_playlist,
    _apply_entity_track,
    _backfill_artist_images,
    _catalog_ttl_seconds,
    _utcnow,
)
from .types import (
    ExternalAlbumMetadata,
    ExternalArtistMetadata,
    ExternalItemRef,
    ExternalPlaylistMetadata,
)

logger = logging.getLogger(__name__)

_KINDS = ("track", "album", "artist", "playlist")


async def resolve_provider_entity(
    session: AsyncSession,
    external_library: ExternalLibrary,
    adapter: Any,
    config: dict,
    kind: str,
    provider_key: str,
) -> Optional[Any]:
    """
    Resolve an entity's metadata catalog-first, provider-fetch on miss.

    Returns an ``ExternalItemRef``/``External*Metadata`` matching ``kind`` or
    ``None`` when the provider can't supply it.
    """
    entry = await provider_catalog.get_catalog_entry(
        session,
        external_library.provider_type,
        kind,
        provider_key,
    )
    if entry is not None and isinstance(entry.payload, dict):
        item = adapter.entity_from_payload(config, kind, entry.payload)
        if item is not None:
            return item
    return await adapter.fetch_entity_metadata(config, kind, provider_key)


async def materialize_provider_entity(
    session: AsyncSession,
    external_library: ExternalLibrary,
    kind: str,
    provider_key: str,
    config: dict,
    user: User,
) -> Any:
    """
    Persist one provider entity as a local Songhive row.

    ``kind`` is one of ``track``/``album``/``artist``/``playlist``. Tracks are
    imported with ``membership="saved"`` (a ``LibraryTrack`` row). Returns the
    materialized model, or ``None`` when the provider cannot resolve the key.
    Raises ``UnsupportedExternalOperation`` for providers without
    ``entity_import``/search materialization support.
    """
    if kind not in _KINDS:
        raise ExternalLibraryError(f"Unsupported entity kind {kind!r}")
    adapter = get_external_adapter(external_library.provider_type)()
    limits = (external_library.capabilities or {}).get("limits") or {}
    if not limits.get("entity_import"):
        raise UnsupportedExternalOperation(
            f"Provider {external_library.provider_type!r} does not support entity import",
            operation="materialize",
        )

    item = await resolve_provider_entity(session, external_library, adapter, config, kind, provider_key)
    if item is None:
        return None

    run = ExternalSyncRun(
        external_library_id=str(external_library.id),
        triggered_by="import",
        triggered_by_user_id=str(user.id),
        status="running",
        started_at=_utcnow(),
    )
    session.add(run)
    await session.flush()
    counters = RunCounters()

    immutable = bool(limits.get("immutable_tracks"))
    lazy = kind in set(limits.get("lazy_contents") or ())

    async with session.begin_nested():
        entity: Any = None
        if isinstance(item, ExternalItemRef) and kind == "track":
            entity = await _apply_entity_track(
                session,
                external_library,
                item,
                run,
                counters,
                config,
                membership="saved",
                add_to_library=True,
                immutable=immutable,
            )
        elif isinstance(item, ExternalAlbumMetadata):
            entity = await _apply_entity_album(session, external_library, item, counters, config, lazy=lazy)
        elif isinstance(item, ExternalArtistMetadata):
            entity = await _apply_entity_artist(session, external_library, item, counters, config)
        elif isinstance(item, ExternalPlaylistMetadata):
            entity = await _apply_entity_playlist(session, external_library, item, counters, lazy=lazy)

    await _backfill_artist_images(
        session,
        adapter,
        config,
        counters,
        external_library.provider_type,
        catalog_ttl=_catalog_ttl_seconds(external_library.provider_type, config),
    )

    run.status = "success" if entity is not None else "failed"
    run.completed_at = _utcnow()
    run.items_seen = counters.items_seen
    run.details = {"entities": dict(counters.entity_counts), "import": {"kind": kind, "provider_key": provider_key}}
    await session.flush()
    return entity


def entity_public_id(entity: Any) -> Optional[str]:
    """Return the materialized entity's public id."""
    if isinstance(entity, (Track, Album, Artist, Playlist)):
        return str(entity.id)
    return None
