"""
Instance-wide provider catalog cache.

Entity-backed providers share catalog objects across users: a TIDAL track id
carries the same title/duration/ISRC regardless of which library references
it. This service owns ``provider_catalog_entries`` so materializing a known
id is a pure DB operation — zero provider API calls — even across users.

The cache is never exposed through the API; it only feeds materialization of
local rows the Songhive ACL already governs.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.provider_catalog import ProviderCatalogEntry


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_catalog_entry(
    session: AsyncSession,
    provider_type: str,
    kind: str,
    provider_key: str,
    *,
    allow_expired: bool = False,
) -> Optional[ProviderCatalogEntry]:
    """Return the cached entry, ``None`` when absent, expired, or unavailable."""
    result = await session.execute(
        select(ProviderCatalogEntry).where(
            ProviderCatalogEntry.provider_type == provider_type,
            ProviderCatalogEntry.kind == kind,
            ProviderCatalogEntry.provider_key == provider_key,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return None
    if entry.unavailable_at is not None:
        return None
    if not allow_expired and entry.expires_at is not None and entry.expires_at <= _utcnow():
        return None
    return entry


async def get_catalog_entries(
    session: AsyncSession,
    provider_type: str,
    kind: str,
    provider_keys: list[str],
    *,
    allow_expired: bool = False,
) -> dict[str, ProviderCatalogEntry]:
    """Batch variant of :func:`get_catalog_entry`, returning a key→entry map."""
    if not provider_keys:
        return {}
    result = await session.execute(
        select(ProviderCatalogEntry).where(
            ProviderCatalogEntry.provider_type == provider_type,
            ProviderCatalogEntry.kind == kind,
            ProviderCatalogEntry.provider_key.in_(provider_keys),
        )
    )
    now = _utcnow()
    found: dict[str, ProviderCatalogEntry] = {}
    for entry in result.scalars().all():
        if entry.unavailable_at is not None:
            continue
        if not allow_expired and entry.expires_at is not None and entry.expires_at <= now:
            continue
        found[entry.provider_key] = entry
    return found


async def upsert_catalog_entries(
    session: AsyncSession,
    provider_type: str,
    kind: str,
    payloads: list[dict[str, Any]],
    *,
    key_field: str = "id",
    write_once: bool = False,
    ttl_seconds: Optional[int] = None,
) -> int:
    """
    Insert catalog rows for ``payloads`` (each a raw provider object).

    ``key_field`` names the JSON key carrying the provider id (``"id"`` for
    tracks/albums/artists, ``"uuid"`` for TIDAL playlists). With
    ``write_once=True`` an existing row's payload is never rewritten —
    immutable-by-id semantics for track catalog entries. ``ttl_seconds`` sets
    ``expires_at``; ``None`` leaves it NULL (immutable). Returns the number of
    newly inserted rows.
    """
    if not payloads:
        return 0

    now = _utcnow()
    expires_at = now + timedelta(seconds=ttl_seconds) if ttl_seconds else None
    keys = [str(payload.get(key_field)) for payload in payloads if payload.get(key_field) is not None]
    if not keys:
        return 0

    existing_result = await session.execute(
        select(ProviderCatalogEntry.provider_key).where(
            ProviderCatalogEntry.provider_type == provider_type,
            ProviderCatalogEntry.kind == kind,
            ProviderCatalogEntry.provider_key.in_(keys),
        )
    )
    existing = set(existing_result.scalars().all())

    inserted = 0
    for payload in payloads:
        key = payload.get(key_field)
        if key is None:
            continue
        provider_key = str(key)
        # Queue bookkeeping keys must not persist into the stored payload.
        stored = {k: v for k, v in payload.items() if k not in ("_kind", "_key")}
        if provider_key in existing:
            if not write_once:
                entry = (
                    await session.execute(
                        select(ProviderCatalogEntry).where(
                            ProviderCatalogEntry.provider_type == provider_type,
                            ProviderCatalogEntry.kind == kind,
                            ProviderCatalogEntry.provider_key == provider_key,
                        )
                    )
                ).scalar_one()
                entry.payload = stored
                entry.fetched_at = now
                entry.expires_at = expires_at
                # A fresh listing sighting resurrects a previously-unavailable id.
                entry.unavailable_at = None
            continue
        entry = ProviderCatalogEntry(
            provider_type=provider_type,
            kind=kind,
            provider_key=provider_key,
            payload=stored,
            fetched_at=now,
            expires_at=expires_at,
        )
        session.add(entry)
        existing.add(provider_key)
        inserted += 1
    try:
        await session.flush()
    except IntegrityError:
        # A concurrent writer may have inserted the same key first; write-once
        # semantics make that benign, so surface it as a rollback for the
        # caller's savepoint to resolve instead of failing the whole sync.
        raise
    return inserted


async def upsert_catalog_contents(
    session: AsyncSession,
    provider_type: str,
    kind: str,
    provider_key: str,
    contents: list[dict[str, Any]],
    *,
    etag: Optional[str] = None,
) -> ProviderCatalogEntry:
    """Write the ordered child refs of a container (playlist/album) entry."""
    result = await session.execute(
        select(ProviderCatalogEntry).where(
            ProviderCatalogEntry.provider_type == provider_type,
            ProviderCatalogEntry.kind == kind,
            ProviderCatalogEntry.provider_key == provider_key,
        )
    )
    entry = result.scalar_one_or_none()
    now = _utcnow()
    if entry is None:
        entry = ProviderCatalogEntry(
            provider_type=provider_type,
            kind=kind,
            provider_key=provider_key,
        )
        session.add(entry)
    entry.contents = contents
    entry.contents_etag = etag
    entry.contents_fetched_at = now
    # A successful contents fetch is a fresh sighting — clear a prior
    # unavailable pin (e.g. the provider had delisted the container).
    entry.unavailable_at = None
    await session.flush()
    return entry


async def mark_unavailable(
    session: AsyncSession,
    provider_type: str,
    kind: str,
    provider_key: str,
) -> None:
    """Pin an id the provider reported as gone/unplayable."""
    result = await session.execute(
        select(ProviderCatalogEntry).where(
            ProviderCatalogEntry.provider_type == provider_type,
            ProviderCatalogEntry.kind == kind,
            ProviderCatalogEntry.provider_key == provider_key,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        entry = ProviderCatalogEntry(
            provider_type=provider_type,
            kind=kind,
            provider_key=provider_key,
        )
        session.add(entry)
    entry.unavailable_at = _utcnow()
    await session.flush()


async def clear_unavailable(
    session: AsyncSession,
    provider_type: str,
    kind: str,
    provider_key: str,
) -> None:
    """Remove the unavailable marker (a later listing returned the id again)."""
    result = await session.execute(
        select(ProviderCatalogEntry).where(
            ProviderCatalogEntry.provider_type == provider_type,
            ProviderCatalogEntry.kind == kind,
            ProviderCatalogEntry.provider_key == provider_key,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is not None:
        entry.unavailable_at = None
        await session.flush()


async def refresh_catalog_entry(
    session: AsyncSession,
    provider_type: str,
    kind: str,
    provider_key: str,
    payload: dict[str, Any],
    *,
    ttl_seconds: Optional[int] = None,
) -> ProviderCatalogEntry:
    """Force a payload rewrite (admin tooling) regardless of write-once rules."""
    result = await session.execute(
        select(ProviderCatalogEntry).where(
            ProviderCatalogEntry.provider_type == provider_type,
            ProviderCatalogEntry.kind == kind,
            ProviderCatalogEntry.provider_key == provider_key,
        )
    )
    entry = result.scalar_one_or_none()
    now = _utcnow()
    if entry is None:
        entry = ProviderCatalogEntry(
            provider_type=provider_type,
            kind=kind,
            provider_key=provider_key,
        )
        session.add(entry)
    entry.payload = payload
    entry.fetched_at = now
    entry.expires_at = now + timedelta(seconds=ttl_seconds) if ttl_seconds else None
    entry.unavailable_at = None
    await session.flush()
    return entry


async def expired_catalog_entries(
    session: AsyncSession,
    provider_type: Optional[str] = None,
    *,
    limit: int = 500,
) -> list[ProviderCatalogEntry]:
    """Return entries whose ``expires_at`` has passed (admin sweep helper)."""
    stmt = select(ProviderCatalogEntry).where(
        ProviderCatalogEntry.expires_at.isnot(None),
        ProviderCatalogEntry.expires_at <= _utcnow(),
    )
    if provider_type:
        stmt = stmt.where(ProviderCatalogEntry.provider_type == provider_type)
    result = await session.execute(stmt.limit(limit))
    return list(result.scalars().all())


_ENTITY_COLUMNS = {
    "track": "track_id",
    "album": "album_id",
    "artist": "artist_id",
    "playlist": "playlist_id",
}


async def _find_entity_external_item(
    session: AsyncSession,
    kind: str,
    entity_id: str,
):
    """Return the active ``ExternalItem`` backing an entity, if any."""
    from sqlalchemy.orm import raiseload, selectinload

    from ..models.external_item import ExternalItem

    column = _ENTITY_COLUMNS.get(kind)
    if column is None:
        return None
    result = await session.execute(
        select(ExternalItem)
        .options(
            selectinload(ExternalItem.external_library),
            # Callers only need the provider library; the entity links would
            # otherwise trigger a cascading selectin storm per request.
            raiseload(ExternalItem.track),
            raiseload(ExternalItem.album),
            raiseload(ExternalItem.artist),
            raiseload(ExternalItem.playlist),
        )
        .where(
            getattr(ExternalItem, column) == str(entity_id),
            ExternalItem.kind == kind,
            ExternalItem.state == "active",
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def editable_fields_for_entity(
    session: AsyncSession,
    kind: str,
    entity_id: str,
) -> Optional[list[str]]:
    """
    Return the provider's locally-editable field list for an entity, or
    ``None`` when the entity is not provider-managed (unrestricted).

    Providers declare ``capabilities["limits"]["editable_fields"]`` — e.g.
    TIDAL allows ``["genres", "tags"]`` because provider metadata is
    immutable. A declared-but-empty list locks every field.
    """
    item = await _find_entity_external_item(session, kind, entity_id)
    if item is None or item.external_library is None:
        return None
    limits = (item.external_library.capabilities or {}).get("limits") or {}
    fields = limits.get("editable_fields")
    if fields is None:
        return None
    return [str(f) for f in fields]


async def provider_type_for_entity(
    session: AsyncSession,
    kind: str,
    entity_id: str,
) -> Optional[str]:
    """Return the provider type backing an entity, or ``None`` when local."""
    item = await _find_entity_external_item(session, kind, entity_id)
    if item is None or item.external_library is None:
        return None
    return item.external_library.provider_type
