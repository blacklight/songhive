"""
Notification service: creation, listing, preferences, and retention.

Notifications are created for follows, likes, boosts, quotes, replies,
mentions, and share grants.  Delivery targets are resolved per (user, type)
from ``NotificationPreference`` rows; a missing row means the defaults
(in-app enabled, email and digest disabled).  The enabled targets are
snapshotted on ``delivered_targets`` at creation time.

In-app delivery pushes a ``notification`` event to the recipient's WebSocket
connections.  Individual email delivery is delegated to the
``send_notification_email`` Celery task and only queued for recipients with a
verified email address.  Digest delivery is deferred to the daily
``send_notification_digests`` task.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, cast

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models.notification import (
    Notification,
    NotificationPreference,
    NotificationType,
)
from ..models.user import User
from ..ws.events import EventWebSocket

logger = logging.getLogger(__name__)

# Delivery targets applied when no NotificationPreference row exists.
DEFAULT_IN_APP = True
DEFAULT_EMAIL = False
DEFAULT_EMAIL_DIGEST = False

NOTIFICATION_WS_EVENT = "notification"
NOTIFICATION_DELETED_WS_EVENT = "notification_deleted"
NOTIFICATION_UPDATED_WS_EVENT = "notification_updated"

# Payload keys rewritten when the source object of a notification is edited
# (a federated note, a local activity's published object).
OBJECT_SNAPSHOT_KEYS = (
    "object_content",
    "object_summary",
    "object_name",
    "object_url",
    "published",
    "object_mentions",
)


def _now_utc() -> datetime:
    """Return the current UTC time with an explicit timezone."""
    return datetime.now(timezone.utc)


def notification_to_dict(notification: Notification) -> Dict[str, Any]:
    """Serialize a notification for REST responses and WebSocket payloads."""
    return {
        "id": notification.id,
        "type": notification.type,
        "actor_url": notification.actor_url,
        "source_url": notification.source_url,
        "payload": notification.payload,
        "seen_at": notification.seen_at.isoformat() if notification.seen_at else None,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
    }


async def get_targets(
    session: AsyncSession,
    user_id: str,
    type: NotificationType,
) -> Tuple[bool, bool, bool]:
    """Return the ``(in_app, email, email_digest)`` targets for a user/type."""
    result = await session.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.type == type,
        )
    )
    pref = result.scalar_one_or_none()
    if pref is None:
        return DEFAULT_IN_APP, DEFAULT_EMAIL, DEFAULT_EMAIL_DIGEST
    return bool(pref.in_app), bool(pref.email), bool(pref.email_digest)


async def _find_unseen_duplicate(
    session: AsyncSession,
    user_id: str,
    type: NotificationType,
    actor_url: Optional[str],
    source_url: Optional[str],
) -> Optional[Notification]:
    """Return an existing unseen notification matching the dedup tuple."""
    result = await session.execute(
        select(Notification).where(
            Notification.user_id == user_id,
            Notification.type == type,
            Notification.actor_url == actor_url,
            Notification.source_url == source_url,
            Notification.seen_at.is_(None),
        )
    )
    return result.scalars().first()


def _enqueue_email_task(notification_id: str) -> None:
    """Queue the individual notification email task, tolerating a missing broker."""
    from ..tasks.email import send_notification_email

    try:
        send_notification_email.delay(notification_id)  # type: ignore
    except Exception as exc:  # kombu/redis broker errors vary; never fail the request
        logger.warning("Could not queue notification email for %s: %s", notification_id, exc)


async def create_notification(
    session: AsyncSession,
    *,
    user_id: str,
    type: NotificationType,
    actor_url: Optional[str] = None,
    source_url: Optional[str] = None,
    payload: Optional[dict] = None,
) -> Optional[Notification]:
    """
    Create a notification respecting the recipient's delivery preferences.

    Returns ``None`` when every delivery target is disabled, and the existing
    row when an identical unseen notification already exists.
    """
    in_app, email, email_digest = await get_targets(session, user_id, type)
    if not (in_app or email or email_digest):
        return None

    existing = await _find_unseen_duplicate(session, user_id, type, actor_url, source_url)
    if existing is not None:
        return existing

    delivered_targets = [
        target for target, enabled in (("in_app", in_app), ("email", email), ("email_digest", email_digest)) if enabled
    ]
    notification = Notification(
        user_id=user_id,
        type=type,
        actor_url=actor_url,
        source_url=source_url,
        payload=payload,
        delivered_targets=delivered_targets,
    )
    session.add(notification)
    await session.flush()

    if in_app:
        EventWebSocket.send_to_user(
            user_id,
            NOTIFICATION_WS_EVENT,
            notification_to_dict(notification),
        )

    if email:
        recipient = await session.get(User, user_id)
        if recipient is not None and recipient.email_verified and recipient.email:
            _enqueue_email_task(str(notification.id))

    return notification


async def list_notifications(
    session: AsyncSession,
    user_id: str,
    *,
    seen: Optional[bool] = None,
    types: Optional[List[NotificationType]] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Notification], int]:
    """Return a page of a user's notifications (newest first) and the total."""
    conditions = [Notification.user_id == user_id]
    if seen is not None:
        conditions.append(Notification.seen_at.is_not(None) if seen else Notification.seen_at.is_(None))
    if types is not None:
        conditions.append(Notification.type.in_(types))

    total_result = await session.execute(select(func.count(Notification.id)).where(*conditions))
    total = total_result.scalar() or 0

    result = await session.execute(
        select(Notification).where(*conditions).order_by(Notification.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all()), total


async def get_unread_count(session: AsyncSession, user_id: str) -> int:
    """Return the number of unseen notifications for a user."""
    result = await session.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.seen_at.is_(None),
        )
    )
    return result.scalar() or 0


async def get_preferences(session: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
    """Return the merged preference view: one entry per notification type."""
    result = await session.execute(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
    stored = {pref.type: pref for pref in result.scalars().all()}
    return [
        {
            "type": type,
            "in_app": bool(stored[type].in_app) if type in stored else DEFAULT_IN_APP,
            "email": bool(stored[type].email) if type in stored else DEFAULT_EMAIL,
            "email_digest": bool(stored[type].email_digest) if type in stored else DEFAULT_EMAIL_DIGEST,
        }
        for type in NotificationType
    ]


async def set_preference(
    session: AsyncSession,
    user_id: str,
    type: NotificationType,
    *,
    in_app: bool,
    email: bool,
    email_digest: bool,
) -> NotificationPreference:
    """Upsert the preference row for ``(user_id, type)``."""
    result = await session.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.type == type,
        )
    )
    pref = result.scalar_one_or_none()
    if pref is not None:
        pref.in_app = in_app
        pref.email = email
        pref.email_digest = email_digest
        await session.flush()
        return pref

    pref = NotificationPreference(
        user_id=user_id,
        type=type,
        in_app=in_app,
        email=email,
        email_digest=email_digest,
    )
    try:
        async with session.begin_nested():
            session.add(pref)
            await session.flush()
    except IntegrityError:
        # A concurrent insert won the race; update the existing row instead.
        existing = await session.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.type == type,
            )
        )
        pref = existing.scalar_one()
        pref.in_app = in_app
        pref.email = email
        pref.email_digest = email_digest
        await session.flush()
    return pref


async def mark_seen(session: AsyncSession, user_id: str, ids: List[str]) -> int:
    """Mark the given unseen notifications seen. Scoped to ``user_id``."""
    if not ids:
        return 0
    result = cast(
        CursorResult,
        await session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.id.in_(ids),
                Notification.seen_at.is_(None),
            )
            .values(seen_at=_now_utc())
        ),
    )
    return result.rowcount or 0


async def mark_unseen(session: AsyncSession, user_id: str, ids: List[str]) -> int:
    """Mark the given seen notifications unseen. Scoped to ``user_id``."""
    if not ids:
        return 0
    result = cast(
        CursorResult,
        await session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.id.in_(ids),
                Notification.seen_at.is_not(None),
            )
            .values(seen_at=None)
        ),
    )
    return result.rowcount or 0


async def mark_all_seen(session: AsyncSession, user_id: str) -> int:
    """Mark every unseen notification of the user seen. Returns the count."""
    result = cast(
        CursorResult,
        await session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.seen_at.is_(None),
            )
            .values(seen_at=_now_utc())
        ),
    )
    return result.rowcount or 0


async def purge_seen_notifications(
    session: AsyncSession,
    *,
    older_than_days: int,
) -> int:
    """Delete seen notifications older than the retention window.

    Unseen notifications (``seen_at IS NULL``) are never deleted. Returns the
    number of rows removed.
    """
    cutoff = _now_utc() - timedelta(days=older_than_days)
    result = cast(
        CursorResult,
        await session.execute(
            delete(Notification).where(
                Notification.seen_at.is_not(None),
                Notification.seen_at < cutoff,
            )
        ),
    )
    return result.rowcount or 0


async def _delete_where(session: AsyncSession, *conditions) -> Dict[str, List[str]]:
    """Delete notifications matching ``conditions``.

    Returns the deleted ids grouped by recipient ``user_id``. Flushes without
    committing; the caller owns the transaction.
    """
    result = await session.execute(select(Notification.id, Notification.user_id).where(*conditions))
    rows = result.all()
    if not rows:
        return {}
    await session.execute(delete(Notification).where(Notification.id.in_([row[0] for row in rows])))
    by_user: Dict[str, List[str]] = {}
    for notification_id, user_id in rows:
        by_user.setdefault(str(user_id), []).append(str(notification_id))
    return by_user


def _push_deleted(deleted: Dict[str, List[str]]) -> None:
    """Push ``notification_deleted`` events so live clients drop the rows."""
    for user_id, ids in deleted.items():
        if not ids:
            continue
        try:
            EventWebSocket.send_to_user(user_id, NOTIFICATION_DELETED_WS_EVENT, {"ids": ids})
        except Exception as exc:
            logger.warning("Could not push notification deletion for %s: %s", user_id, exc)


async def delete_notifications(session: AsyncSession, user_id: str, ids: List[str]) -> int:
    """Delete the given notifications. Scoped to ``user_id``."""
    if not ids:
        return 0
    deleted = await _delete_where(
        session,
        Notification.user_id == user_id,
        Notification.id.in_(ids),
    )
    _push_deleted(deleted)
    return sum(len(v) for v in deleted.values())


async def clear_notifications(session: AsyncSession, user_id: str) -> int:
    """Delete every notification of the user. Returns the count removed."""
    deleted = await _delete_where(session, Notification.user_id == user_id)
    _push_deleted(deleted)
    return sum(len(v) for v in deleted.values())


async def retract_notifications(
    session: AsyncSession,
    *,
    user_id: Optional[str] = None,
    type: Optional[NotificationType] = None,
    actor_urls: Optional[Iterable[str]] = None,
    source_url: Optional[str] = None,
) -> int:
    """Delete notifications matching the given retraction scope.

    Used when an interaction is undone or revoked (unfollow, unlike,
    un-announce, share-grant revocation, account deletion): the notification
    it produced is no longer applicable and is removed entirely — seen or
    not. ``user_id`` restricts the retraction to a single recipient;
    ``actor_urls`` matches any of the actor URL forms the actor may have
    been recorded under (federated actor URL, ``/users/<name>``, or the
    ``urn:songhive:user:<name>`` local fallback). Returns the number of rows
    removed. Flushes without committing; the caller owns the transaction.
    """
    conditions = []
    if user_id is not None:
        conditions.append(Notification.user_id == user_id)
    if type is not None:
        conditions.append(Notification.type == type)
    if actor_urls is not None:
        urls = [url for url in actor_urls if url]
        if not urls:
            return 0
        conditions.append(Notification.actor_url.in_(urls))
    if source_url is not None:
        conditions.append(Notification.source_url == source_url)
    if not conditions:
        return 0

    deleted = await _delete_where(session, *conditions)
    _push_deleted(deleted)
    return sum(len(v) for v in deleted.values())


def _payload_value_in(session: AsyncSession, key: str, values: List[str]):
    """Return a condition matching the JSON string ``payload[key]`` against ``values``.

    ``payload['key']`` renders as MySQL-style JSON syntax that SQLite cannot
    parse; ``json_extract`` works on both SQLite and MySQL, while PostgreSQL
    uses the native ``->>`` accessor.
    """
    dialect = getattr(getattr(session, "bind", None), "dialect", None)
    if dialect is not None and getattr(dialect, "name", None) == "postgresql":
        return Notification.payload[key].as_string().in_(values)
    return func.json_extract(Notification.payload, f"$.{key}").in_(values)


async def retract_notifications_referencing(
    session: AsyncSession,
    urls: Iterable[str],
    *,
    user_id: Optional[str] = None,
    actor_url: Optional[str] = None,
) -> int:
    """Delete notifications that reference any of ``urls``.

    A notification references a URL through ``source_url`` (the remote note
    or local route it links to) or through the payload's ``target_url`` (the
    object a reply/quote pointed at) and ``activity_id`` (the federated
    activity that produced it). Used when the referenced object or activity
    is deleted or retracted. Returns the number of rows removed. Flushes
    without committing; the caller owns the transaction.
    """
    url_list = [url for url in urls if url]
    if not url_list:
        return 0

    payload_refs = or_(
        _payload_value_in(session, "target_url", url_list),
        _payload_value_in(session, "activity_id", url_list),
    )

    conditions = [
        or_(
            Notification.source_url.in_(url_list),
            payload_refs,
        )
    ]
    if user_id is not None:
        conditions.append(Notification.user_id == user_id)
    if actor_url is not None:
        conditions.append(Notification.actor_url == actor_url)

    deleted = await _delete_where(session, *conditions)
    _push_deleted(deleted)
    return sum(len(v) for v in deleted.values())


async def _update_where(
    session: AsyncSession,
    fields_for: Callable[[Notification], Optional[Dict[str, Any]]],
    *conditions,
) -> Dict[str, List[Notification]]:
    """Merge per-row field updates into the payloads of matching rows.

    ``fields_for`` returns the fields to merge into a row's payload — a
    ``None`` value removes the key — or ``None`` to leave the row
    untouched. Returns the changed rows grouped by recipient ``user_id``.
    Flushes without committing; the caller owns the transaction.
    """
    result = await session.execute(select(Notification).where(*conditions))
    by_user: Dict[str, List[Notification]] = {}
    for notification in result.scalars().all():
        fields = fields_for(notification)
        if not fields:
            continue
        payload = dict(notification.payload or {})
        for key, value in fields.items():
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
        if payload != (notification.payload or {}):
            notification.payload = payload
            by_user.setdefault(str(notification.user_id), []).append(notification)
    if by_user:
        await session.flush()
    return by_user


def _push_updated(updated: Dict[str, List[Notification]]) -> None:
    """Push ``notification_updated`` events so live clients refresh the rows."""
    for user_id, rows in updated.items():
        if not rows:
            continue
        try:
            EventWebSocket.send_to_user(
                user_id,
                NOTIFICATION_UPDATED_WS_EVENT,
                {"notifications": [notification_to_dict(row) for row in rows]},
            )
        except Exception as exc:
            logger.warning("Could not push notification update for %s: %s", user_id, exc)


async def update_notifications_referencing(
    session: AsyncSession,
    urls: Iterable[str],
    *,
    fields_for: Callable[[Notification], Optional[Dict[str, Any]]],
    user_id: Optional[str] = None,
    actor_url: Optional[str] = None,
) -> int:
    """Patch the payloads of notifications whose source object is one of ``urls``.

    Used when a local or federated object is edited: the denormalized
    snapshot stored at creation (``object_*`` note fields, ``target_*``
    fields) is rewritten so rendered notifications reflect the current
    content. ``fields_for`` returns the fields to merge per row — a
    ``None`` value removes the key — or ``None`` to skip the row.
    ``user_id``/``actor_url`` restrict the update to a single recipient or
    producer. Returns the number of rows changed. Flushes without
    committing; the caller owns the transaction.
    """
    url_list = [url for url in urls if url]
    if not url_list:
        return 0

    conditions: List[ColumnElement[bool]] = [Notification.source_url.in_(url_list)]
    if user_id is not None:
        conditions.append(Notification.user_id == user_id)
    if actor_url is not None:
        conditions.append(Notification.actor_url == actor_url)

    updated = await _update_where(session, fields_for, *conditions)
    _push_updated(updated)
    return sum(len(v) for v in updated.values())


async def update_notifications_from_actor(
    session: AsyncSession,
    actor_url: str,
    *,
    fields: Dict[str, Any],
    user_id: Optional[str] = None,
) -> int:
    """Patch the payloads of every notification produced by ``actor_url``.

    Used when a federated actor document is updated: the ``actor_name``,
    ``actor_display_name`` and ``actor_avatar_url`` snapshots are refreshed
    so user cards keep rendering the current profile. ``None`` field values
    remove the key. Returns the number of rows changed. Flushes without
    committing; the caller owns the transaction.
    """
    if not actor_url or not fields:
        return 0

    conditions = [Notification.actor_url == actor_url]
    if user_id is not None:
        conditions.append(Notification.user_id == user_id)

    updated = await _update_where(session, lambda _notification: fields, *conditions)
    _push_updated(updated)
    return sum(len(v) for v in updated.values())


async def refresh_notifications_for_item(
    session: AsyncSession,
    *,
    item_type: str,
    item_id: str,
    title: Optional[str],
) -> int:
    """Refresh the denormalized title of notifications about an item.

    Notification payloads copy the referenced item's title (``item_title``
    for the card the notification links to, ``target_item_title`` for the
    object a reply/quote pointed at, plus the legacy ``track_title``) so
    rows stay renderable on their own; an item rename rewrites those
    references. A ``None`` title removes the keys. Returns the number of
    rows changed. Flushes without committing; the caller owns the
    transaction.
    """
    condition = or_(
        _payload_value_in(session, "item_id", [item_id]),
        _payload_value_in(session, "target_item_id", [item_id]),
    )

    def _fields(notification: Notification) -> Optional[Dict[str, Any]]:
        payload = notification.payload or {}
        fields: Dict[str, Any] = {}
        if payload.get("item_type") == item_type and payload.get("item_id") == item_id:
            fields["item_title"] = title
            if "track_title" in payload:
                fields["track_title"] = title
        if payload.get("target_item_type") == item_type and payload.get("target_item_id") == item_id:
            fields["target_item_title"] = title
        return fields or None

    updated = await _update_where(session, _fields, condition)
    _push_updated(updated)
    return sum(len(v) for v in updated.values())
