"""
Materialize inbound remote replies into local ``Activity`` rows.

An inbound ``Create`` whose object replies to a known activity is stored
as a ``source_type="remote"`` ``reply`` row — alongside the Pubby
interaction record — so the reply can be listed, counted, and interacted
with (liked, boosted, replied to) exactly like a local reply. Inbound
``Update`` and ``Delete`` activities revise or retract materialized rows.

Publicly addressed replies are stored with the entity-clamped ``public``
visibility. Non-public replies (direct messages, followers-only) are
stored with ``mentioned`` visibility when they address at least one
local user — through ``to``/``cc``/``bto``/``bcc`` addressees or
``Mention`` tags — so only the addressed audience can see them in the
thread. Non-public replies addressing no local user are not materialized.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from pubby import AttributionMismatch, validate_attribution
from pubby.audience import addressees, is_public, mentioned_actors
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User, Visibility
from ..models.activity import _MENTION_HANDLE_RE, Activity, ActivityMention
from ..services.activities import (
    _entity_visibility,
    _remote_actor_handle,
    _sync_activity_tags,
    resolve_entity,
)

logger = logging.getLogger(__name__)


def _parse_published(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into an aware datetime, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _object_language(obj: dict) -> Optional[str]:
    """Return the language declared by the object's ``contentMap``, if any."""
    content_map = obj.get("contentMap")
    if isinstance(content_map, dict) and content_map:
        first = next(iter(content_map))
        if isinstance(first, str):
            return first
    return None


def _remote_hashtags(obj: dict) -> List[str]:
    """Return hashtag names declared in the object's ``tag`` list."""
    tags = obj.get("tag")
    if not isinstance(tags, list):
        return []
    return [
        name.lstrip("#")
        for tag in tags
        if isinstance(tag, dict) and tag.get("type") == "Hashtag" and isinstance((name := tag.get("name")), str)
    ]


async def _local_addressee_users(session: AsyncSession, obj: dict) -> Dict[str, User]:
    """
    Resolve the object's local audience to ``User`` rows.

    Covers ``to``/``cc``/``bto``/``bcc`` addressees and ``Mention`` tag
    ``href``s matching a local ``User.actor_url`` — a direct reply may
    address the recipient without carrying an explicit ``Mention`` tag.
    Returns a map keyed by actor URL.
    """
    urls = addressees(obj) | set(mentioned_actors(obj))
    if not urls:
        return {}
    rows = await session.execute(select(User).where(User.actor_url.in_(urls)))
    return {user.actor_url: user for user in rows.scalars().all() if user.actor_url}


async def _remote_mentions(session: AsyncSession, obj: dict) -> List[Dict[str, Any]]:
    """
    Build ``ActivityMention`` field dicts for the object's audience.

    Combines ``tag`` Mentions — whose ``href`` values matching a local
    user's ``actor_url`` are linked to that user (``user_id``), the rest
    staying remote-only — with local users found in the object's
    addressees, so a non-public reply stays viewable to the local users it
    addresses even without an explicit ``Mention`` tag. Entries whose
    handle cannot be derived or validated are dropped.
    """
    mentions = await _tag_mentions(session, obj)
    covered = {m["actor_url"] for m in mentions}
    for actor_url, user in (await _local_addressee_users(session, obj)).items():
        if actor_url not in covered:
            mentions.append({"handle": f"@{user.username}", "actor_url": actor_url, "user_id": user.id})
    return mentions


async def _tag_mentions(session: AsyncSession, obj: dict) -> List[Dict[str, Any]]:
    """Build ``ActivityMention`` field dicts from the object's ``tag`` Mentions."""
    tags = obj.get("tag")
    if not isinstance(tags, list):
        return []
    hrefs = mentioned_actors(obj)
    if not hrefs:
        return []

    rows = await session.execute(select(User.id, User.actor_url).where(User.actor_url.in_(hrefs)))
    local_users = {actor_url: user_id for user_id, actor_url in rows.all() if actor_url}

    mentions: List[Dict[str, Any]] = []
    seen: set = set()
    for tag in tags:
        if not isinstance(tag, dict) or tag.get("type") != "Mention":
            continue
        href = tag.get("href")
        if not isinstance(href, str) or not href or href in seen:
            continue
        handle = tag.get("name")
        if not isinstance(handle, str) or not _MENTION_HANDLE_RE.match(handle):
            handle = _remote_actor_handle(href)
            if not _MENTION_HANDLE_RE.match(handle):
                continue
        seen.add(href)
        mentions.append(
            {
                "handle": handle,
                "actor_url": href,
                "user_id": local_users.get(href),
            }
        )
    return mentions


async def _resolve_reply_parent(session: AsyncSession, in_reply_to: str) -> Optional[Activity]:
    """
    Resolve an ``inReplyTo`` URI to a known activity row.

    Matches local and materialized-remote activities by ``source_id`` (the
    object id) and, for the ``/objects/{id}`` permalink form, by
    ``local_object_id``.
    """
    conditions = [Activity.source_id == in_reply_to]
    objects_match = re.search(r"/objects/([^/?#]+)/?$", urlparse(in_reply_to).path or "")
    if objects_match:
        object_id = objects_match.group(1)
        conditions += [
            Activity.local_object_id == object_id,
            Activity.source_id == object_id,
        ]
    return await session.scalar(select(Activity).where(or_(*conditions)))


async def _find_remote_reply(session: AsyncSession, object_id: str, actor: str) -> Optional[Activity]:
    """Return the materialized remote reply for ``object_id`` authored by ``actor``."""
    return await session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == object_id,
            Activity.source_actor == actor,
        )
    )


def _clamp_visibility(entity: Any) -> Visibility:
    """
    Return the visibility a materialized public reply may carry.

    The stored visibility is clamped to the containing entity's so the row
    can never outrank its container (and local replies to it stay legal).
    """
    entity_visibility = _entity_visibility(entity)
    if Visibility.can_contain(Visibility.PUBLIC, entity_visibility):
        return Visibility.PUBLIC
    return entity_visibility


def _reply_visibility(obj: dict, entity: Any) -> Visibility:
    """
    Return the visibility a materialized reply should carry.

    Publicly addressed objects keep the entity-clamped ``public``
    visibility; everything else (direct messages, followers-only replies)
    degrades to ``mentioned`` — the addressed audience is already
    constrained by the row's ``ActivityMention`` entries, and entity
    access is enforced separately when its activities are listed.
    """
    if is_public(obj):
        return _clamp_visibility(entity)
    return Visibility.MENTIONED


async def materialize_remote_reply(session: AsyncSession, *, activity: dict) -> Optional[Activity]:
    """
    Store an inbound ``Create`` reply as a ``source_type="remote"`` activity.

    The reply is attached to the replied-to activity's entity, linked
    through ``in_reply_to_activity_id``, and inherits the entity-clamped
    public visibility — or ``mentioned`` when the object is not publicly
    addressed. Returns ``None`` — leaving the reply to the Pubby
    interaction listing — when the object is not a reply, the
    signature-side attribution checks fail, the parent cannot be resolved
    to a known activity, or a non-public reply addresses no local user.
    Re-delivery is idempotent on ``(source_type, source_id)``.
    """
    if activity.get("type") != "Create":
        return None
    obj = activity.get("object")
    if not isinstance(obj, dict):
        return None

    actor = activity.get("actor")
    object_id = obj.get("id")
    in_reply_to = obj.get("inReplyTo")
    if (
        not isinstance(actor, str)
        or not actor.startswith(("http://", "https://"))
        or not isinstance(object_id, str)
        or not object_id.startswith(("http://", "https://"))
        or not isinstance(in_reply_to, str)
        or not in_reply_to
    ):
        return None

    # Attribution sanity is owned by pubby (``strict_attribution`` on the
    # inbox processor) — but this sync runs on the raw activity even when
    # the processor dropped it, so the same guard is applied here via the
    # pubby validator.
    try:
        validate_attribution(actor, obj)
    except AttributionMismatch:
        return None

    existing = await session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == object_id,
        )
    )
    if existing is not None:
        return existing

    public = is_public(obj)
    mentions = await _remote_mentions(session, obj)
    if not public and not any(m["user_id"] for m in mentions):
        # A non-public reply addressing no local user has no audience here.
        return None

    parent = await _resolve_reply_parent(session, in_reply_to)
    if parent is None:
        return None
    entity = await resolve_entity(session, parent.entity_type, parent.entity_id)
    if entity is None:
        return None

    content = obj.get("content")
    reply = Activity(
        entity_type=parent.entity_type,
        entity_id=parent.entity_id,
        activity_type="reply",
        source_type="remote",
        source_actor=actor,
        source_id=object_id,
        owner_user_id=None,
        visibility=_reply_visibility(obj, entity).value,
        in_reply_to_activity_id=str(parent.id),
        content=content if isinstance(content, str) and content else None,
        content_type="text/html" if isinstance(content, str) and content else None,
        language=_object_language(obj),
        payload=activity,
        published_at=_parse_published(obj.get("published")) or datetime.now(timezone.utc),
    )
    session.add(reply)
    await session.flush()

    for mention in mentions:
        session.add(ActivityMention(activity_id=reply.id, **mention))
    await session.flush()
    await _sync_activity_tags(session, reply, _remote_hashtags(obj))
    logger.info("Materialized remote reply %s from %s on %s", object_id, actor, in_reply_to)
    return reply


async def update_remote_reply(session: AsyncSession, *, activity: dict) -> None:
    """
    Apply an inbound ``Update`` to a materialized remote reply.

    Content, language, payload, published timestamp, mention rows and
    hashtags are refreshed from the updated object, and the stored
    visibility is recomputed from the object's addressing — an edit that
    drops the public audience degrades the row to ``mentioned`` rather
    than retracting it. Updates for objects that were never materialized
    fall back to materialization when they carry an ``inReplyTo`` —
    covering a missed ``Create``.
    """
    if activity.get("type") != "Update":
        return
    obj = activity.get("object")
    actor = activity.get("actor")
    if not isinstance(obj, dict) or not isinstance(actor, str):
        return
    object_id = obj.get("id")
    if not isinstance(object_id, str) or not object_id:
        return

    try:
        validate_attribution(actor, obj)
    except AttributionMismatch:
        return

    row = await _find_remote_reply(session, object_id, actor)
    if row is None:
        if isinstance(obj.get("inReplyTo"), str) and obj["inReplyTo"]:
            await materialize_remote_reply(session, activity={**activity, "type": "Create"})
        return

    entity = await resolve_entity(session, row.entity_type, row.entity_id)
    if entity is not None:
        row.visibility = _reply_visibility(obj, entity).value

    content = obj.get("content")
    row.content = content if isinstance(content, str) and content else None
    row.language = _object_language(obj)
    row.payload = activity
    published = _parse_published(obj.get("published"))
    if published is not None:
        row.published_at = published

    await session.execute(delete(ActivityMention).where(ActivityMention.activity_id == row.id))
    for mention in await _remote_mentions(session, obj):
        session.add(ActivityMention(activity_id=row.id, **mention))
    await session.flush()
    await _sync_activity_tags(session, row, _remote_hashtags(obj))


async def retract_remote_reply(session: AsyncSession, *, activity: dict) -> None:
    """
    Apply an inbound ``Delete`` to a materialized remote reply.

    The row is soft-deleted — like local retractions — so thread traversal
    still crosses it and the stale Pubby interaction stays deduplicated.
    Only the recorded ``source_actor`` may retract its own object.
    """
    if activity.get("type") != "Delete":
        return
    obj = activity.get("object")
    actor = activity.get("actor")
    target = obj.get("id") if isinstance(obj, dict) else obj if isinstance(obj, str) else None
    if not isinstance(target, str) or not target or not isinstance(actor, str):
        return

    row = await _find_remote_reply(session, target, actor)
    if row is not None and row.deleted_at is None:
        row.deleted_at = datetime.now(timezone.utc)
        await session.flush()
        logger.info("Retracted remote reply %s from %s", target, actor)


async def sync_remote_activity(session: AsyncSession, *, activity: dict) -> None:
    """
    Reflect an inbound remote activity onto materialized ``Activity`` rows.

    ``Create`` materializes replies to known activities; ``Update``
    revises and ``Delete`` retracts materialized rows. Other activity
    types are ignored — likes, boosts and quotes stay interaction-only.
    """
    activity_type = activity.get("type")
    if activity_type == "Create":
        await materialize_remote_reply(session, activity=activity)
    elif activity_type == "Update":
        await update_remote_reply(session, activity=activity)
    elif activity_type == "Delete":
        await retract_remote_reply(session, activity=activity)
