"""
Materialize inbound remote replies and quotes into local ``Activity`` rows.

An inbound ``Create`` whose object replies to — or quotes — a known
activity is stored as a ``source_type="remote"`` ``reply``/``quote`` row
— alongside the Pubby interaction record — so the post can be listed,
counted, and interacted with (liked, boosted, replied to) exactly like a
local one. Inbound ``Update`` and ``Delete`` activities revise or retract
materialized rows, and an ``Accept`` of a ``QuoteRequest`` we sent stamps
the returned ``QuoteAuthorization`` onto our quoting post.

Publicly addressed objects are stored with the entity-clamped ``public``
visibility. Non-public ones (direct messages, followers-only) are stored
with ``mentioned`` visibility when they address at least one local user —
through ``to``/``cc``/``bto``/``bcc`` addressees or ``Mention`` tags — so
only the addressed audience can see them. Non-public posts addressing no
local user are not materialized.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from pubby import AttributionMismatch, validate_attribution
from pubby.audience import addressees, is_public, mentioned_actors
from pubby.quotes import extract_quote_target
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..config.schema import SonghiveConfig
from ..models import User, Visibility
from ..models.activity import _MENTION_HANDLE_RE, Activity, ActivityMention
from ..services import federation as federation_service
from ..services.activities import (
    _entity_visibility,
    _remote_actor_handle,
    _sync_activity_tags,
    fan_out_activity_update,
    resolve_entity,
)
from ..services.preview_cards import schedule_preview_card_fetch
from .notifications import strip_quote_fallback
from .storage import get_or_create_private_key

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


async def _resolve_object_activity(session: AsyncSession, object_uri: str) -> Optional[Activity]:
    """
    Resolve an object URI to a known activity row.

    Used both for ``inReplyTo`` parents and quote targets
    (``quote``/``quoteUrl``/``_misskey_quote``). Matches local and
    materialized-remote activities by ``source_id`` (the object id) and,
    for the ``/objects/{id}`` permalink form, by ``local_object_id``.
    """
    conditions = [Activity.source_id == object_uri]
    objects_match = re.search(r"/objects/([^/?#]+)/?$", urlparse(object_uri).path or "")
    if objects_match:
        object_id = objects_match.group(1)
        conditions += [
            Activity.local_object_id == object_id,
            Activity.source_id == object_id,
        ]
    return await session.scalar(select(Activity).where(or_(*conditions)))


async def _find_remote_object(session: AsyncSession, object_id: str, actor: str) -> Optional[Activity]:
    """Return the materialized remote object (reply or quote) for ``object_id`` authored by ``actor``."""
    return await session.scalar(
        select(Activity).where(
            Activity.source_type == "remote",
            Activity.source_id == object_id,
            Activity.source_actor == actor,
        )
    )


def _clamp_visibility(entity: Any) -> Visibility:
    """
    Return the visibility a materialized public reply or quote may carry.

    The stored visibility is clamped to the containing entity's so the row
    can never outrank its container (and local replies to it stay legal).
    """
    entity_visibility = _entity_visibility(entity)
    if Visibility.can_contain(Visibility.PUBLIC, entity_visibility):
        return Visibility.PUBLIC
    return entity_visibility


def _materialized_visibility(obj: dict, entity: Any) -> Visibility:
    """
    Return the visibility a materialized remote object should carry.

    Publicly addressed objects keep the entity-clamped ``public``
    visibility; everything else (direct messages, followers-only posts)
    degrades to ``mentioned`` — the addressed audience is already
    constrained by the row's ``ActivityMention`` entries, and entity
    access is enforced separately when its activities are listed.
    """
    if is_public(obj):
        return _clamp_visibility(entity)
    return Visibility.MENTIONED


async def _relay_thread_activity(
    session: AsyncSession,
    *,
    activity: dict,
    obj: dict,
    target: Activity,
    config: Optional[SonghiveConfig],
) -> None:
    """
    Relay a public remote reply/quote to followers of its thread objects.

    Remote actors may ``Follow`` a local object rather than an actor —
    e.g. Friendica sends ``Follow`` on a thread's root item for
    conversation subscriptions — and expect the object's server to
    forward new thread items to them. The received activity is forwarded
    verbatim, signed by the nearest local ancestor's owner (or the
    instance actor), to every inbox following an object in the reply
    chain. Non-public objects are never relayed.
    """
    if config is None or not config.federation.enabled or not config.federation.instance_domain or not is_public(obj):
        return

    object_ids: Set[str] = set()
    if target.source_id:
        object_ids.add(target.source_id)
    owner_id = target.owner_user_id if target.source_type == "local" else None
    seen: Set[str] = {target.id}
    parent_id = target.in_reply_to_activity_id

    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        row = (
            await session.execute(
                select(
                    Activity.source_id,
                    Activity.source_type,
                    Activity.owner_user_id,
                    Activity.in_reply_to_activity_id,
                ).where(Activity.id == parent_id)
            )
        ).first()
        if row is None:
            break
        if row.source_id:
            object_ids.add(row.source_id)
        if owner_id is None and row.source_type == "local" and row.owner_user_id:
            owner_id = row.owner_user_id
        parent_id = row.in_reply_to_activity_id

    signer = await session.get(User, owner_id) if owner_id else None
    inboxes = await asyncio.to_thread(
        federation_service.get_object_follower_inboxes,
        object_ids,
        config.database.url,
    )

    if not inboxes:
        return

    if signer is not None and signer.actor_url and signer.private_key_pem:
        key_id = f"{signer.actor_url}#main-key"
        private_key_pem = signer.private_key_pem
    else:
        domain = config.federation.instance_domain
        key_id = f"https://{domain}/ap/actor#main-key"
        private_key_pem = get_or_create_private_key(config.federation.private_key_path).read_text(encoding="utf-8")

    # Deferred: ``tasks.federation`` drives this module's sync entry point.
    from ..tasks.federation import deliver_activity

    for inbox in inboxes:
        try:
            deliver_activity.delay(activity, inbox, key_id, private_key_pem)
        except Exception as exc:
            logger.warning("Cannot relay %s to %s: %s", activity.get("id"), inbox, exc)
    logger.info(
        "Relayed remote %s %s to %d object follower(s)",
        activity.get("type"),
        obj.get("id"),
        len(inboxes),
    )


async def _materialize_remote_object(
    session: AsyncSession,
    *,
    activity: dict,
    obj: dict,
    actor: str,
    object_id: str,
    target: Activity,
    activity_type: str,
    config: Optional[SonghiveConfig] = None,
) -> Optional[Activity]:
    """
    Store a validated inbound ``Create`` object as a remote activity row.

    Shared by :func:`materialize_remote_reply` and
    :func:`materialize_remote_quote` after each has validated the envelope
    and resolved its target. Dedupes on ``(source_type, source_id)``,
    skips non-public posts addressing no local user, attaches the row to
    the target's entity with ``in_reply_to_activity_id`` pointing at the
    target, and syncs mention rows and hashtags.
    """
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
        # A non-public post addressing no local user has no audience here.
        return None

    entity = await resolve_entity(session, target.entity_type, target.entity_id)
    if entity is None:
        return None

    # The ``RE:`` quote fallback remote servers append for non-quote-aware
    # clients is stripped — the quoted activity renders through the
    # ``in_reply_to_activity_id`` embed instead.
    content = strip_quote_fallback(obj.get("content"), extract_quote_target(obj))
    row = Activity(
        entity_type=target.entity_type,
        entity_id=target.entity_id,
        activity_type=activity_type,
        source_type="remote",
        source_actor=actor,
        source_id=object_id,
        owner_user_id=None,
        visibility=_materialized_visibility(obj, entity).value,
        in_reply_to_activity_id=str(target.id),
        content=content if isinstance(content, str) and content else None,
        content_type="text/html" if isinstance(content, str) and content else None,
        language=_object_language(obj),
        payload=activity,
        published_at=_parse_published(obj.get("published")) or datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()

    for mention in mentions:
        session.add(ActivityMention(activity_id=row.id, **mention))
    await session.flush()
    await _sync_activity_tags(session, row, _remote_hashtags(obj))
    schedule_preview_card_fetch(row)
    logger.info(
        "Materialized remote %s %s from %s on %s",
        activity_type,
        object_id,
        actor,
        target.source_id,
    )
    try:
        await _relay_thread_activity(session, activity=activity, obj=obj, target=target, config=config)
    except Exception as exc:
        logger.warning("Failed to relay remote %s to object followers: %s", object_id, exc)
    return row


async def materialize_remote_reply(
    session: AsyncSession,
    *,
    activity: dict,
    config: Optional[SonghiveConfig] = None,
) -> Optional[Activity]:
    """
    Store an inbound ``Create`` reply as a ``source_type="remote"`` activity.

    The reply is attached to the replied-to activity's entity, linked
    through ``in_reply_to_activity_id``, and inherits the entity-clamped
    public visibility — or ``mentioned`` when the object is not publicly
    addressed. Returns ``None`` — leaving the reply to the Pubby
    interaction listing — when the object is not a reply (objects carrying
    quote fields are handled by :func:`materialize_remote_quote`), the
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
        or extract_quote_target(obj)
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

    parent = await _resolve_object_activity(session, in_reply_to)
    if parent is None:
        return None
    return await _materialize_remote_object(
        session,
        activity=activity,
        obj=obj,
        actor=actor,
        object_id=object_id,
        target=parent,
        activity_type="reply",
        config=config,
    )


async def materialize_remote_quote(
    session: AsyncSession,
    *,
    activity: dict,
    config: Optional[SonghiveConfig] = None,
) -> Optional[Activity]:
    """
    Store an inbound ``Create`` quote as a ``source_type="remote"`` activity.

    The quoted object is resolved from the FEP-0449 ``quote`` field,
    Mastodon's ``quoteUrl``, or Misskey's ``_misskey_quote`` — quotes need
    not carry ``inReplyTo``. The row attaches to the quoted activity's
    entity and links to it through ``in_reply_to_activity_id``, mirroring
    local quotes, so it is listed and counted like one.

    Returns ``None`` — leaving the quote to the Pubby interaction listing
    — when the object carries no quote field, the signature-side
    attribution checks fail, the quoted URI resolves to no known activity,
    or a non-public quote addresses no local user. Re-delivery is
    idempotent on ``(source_type, source_id)``.
    """
    if activity.get("type") != "Create":
        return None
    obj = activity.get("object")
    if not isinstance(obj, dict):
        return None

    actor = activity.get("actor")
    object_id = obj.get("id")
    quoted = extract_quote_target(obj)
    if (
        not isinstance(actor, str)
        or not actor.startswith(("http://", "https://"))
        or not isinstance(object_id, str)
        or not object_id.startswith(("http://", "https://"))
        or not quoted
    ):
        return None

    try:
        validate_attribution(actor, obj)
    except AttributionMismatch:
        return None

    target = await _resolve_object_activity(session, quoted)
    if target is None:
        return None
    return await _materialize_remote_object(
        session,
        activity=activity,
        obj=obj,
        actor=actor,
        object_id=object_id,
        target=target,
        activity_type="quote",
        config=config,
    )


async def update_remote_object(
    session: AsyncSession,
    *,
    activity: dict,
    config: Optional[SonghiveConfig] = None,
) -> None:
    """
    Apply an inbound ``Update`` to a materialized remote reply or quote.

    Content, language, payload, published timestamp, mention rows and
    hashtags are refreshed from the updated object, and the stored
    visibility is recomputed from the object's addressing — an edit that
    drops the public audience degrades the row to ``mentioned`` rather
    than retracting it. Updates for objects that were never materialized
    fall back to materialization when they carry a quote field or an
    ``inReplyTo`` — covering a missed ``Create``.
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

    row = await _find_remote_object(session, object_id, actor)
    if row is None:
        # A missed ``Create`` is replayed through the matching
        # materializer — quote fields take precedence over ``inReplyTo``,
        # mirroring Pubby's interaction typing.
        if extract_quote_target(obj):
            await materialize_remote_quote(session, activity={**activity, "type": "Create"}, config=config)
        elif isinstance(obj.get("inReplyTo"), str) and obj["inReplyTo"]:
            await materialize_remote_reply(session, activity={**activity, "type": "Create"}, config=config)
        return

    entity = await resolve_entity(session, row.entity_type, row.entity_id)
    if entity is not None:
        row.visibility = _materialized_visibility(obj, entity).value

    content = strip_quote_fallback(obj.get("content"), extract_quote_target(obj))
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
    # The content may link a different first URL after the edit — force a
    # pipeline re-run so a stale card is refreshed or cleared.
    schedule_preview_card_fetch(row, force=True)


async def retract_remote_object(session: AsyncSession, *, activity: dict) -> None:
    """
    Apply an inbound ``Delete`` to a materialized remote reply or quote.

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

    row = await _find_remote_object(session, target, actor)
    if row is not None and row.deleted_at is None:
        row.deleted_at = datetime.now(timezone.utc)
        await session.flush()
        logger.info("Retracted remote %s %s from %s", row.activity_type, target, actor)


async def apply_quote_authorization(
    session: AsyncSession,
    *,
    activity: dict,
    config: Optional[SonghiveConfig] = None,
) -> None:
    """
    Apply an inbound ``Accept`` of a ``QuoteRequest`` we sent (FEP-044f).

    When a remote server approves our quote it replies with an ``Accept``
    echoing the ``QuoteRequest`` and carrying the issued
    ``QuoteAuthorization``'s dereferenceable id in ``result``. The local
    ``quote`` row's stored ``Create`` payload is stamped with that id so
    the served object document — and remote copies refreshed through a
    fanned-out ``Update`` — advertise the verified quote.

    The ``Accept`` applies only when it names the same quoting and quoted
    objects the row recorded and its actor is the quoted post's author —
    anyone else cannot legitimately approve the quote.
    """
    if activity.get("type") != "Accept":
        return
    request = activity.get("object")
    if not isinstance(request, dict) or request.get("type") != "QuoteRequest":
        return
    actor = activity.get("actor")
    instrument = request.get("instrument")
    quoting_id = instrument.get("id") if isinstance(instrument, dict) else instrument
    quoted_id = request.get("object")
    if isinstance(quoted_id, dict):
        quoted_id = quoted_id.get("id")
    result = activity.get("result")
    auth_id = result.get("id") if isinstance(result, dict) else result
    inst_actor = instrument.get("attributedTo") if isinstance(instrument, dict) else None
    if isinstance(inst_actor, dict):
        inst_actor = inst_actor.get("id")
    if not (
        isinstance(actor, str)
        and actor
        and isinstance(quoting_id, str)
        and quoting_id
        and isinstance(quoted_id, str)
        and quoted_id
        and isinstance(auth_id, str)
        and auth_id
    ):
        return

    row = await session.scalar(
        select(Activity).where(
            Activity.source_type == "local",
            Activity.activity_type == "quote",
            Activity.source_id == quoting_id,
        )
    )
    if row is None or row.deleted_at is not None:
        return

    target = (
        await session.scalar(select(Activity).where(Activity.id == row.in_reply_to_activity_id))
        if row.in_reply_to_activity_id
        else None
    )
    if (
        target is None
        or target.source_id != quoted_id
        or target.source_actor != actor
        or (inst_actor is not None and inst_actor != row.source_actor)
    ):
        return

    obj = row.payload.get("object") if isinstance(row.payload, dict) else None
    if not isinstance(obj, dict) or obj.get("quoteAuthorization") == auth_id:
        return
    obj["quoteAuthorization"] = auth_id
    flag_modified(row, "payload")
    await session.flush()
    logger.info("Stamped quote authorization %s on quote %s", auth_id, quoting_id)

    if config is not None:
        try:
            await fan_out_activity_update(session, row, config)
        except Exception as exc:
            logger.warning("Failed to fan out authorized quote %s: %s", quoting_id, exc)


async def sync_remote_activity(
    session: AsyncSession,
    *,
    activity: dict,
    config: Optional[SonghiveConfig] = None,
) -> None:
    """
    Reflect an inbound remote activity onto materialized ``Activity`` rows.

    ``Create`` materializes replies to — and quotes of — known activities;
    ``Update`` revises and ``Delete`` retracts materialized rows; an
    ``Accept`` answering a ``QuoteRequest`` we sent stamps the issued
    ``QuoteAuthorization`` onto the quoting post. Newly materialized
    public replies and quotes are relayed to remote actors following an
    object in the thread — object-scoped follows (e.g. Friendica thread
    subscriptions) — signed by the nearest local ancestor's owner. Other
    activity types are ignored — likes and boosts stay interaction-only.
    """
    activity_type = activity.get("type")
    if activity_type == "Create":
        obj = activity.get("object")
        # Quote fields take precedence over ``inReplyTo`` — mirroring
        # Pubby, which records a note carrying both as a QUOTE interaction.
        if isinstance(obj, dict) and extract_quote_target(obj):
            await materialize_remote_quote(session, activity=activity, config=config)
        else:
            await materialize_remote_reply(session, activity=activity, config=config)
    elif activity_type == "Update":
        await update_remote_object(session, activity=activity, config=config)
    elif activity_type == "Delete":
        await retract_remote_object(session, activity=activity)
    elif activity_type == "Accept":
        await apply_quote_authorization(session, activity=activity, config=config)
