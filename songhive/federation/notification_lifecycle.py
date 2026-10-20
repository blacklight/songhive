"""Inbox notification lifecycle — create handling, retract, update."""

import logging
from typing import Any, Dict, List, Optional

from pubby.moderation import extract_domain
from pubby.quotes import extract_quote_target
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.mention_record import MentionSource
from ..models.notification import Notification, NotificationType
from ..models.user import User
from ..services import federation as federation_service
from ..services import mention_records as mention_records_service
from ..services.activities import (
    _actor_doc_avatar_url,
    _actor_doc_display_name,
)
from ..services.notifications import (
    OBJECT_SNAPSHOT_KEYS,
    create_notification,
    delete_notifications,
    retract_notifications,
    retract_notifications_referencing,
    update_notifications_from_actor,
    update_notifications_referencing,
)
from .notification_creators import (
    _process_incoming_quote_notification,
    _process_incoming_reply_notification,
)
from .notification_helpers import (
    _ACTOR_TYPES,
    _actor_name,
    _mentions_recipient,
    _note_snapshot,
    _record_inbox_mention,
    _resolve_local_object,
    _stamp_mention_rows,
    _target_owner_users,
)

logger = logging.getLogger(__name__)


async def _handle_inbox_create(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    payload: Dict[str, Any],
    obj: dict,
    instance_domain: Optional[str],
) -> None:
    """Route an inbound ``Create`` to quote/reply/mention notifications."""
    note_id = obj.get("id")
    source_url = note_id if isinstance(note_id, str) else None
    quote_target = extract_quote_target(obj)
    in_reply_to = obj.get("inReplyTo")
    note = _note_snapshot(obj)
    # The note itself may map to a stored activity — remote replies are
    # materialized as rows — so clients can render its real card and link
    # to its ``/activities/{id}`` page rather than a remote object id that
    # may not dereference to a browser page (e.g. private Akkoma notes).
    self_fields = await _resolve_local_object(session, source_url, instance_domain) or {}

    # ``thread_notified`` suppresses a redundant ``mention`` for the same
    # note: a reply/quote notification already tells the recipient the note
    # concerns them — quoting or replying to their post always tags them.
    thread_notified = False
    if quote_target:
        thread_notified = await _process_incoming_quote_notification(
            session,
            actor_url=actor_url,
            recipient=recipient,
            payload=payload,
            quote_target=quote_target,
            instance_domain=instance_domain,
            source_url=source_url,
            note=note,
            self_fields=self_fields,
        )
    elif isinstance(in_reply_to, str) and in_reply_to:
        thread_notified = await _process_incoming_reply_notification(
            session,
            actor_url=actor_url,
            recipient=recipient,
            payload=payload,
            in_reply_to=in_reply_to,
            instance_domain=instance_domain,
            source_url=source_url,
            note=note,
            self_fields=self_fields,
        )

    await _notify_inbox_mention(
        session,
        actor_url=actor_url,
        recipient=recipient,
        obj=obj,
        source_url=source_url,
        payload={**payload, **note, **self_fields},
        self_fields=self_fields,
        thread_notified=thread_notified,
    )


async def _notify_inbox_mention(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    obj: dict,
    source_url: Optional[str],
    payload: Dict[str, Any],
    self_fields: Dict[str, Any],
    thread_notified: bool,
) -> None:
    """Create the mention notification when the recipient was tagged."""
    mentioned = _mentions_recipient(obj, recipient.actor_url)
    if not thread_notified and mentioned:
        mention = await create_notification(
            session,
            user_id=recipient.id,
            type=NotificationType.MENTION,
            actor_url=actor_url,
            source_url=source_url,
            payload=payload,
        )
        if mention is not None:
            await _stamp_mention_rows(session, recipient)
    elif thread_notified and mentioned:
        # The reply/quote notification covered the mention — the pending
        # ``ActivityMention`` rows are still marked as notified.
        await _stamp_mention_rows(session, recipient)

    # The permanent archive records the mention even when its
    # notification was suppressed as redundant or undelivered.
    if mentioned and source_url:
        await _record_inbox_mention(
            session,
            recipient=recipient,
            actor_url=actor_url,
            source_url=source_url,
            obj=obj,
            payload=payload,
            self_fields=self_fields,
        )


async def _notify_like_announce(
    session: AsyncSession,
    *,
    activity_type: str,
    actor_url: str,
    recipient: User,
    payload: Dict[str, Any],
    obj: Any,
    instance_domain: Optional[str],
) -> None:
    source_url = obj if isinstance(obj, str) else None
    resolved = await _resolve_local_object(session, source_url, instance_domain)
    owners = await _target_owner_users(session, {source_url}, instance_domain=instance_domain) if source_url else []
    if resolved is None or not any(str(owner.id) == str(recipient.id) for owner in owners):
        # Only the target's owner gets "liked/boosted your post" — a
        # like/boost delivered to a follower inbox (e.g. a followed
        # actor relaying a remote post) does not concern the recipient,
        # matching ``_notify_reaction`` on the local path.
        return
    await create_notification(
        session,
        user_id=recipient.id,
        type=NotificationType.LIKE if activity_type == "Like" else NotificationType.BOOST,
        actor_url=actor_url,
        source_url=source_url,
        payload={**payload, **resolved},
    )


async def retract_inbox_notifications(
    session: AsyncSession,
    *,
    activity: dict,
    recipient: User,
) -> int:
    """
    Retract notifications when an incoming activity undoes or deletes
    earlier ones.

    ``recipient`` is the local user whose inbox was addressed; only their
    notifications are affected, and only notifications produced by the
    activity's own ``actor``. Returns the number of rows removed.
    """
    actor_url = activity.get("actor")
    if not isinstance(actor_url, str) or not actor_url:
        return 0

    activity_type = activity.get("type")
    if activity_type == "Undo":
        return await _retract_undo(session, activity=activity, recipient=recipient, actor_url=actor_url)
    if activity_type == "Delete":
        return await _retract_delete(session, activity=activity, recipient=recipient, actor_url=actor_url)
    return 0


async def _retract_undo(
    session: AsyncSession,
    *,
    activity: dict,
    recipient: User,
    actor_url: str,
) -> int:
    inner = activity.get("object")
    inner_id: Optional[str] = None
    inner_type: Optional[str] = None
    if isinstance(inner, dict):
        raw_id = inner.get("id")
        inner_id = raw_id if isinstance(raw_id, str) else None
        raw_type = inner.get("type")
        inner_type = raw_type if isinstance(raw_type, str) else None
    elif isinstance(inner, str):
        # Undo carrying only the original activity's id.
        inner_id = inner

    removed = 0
    if inner_id:
        removed += await retract_notifications_referencing(
            session, [inner_id], user_id=recipient.id, actor_url=actor_url
        )
        # An undone ``Create`` also removes the mention archive record.
        await mention_records_service.delete_mentions_referencing(
            session, [inner_id], user_id=recipient.id, actor_url=actor_url
        )

    if inner_type == "Follow":
        removed += await retract_notifications(
            session, user_id=recipient.id, type=NotificationType.FOLLOW, actor_urls=[actor_url]
        )
    elif inner_type in ("Like", "Announce") and isinstance(inner, dict):
        target = inner.get("object")
        if isinstance(target, dict):
            target = target.get("id")
        removed += await retract_notifications(
            session,
            user_id=recipient.id,
            type=NotificationType.LIKE if inner_type == "Like" else NotificationType.BOOST,
            actor_urls=[actor_url],
            source_url=target if isinstance(target, str) else None,
        )

    return removed


async def _retract_delete(
    session: AsyncSession,
    *,
    activity: dict,
    recipient: User,
    actor_url: str,
) -> int:
    obj = activity.get("object")
    target: Optional[str] = None
    target_type: Optional[str] = None
    if isinstance(obj, dict):
        raw_id = obj.get("id")
        target = raw_id if isinstance(raw_id, str) else None
        raw_type = obj.get("type")
        target_type = raw_type if isinstance(raw_type, str) else None
    elif isinstance(obj, str):
        target = obj
    if not target:
        return 0

    if target == actor_url or target_type in _ACTOR_TYPES:
        # The actor itself was deleted: every notification they produced for
        # the recipient is now dangling — as is every mention record.
        await mention_records_service.delete_mentions(session, user_id=recipient.id, actor_urls=[actor_url])
        return await retract_notifications(session, user_id=recipient.id, actor_urls=[actor_url])

    # Delete may reference either the object itself (a deleted note, which is
    # a quote/reply/mention's source_url) or the activity that created it.
    assert target  # for mypy
    await mention_records_service.delete_mentions_referencing(
        session, [target], user_id=recipient.id, actor_url=actor_url
    )
    return await retract_notifications_referencing(session, [target], user_id=recipient.id, actor_url=actor_url)


async def update_inbox_notifications(
    session: AsyncSession,
    *,
    activity: dict,
    recipient: User,
    instance_domain: Optional[str] = None,
) -> int:
    """
    Sync notifications when an incoming ``Update`` revises a known object.

    ``recipient`` is the local user whose inbox was addressed; only their
    notifications are affected, and only notifications produced by the
    activity's own ``actor``. Non-``Update`` activities, string objects, and
    objects no notification references are no-ops. Returns the number of
    rows changed (updated or retracted).
    """
    if activity.get("type") != "Update":
        return 0
    actor_url = activity.get("actor")
    obj = activity.get("object")
    if not isinstance(actor_url, str) or not actor_url or not isinstance(obj, dict):
        return 0
    obj_id = obj.get("id")
    if not isinstance(obj_id, str) or not obj_id:
        return 0

    obj_type = obj.get("type")
    if obj_id == actor_url or (isinstance(obj_type, str) and obj_type in _ACTOR_TYPES):
        # An actor document may only be revised by an actor on its own
        # instance; anything else is a spoofed cross-domain update.
        if extract_domain(obj_id or "") != extract_domain(actor_url):
            return 0
        return await _update_actor_notifications(session, obj=obj, recipient=recipient, actor_url=obj_id or "")
    return await _update_object_notifications(
        session,
        obj=obj,
        recipient=recipient,
        actor_url=actor_url,
        instance_domain=instance_domain,
    )


async def _update_actor_notifications(
    session: AsyncSession,
    *,
    obj: dict,
    recipient: User,
    actor_url: str,
) -> int:
    """Refresh ``actor_*`` payload fields from an updated actor document."""
    preferred = obj.get("preferredUsername")
    fields = {
        "actor_name": preferred if isinstance(preferred, str) and preferred else _actor_name(actor_url),
        "actor_handle": federation_service.actor_doc_handle(obj, actor_url),
        "actor_display_name": _actor_doc_display_name(obj),
        "actor_avatar_url": _actor_doc_avatar_url(obj),
    }
    updated = await update_notifications_from_actor(session, actor_url, fields=fields, user_id=recipient.id)
    updated += await mention_records_service.update_mentions_from_actor(
        session, actor_url, fields=fields, user_id=recipient.id
    )
    return updated


async def _update_object_notifications(
    session: AsyncSession,
    *,
    obj: dict,
    recipient: User,
    actor_url: str,
    instance_domain: Optional[str],
) -> int:
    """
    Rewrite the note snapshot of notifications sourced from ``obj``.

    Rows whose basis disappeared are retracted rather than updated: a
    ``mention`` whose ``tag`` no longer names the recipient, a ``reply`` or
    ``quote`` whose target was removed, and a ``reply``/``quote`` whose
    target moved to a different object than the one the notification
    recorded.
    """
    obj_id = obj["id"]
    note = _note_snapshot(obj)
    base = {key: note.get(key) for key in OBJECT_SNAPSHOT_KEYS}

    in_reply_to = obj.get("inReplyTo")
    reply_target = in_reply_to if isinstance(in_reply_to, str) and in_reply_to else None
    quote_target = extract_quote_target(obj)
    resolved_self = await _resolve_local_object(session, obj_id, instance_domain)
    resolved_reply = await _resolve_local_object(session, reply_target, instance_domain) if reply_target else None
    resolved_quote = await _resolve_local_object(session, quote_target, instance_domain) if quote_target else None
    self_fields = resolved_self or {}

    def _target_fields(url: str, resolved: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        resolved = resolved or {}
        return {
            "target_url": url,
            "target_item_type": resolved.get("item_type"),
            "target_item_id": resolved.get("item_id"),
            "target_item_title": resolved.get("item_title"),
            "target_local_url": resolved.get("local_url"),
            "target_object_activity_id": resolved.get("object_activity_id"),
            "target_object_page_url": resolved.get("object_page_url"),
            "target_object_type": resolved.get("object_type"),
        }

    retract_ids: List[str] = []

    def _fields(notification: Notification) -> Optional[Dict[str, Any]]:
        if notification.type == NotificationType.MENTION:
            if not _mentions_recipient(obj, recipient.actor_url):
                retract_ids.append(str(notification.id))
                return None
            return {**base, **self_fields}
        # Only note notifications carry an object snapshot; likes/boosts
        # reference the object id without rendering it.
        if notification.type not in (NotificationType.REPLY, NotificationType.QUOTE):
            return None
        target, resolved = (
            (reply_target, resolved_reply)
            if notification.type == NotificationType.REPLY
            else (quote_target, resolved_quote)
        )
        stored = (notification.payload or {}).get("target_url")
        if target is None or (isinstance(stored, str) and stored and stored != target):
            retract_ids.append(str(notification.id))
            return None
        return {**base, **self_fields, **_target_fields(target, resolved)}

    updated = await update_notifications_referencing(
        session,
        [obj_id],
        fields_for=_fields,
        user_id=recipient.id,
        actor_url=actor_url,
    )
    removed = 0
    if retract_ids:
        removed = await delete_notifications(session, recipient.id, retract_ids)

    # Sync the permanent mention archive the same way: a mention that
    # survived the edit refreshes its snapshot — merged so the actor and
    # activity fields recorded at ``Create`` are preserved — while one
    # the edit removed drops its record.
    if _mentions_recipient(obj, recipient.actor_url):
        await _record_inbox_mention(
            session,
            recipient=recipient,
            actor_url=actor_url,
            source_url=obj_id,
            obj=obj,
            payload={**base, **self_fields},
            self_fields=self_fields,
            merge_payload=True,
        )
    else:
        await mention_records_service.delete_mentions(
            session,
            user_id=recipient.id,
            source=MentionSource.ACTIVITYPUB,
            source_url=obj_id,
        )
    return updated + removed
