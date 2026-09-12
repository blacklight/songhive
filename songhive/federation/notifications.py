"""
Notification hooks for incoming ActivityPub activities.

Maps federated interactions to user notifications:

- ``Follow`` → ``follow``
- ``Like`` → ``like``
- ``Announce`` → ``boost``
- ``Create`` (Note): ``quote`` when the note quotes a local object URL,
  otherwise ``reply`` when it has ``inReplyTo`` (a note that is both emits
  only ``quote``), plus ``mention`` whenever the note's ``tag`` entries
  mention the recipient's actor URL. Creating a ``mention`` notification also
  stamps ``ActivityMention.notified_at`` on the recipient's pending mention
  rows.

The incoming activity's own ``id`` is recorded in each notification's
``payload.activity_id`` so a later ``Undo`` or ``Delete`` can retract the
notification even when it only references the original activity id:

- ``Undo(Follow)`` → removes the actor's ``follow`` notifications
- ``Undo(Like)`` / ``Undo(Announce)`` → removes the actor's ``like`` /
  ``boost`` notification for the undone object
- ``Delete`` → removes notifications referencing the deleted object or
  activity id; deleting the actor itself removes every notification the
  actor produced for the recipient
- ``Update`` → rewrites the stored payload snapshot of notifications
  sourced from the edited object (``object_*``/``target_*`` fields),
  retracts rows whose basis disappeared (a removed mention, a dropped or
  retargeted ``inReplyTo``/quote), and refreshes ``actor_*`` fields when
  the updated object is the actor document itself
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from pubby.moderation import extract_domain
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.activity import Activity, ActivityMention
from ..models.notification import Notification, NotificationType
from ..models.track import Track
from ..models.user import User
from ..services import acl
from ..services.activities import (
    _actor_doc_avatar_url,
    _actor_doc_display_name,
    resolve_entity,
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

logger = logging.getLogger(__name__)

# ActivityStreams types that identify an actor document rather than content.
_ACTOR_TYPES = {"Application", "Group", "Organization", "Person", "Service"}

# Bound the remote note snapshot stored in ``payload``.
_MAX_CONTENT_SNAPSHOT = 10_000
_MAX_MENTION_SNAPSHOT = 50

# ``/{plural}/{id}`` frontend page paths that identify a local object.
_PLURAL_TO_ITEM_TYPE = {
    "tracks": "track",
    "albums": "album",
    "artists": "artist",
    "playlists": "playlist",
    "libraries": "library",
    "radios": "radio",
    "files": "file",
}


def _actor_name(actor_url: Optional[str]) -> Optional[str]:
    """Derive a display name from the last segment of an actor URL."""
    if not actor_url:
        return None
    name = actor_url.strip().rstrip("/").rsplit("/", 1)[-1]
    return name or None


def _object_url(obj: dict) -> Optional[str]:
    """
    Extract the preferred human-facing URL of an embedded object.

    ``url`` may be a plain string, a ``Link`` dict, or a list of them; the
    first ``text/html`` link wins so remote readers land on a page rather
    than the JSON document. Falls back to the object ``id``.
    """
    url = obj.get("url")
    if isinstance(url, str) and url:
        return url
    links = url if isinstance(url, list) else [url]
    candidates = [link for link in links if isinstance(link, dict) and link.get("href")]
    for link in candidates:
        if link.get("mediaType") == "text/html":
            return link["href"]
    if candidates:
        return candidates[0]["href"]
    note_id = obj.get("id")
    return note_id if isinstance(note_id, str) and note_id else None


def _note_snapshot(obj: dict) -> Dict[str, Any]:
    """
    Snapshot the renderable fields of an incoming ``Note`` object.

    Remote notes are not persisted as ``Activity`` rows, so the notification
    payload carries enough context to render an activity card: the raw HTML
    ``content`` (clients reduce it to text and links), the ``summary`` content
    warning, an optional ``name`` title, the human-facing ``object_url``, the
    ``published`` timestamp, and the note's ``Mention`` tags (``handle`` +
    ``actor_url``) so ``@handle`` text links to the real actor.
    """
    snapshot: Dict[str, Any] = {}
    content = obj.get("content")
    if isinstance(content, str) and content:
        snapshot["object_content"] = content[:_MAX_CONTENT_SNAPSHOT]
    summary = obj.get("summary")
    if isinstance(summary, str) and summary:
        snapshot["object_summary"] = summary[:1000]
    name = obj.get("name")
    if isinstance(name, str) and name:
        snapshot["object_name"] = name[:500]
    object_url = _object_url(obj)
    if object_url:
        snapshot["object_url"] = object_url
    published = obj.get("published")
    if isinstance(published, str) and published:
        snapshot["published"] = published
    tags = obj.get("tag")
    if isinstance(tags, list):
        mentions = []
        for tag in tags:
            if not isinstance(tag, dict) or tag.get("type") != "Mention":
                continue
            href = tag.get("href")
            if not isinstance(href, str) or not href:
                continue
            mention_name = tag.get("name")
            mentions.append(
                {
                    "handle": mention_name if isinstance(mention_name, str) and mention_name else href,
                    "actor_url": href,
                }
            )
        if mentions:
            snapshot["object_mentions"] = mentions[:_MAX_MENTION_SNAPSHOT]
    return snapshot


async def _resolve_local_object(
    session: AsyncSession,
    url: Optional[str],
    instance_domain: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Resolve a federated object URL to a local item for display.

    Handles the ``{actor_url}/objects/{id}`` form used by this instance's
    ``Audio``/``Note`` objects (``Track.federation_object_id`` first, then
    ``Activity.local_object_id``/``source_id``) and ``/{plural}/{id}`` page
    URLs on the instance domain. Returns ``{item_type, item_id, item_title,
    local_url}`` so the client can render the referenced track or entity
    with its own link; ``None`` when the object is not local.
    """
    if not isinstance(url, str) or not url:
        return None

    parsed = urlparse(url)
    path = parsed.path or ""

    item = None
    item_type: Optional[str] = None
    objects_match = re.search(r"/objects/([^/?#]+)/?$", path)
    if objects_match:
        object_id = objects_match.group(1)
        track = await session.scalar(select(Track).where(Track.federation_object_id == object_id))
        if track is not None:
            item, item_type = track, "track"
        else:
            activity = await session.scalar(
                select(Activity).where(
                    or_(
                        Activity.local_object_id == object_id,
                        Activity.source_id == object_id,
                        Activity.source_id == url,
                    )
                )
            )
            if activity is not None:
                item = await resolve_entity(session, activity.entity_type, activity.entity_id)
                item_type = activity.entity_type
    elif instance_domain and parsed.netloc == instance_domain:
        parts = [p for p in path.split("/") if p]
        candidate_type = _PLURAL_TO_ITEM_TYPE.get(parts[0]) if len(parts) == 2 else None
        if candidate_type is not None:
            item_type = candidate_type
            item = await acl.get_item(session, candidate_type, parts[1])

    if item is None or item_type is None:
        return None
    plural = acl.get_item_plural(item_type) or item_type
    return {
        "item_type": item_type,
        "item_id": str(item.id),
        "item_title": getattr(item, "title", None) or getattr(item, "name", None),
        "local_url": f"/{plural}/{item.id}",
    }


def _extract_quote_target(obj: dict) -> Optional[str]:
    """
    Extract the quoted object URL from a Create object, if present.

    Checks the FEP-0449 ``quote`` field, Mastodon's ``quoteUrl``, and
    Misskey's ``_misskey_quote``.
    """
    for key in ("quote", "quoteUrl", "_misskey_quote"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _mentions_recipient(obj: dict, actor_url: Optional[str]) -> bool:
    """Return True when the object's Mention tags address ``actor_url``."""
    if not actor_url:
        return False
    tags = obj.get("tag")
    if not isinstance(tags, list):
        return False
    return any(
        isinstance(tag, dict) and tag.get("type") == "Mention" and (tag.get("href") == actor_url) for tag in tags
    )


async def _stamp_mention_rows(session: AsyncSession, recipient: User) -> None:
    """Mark the recipient's pending ``ActivityMention`` rows as notified."""
    conditions = [ActivityMention.user_id == recipient.id]
    if recipient.actor_url:
        conditions.append(ActivityMention.actor_url == recipient.actor_url)
    await session.execute(
        update(ActivityMention)
        .where(
            or_(*conditions),
            ActivityMention.notified_at.is_(None),
        )
        .values(notified_at=datetime.now(timezone.utc))
    )


async def create_inbox_notifications(
    session: AsyncSession,
    *,
    activity: dict,
    recipient: User,
    actor_doc: Optional[dict] = None,
    instance_domain: Optional[str] = None,
) -> None:
    """
    Create notifications for an incoming federated activity.

    ``recipient`` is the local user whose inbox was addressed. Unknown or
    unsupported activity types are ignored.

    ``actor_doc`` is the actor's cached ActivityPub document (populated by
    the inbox signature verification); its ``name``/``icon`` enrich the
    payload's ``actor_display_name``/``actor_avatar_url`` so clients can
    render a user card. ``instance_domain`` enables resolving the activity's
    object to a local item (``item_*``/``local_url`` payload fields).
    """
    actor_url = activity.get("actor")
    if not isinstance(actor_url, str) or not actor_url:
        return

    activity_id = activity.get("id")
    if not isinstance(activity_id, str) or not activity_id:
        activity_id = None

    activity_type = activity.get("type")
    obj = activity.get("object")
    payload: Dict[str, Any] = {"actor_name": _actor_name(actor_url), "activity_id": activity_id}
    if isinstance(actor_doc, dict):
        display_name = _actor_doc_display_name(actor_doc)
        avatar_url = _actor_doc_avatar_url(actor_doc)
        if display_name:
            payload["actor_display_name"] = display_name
        if avatar_url:
            payload["actor_avatar_url"] = avatar_url

    if activity_type == "Follow":
        # The Follow object is the followed actor (the recipient); link the
        # notification to the follower's actor URL instead.
        await create_notification(
            session,
            user_id=recipient.id,
            type=NotificationType.FOLLOW,
            actor_url=actor_url,
            source_url=actor_url,
            payload=payload,
        )
        return

    if activity_type in ("Like", "Announce"):
        source_url = obj if isinstance(obj, str) else None
        resolved = await _resolve_local_object(session, source_url, instance_domain)
        await create_notification(
            session,
            user_id=recipient.id,
            type=NotificationType.LIKE if activity_type == "Like" else NotificationType.BOOST,
            actor_url=actor_url,
            source_url=source_url,
            payload={**payload, **(resolved or {})},
        )
        return

    if activity_type != "Create" or not isinstance(obj, dict):
        return

    note_id = obj.get("id")
    source_url = note_id if isinstance(note_id, str) else None
    quote_target = _extract_quote_target(obj)
    in_reply_to = obj.get("inReplyTo")
    note = _note_snapshot(obj)

    if quote_target:
        resolved = await _resolve_local_object(session, quote_target, instance_domain)
        target_fields = {f"target_{key}": value for key, value in (resolved or {}).items()}
        await create_notification(
            session,
            user_id=recipient.id,
            type=NotificationType.QUOTE,
            actor_url=actor_url,
            source_url=source_url,
            payload={**payload, **note, "target_url": quote_target, **target_fields},
        )
    elif isinstance(in_reply_to, str) and in_reply_to:
        resolved = await _resolve_local_object(session, in_reply_to, instance_domain)
        target_fields = {f"target_{key}": value for key, value in (resolved or {}).items()}
        await create_notification(
            session,
            user_id=recipient.id,
            type=NotificationType.REPLY,
            actor_url=actor_url,
            source_url=source_url,
            payload={**payload, **note, "target_url": in_reply_to, **target_fields},
        )

    if _mentions_recipient(obj, recipient.actor_url):
        mention = await create_notification(
            session,
            user_id=recipient.id,
            type=NotificationType.MENTION,
            actor_url=actor_url,
            source_url=source_url,
            payload={**payload, **note},
        )
        if mention is not None:
            await _stamp_mention_rows(session, recipient)


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
        # the recipient is now dangling.
        return await retract_notifications(session, user_id=recipient.id, actor_urls=[actor_url])

    # Delete may reference either the object itself (a deleted note, which is
    # a quote/reply/mention's source_url) or the activity that created it.
    assert target  # for mypy
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
        if extract_domain(obj_id) != extract_domain(actor_url):
            return 0
        return await _update_actor_notifications(session, obj=obj, recipient=recipient, actor_url=obj_id)
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
        "actor_display_name": _actor_doc_display_name(obj),
        "actor_avatar_url": _actor_doc_avatar_url(obj),
    }
    return await update_notifications_from_actor(session, actor_url, fields=fields, user_id=recipient.id)


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
    quote_target = _extract_quote_target(obj)
    resolved_reply = await _resolve_local_object(session, reply_target, instance_domain) if reply_target else None
    resolved_quote = await _resolve_local_object(session, quote_target, instance_domain) if quote_target else None

    def _target_fields(url: str, resolved: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        resolved = resolved or {}
        return {
            "target_url": url,
            "target_item_type": resolved.get("item_type"),
            "target_item_id": resolved.get("item_id"),
            "target_item_title": resolved.get("item_title"),
            "target_local_url": resolved.get("local_url"),
        }

    retract_ids: List[str] = []

    def _fields(notification: Notification) -> Optional[Dict[str, Any]]:
        if notification.type == NotificationType.MENTION:
            if not _mentions_recipient(obj, recipient.actor_url):
                retract_ids.append(str(notification.id))
                return None
            return dict(base)
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
        return {**base, **_target_fields(target, resolved)}

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
    return updated + removed
