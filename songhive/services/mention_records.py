"""
Mention record service: the permanent per-user archive backing ``/mentions``.

Where notifications are transient — dismissible, purged by retention, and
skipped entirely when every delivery target is disabled — a ``MentionRecord``
is written for every local user an activity mentions so the mentions page can
always list each activity that ever addressed them.  Rows are keyed on
``(user_id, source, source_url)`` so re-delivery and edits update the record
instead of duplicating it; retractions (``Undo``/``Delete`` for federated
objects, activity retraction for local and Webmention rows) remove it again.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple, cast

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models.mention_record import MentionRecord, MentionSource

logger = logging.getLogger(__name__)

__all__ = [
    "MENTION_SOURCES",
    "MentionSource",
    "mention_to_dict",
    "upsert_mention",
    "list_mentions",
    "delete_mentions",
    "delete_mentions_referencing",
    "update_mentions_visibility",
    "update_mentions_from_actor",
]

MENTION_SOURCES = tuple(s.value for s in MentionSource)


def mention_to_dict(record: MentionRecord) -> Dict[str, Any]:
    """Serialize a mention record for REST responses."""
    return {
        "id": record.id,
        "source": record.source,
        "actor_url": record.actor_url,
        "actor_handle": (record.payload or {}).get("actor_handle"),
        "source_url": record.source_url,
        "activity_id": record.activity_id,
        "visibility": record.visibility,
        "payload": record.payload,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


async def upsert_mention(
    session: AsyncSession,
    *,
    user_id: str,
    source: MentionSource | str,
    source_url: str,
    actor_url: Optional[str] = None,
    activity_id: Optional[str] = None,
    visibility: Optional[str] = None,
    payload: Optional[dict] = None,
    merge_payload: bool = False,
) -> MentionRecord:
    """
    Insert or refresh the mention record for ``(user_id, source, source_url)``.

    Existing rows have their mutable fields rewritten — a re-delivered or
    edited mention updates the snapshot rather than duplicating.  With
    ``merge_payload`` the given payload keys are merged into the stored
    payload (a ``None`` value removes the key), matching the field-merge
    semantics the notification update path uses; otherwise ``payload``
    replaces the stored snapshot wholesale.
    """
    result = await session.execute(
        select(MentionRecord).where(
            MentionRecord.user_id == user_id,
            MentionRecord.source == MentionSource(source).value,
            MentionRecord.source_url == source_url,
        )
    )
    record = result.scalar_one_or_none()
    if record is not None:
        record.actor_url = actor_url
        record.activity_id = activity_id
        record.visibility = visibility
        if merge_payload and payload is not None:
            merged = dict(record.payload or {})
            for key, value in payload.items():
                if value is None:
                    merged.pop(key, None)
                else:
                    merged[key] = value
            record.payload = merged
        else:
            record.payload = payload
        await session.flush()
        return record

    record = MentionRecord(
        user_id=user_id,
        source=MentionSource(source).value,
        source_url=source_url,
        actor_url=actor_url,
        activity_id=activity_id,
        visibility=visibility,
        payload=payload,
    )
    try:
        async with session.begin_nested():
            session.add(record)
            await session.flush()
    except IntegrityError:
        # A concurrent insert won the race; update the existing row instead.
        existing = await session.execute(
            select(MentionRecord).where(
                MentionRecord.user_id == user_id,
                MentionRecord.source == MentionSource(source).value,
                MentionRecord.source_url == source_url,
            )
        )
        record = existing.scalar_one()
        record.actor_url = actor_url
        record.activity_id = activity_id
        record.visibility = visibility
        record.payload = payload
        await session.flush()
    return record


async def list_mentions(
    session: AsyncSession,
    user_id: str,
    *,
    sources: Optional[List[str]] = None,
    private_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[MentionRecord], int]:
    """Return a page of a user's mention records (newest first) and the total."""
    conditions = [MentionRecord.user_id == user_id]
    if sources is not None:
        conditions.append(MentionRecord.source.in_(sources))
    if private_only:
        conditions.append(MentionRecord.visibility != "public")

    total_result = await session.execute(select(func.count(MentionRecord.id)).where(*conditions))
    total = total_result.scalar() or 0

    result = await session.execute(
        select(MentionRecord).where(*conditions).order_by(MentionRecord.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all()), total


async def delete_mentions(
    session: AsyncSession,
    *,
    user_id: Optional[str] = None,
    source: Optional[MentionSource | str] = None,
    source_url: Optional[str] = None,
    actor_urls: Optional[Iterable[str]] = None,
    exclude_user_ids: Optional[Iterable[str]] = None,
) -> int:
    """Delete mention records matching the given scope.

    Mirrors ``retract_notifications``: used when a mention is retracted
    (``Undo``/``Delete``/activity retraction), when the actor who produced
    it is deleted, or when an edit drops a recipient from the mention set
    (``exclude_user_ids`` keeps the rows still addressed). Returns the
    number of rows removed. Flushes without committing; the caller owns
    the transaction.
    """
    conditions = []
    if user_id is not None:
        conditions.append(MentionRecord.user_id == user_id)
    if source is not None:
        conditions.append(MentionRecord.source == MentionSource(source).value)
    if source_url is not None:
        conditions.append(MentionRecord.source_url == source_url)
    if actor_urls is not None:
        urls = [url for url in actor_urls if url]
        if not urls:
            return 0
        conditions.append(MentionRecord.actor_url.in_(urls))
    if exclude_user_ids is not None:
        keep = [uid for uid in exclude_user_ids if uid]
        if keep:
            conditions.append(MentionRecord.user_id.notin_(keep))
    if not conditions:
        return 0

    result = cast(CursorResult, await session.execute(delete(MentionRecord).where(*conditions)))
    return result.rowcount or 0


def _payload_value_in(session: AsyncSession, key: str, values: List[str]) -> ColumnElement[bool]:
    """Return a condition matching the JSON string ``payload[key]`` against ``values``.

    Mirrors ``services.notifications._payload_value_in``: ``->>`` on
    PostgreSQL, ``json_extract`` on SQLite/MySQL.
    """
    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    if dialect is not None and getattr(dialect, "name", None) == "postgresql":
        return MentionRecord.payload[key].as_string().in_(values)
    return func.json_extract(MentionRecord.payload, f"$.{key}").in_(values)


async def delete_mentions_referencing(
    session: AsyncSession,
    urls: Iterable[str],
    *,
    user_id: Optional[str] = None,
    actor_url: Optional[str] = None,
) -> int:
    """Delete mention records that reference any of ``urls``.

    A record references a URL through ``source_url`` (the mentioning
    object) or through the payload's ``activity_id`` (the federated
    activity that delivered it). Used when the referenced object or
    activity is deleted, undone, or retracted. Returns the number of rows
    removed. Flushes without committing; the caller owns the transaction.
    """
    url_list = [url for url in urls if url]
    if not url_list:
        return 0

    conditions = [
        or_(
            MentionRecord.source_url.in_(url_list),
            _payload_value_in(session, "activity_id", url_list),
        )
    ]
    if user_id is not None:
        conditions.append(MentionRecord.user_id == user_id)
    if actor_url is not None:
        conditions.append(MentionRecord.actor_url == actor_url)

    result = cast(CursorResult, await session.execute(delete(MentionRecord).where(*conditions)))
    return result.rowcount or 0


async def update_mentions_visibility(
    session: AsyncSession,
    source_url: str,
    visibility: str,
) -> int:
    """Rewrite the stored visibility of every record for ``source_url``.

    Used when a local activity's audience changes so the ``private``
    filter keeps classifying the mention correctly. Returns the number of
    rows changed. Flushes without committing; the caller owns the
    transaction.
    """
    result = cast(
        CursorResult,
        await session.execute(
            update(MentionRecord).where(MentionRecord.source_url == source_url).values(visibility=visibility)
        ),
    )
    return result.rowcount or 0


async def update_mentions_from_actor(
    session: AsyncSession,
    actor_url: str,
    *,
    fields: Dict[str, Any],
    user_id: Optional[str] = None,
) -> int:
    """Patch the payloads of every mention record produced by ``actor_url``.

    Used when a federated actor document is updated: the ``actor_name``,
    ``actor_display_name`` and ``actor_avatar_url`` snapshots are refreshed
    so cards keep rendering the current profile. ``None`` field values
    remove the key. Returns the number of rows changed. Flushes without
    committing; the caller owns the transaction.
    """
    if not actor_url or not fields:
        return 0

    conditions = [MentionRecord.actor_url == actor_url]
    if user_id is not None:
        conditions.append(MentionRecord.user_id == user_id)

    result = await session.execute(select(MentionRecord).where(*conditions))
    changed = 0
    for record in result.scalars().all():
        payload = dict(record.payload or {})
        for key, value in fields.items():
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
        if payload != (record.payload or {}):
            record.payload = payload
            changed += 1
    if changed:
        await session.flush()
    return changed
