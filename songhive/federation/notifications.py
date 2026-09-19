"""
Notification hooks for incoming ActivityPub activities.

Maps federated interactions to user notifications:

- ``Follow`` → ``follow``; an object-scoped Follow (FEP-efda thread
  subscription) notifies only the object's owner and carries
  ``target_*`` payload fields describing the followed object. Actor
  follows held for manual approval carry ``follow_request_pending`` so
  clients can render accept/reject controls; rejected follows create no
  notification at all
- ``Like`` → ``like``; ``Announce`` → ``boost`` — only when the target
  resolves to a local object owned by the recipient (a like/boost
  delivered to a follower inbox does not concern them)
- ``Create`` (Note): ``quote`` when the note quotes a local object URL,
  otherwise ``reply`` when it has ``inReplyTo`` (a note that is both emits
  only ``quote``), plus ``mention`` whenever the note's ``tag`` entries
  mention the recipient's actor URL. Creating a ``mention`` notification also
  stamps ``ActivityMention.notified_at`` on the recipient's pending mention
  rows.
- ``QuoteRequest`` (FEP-044f): ``quote`` when the requested object resolves
  to a local one — pubby auto-approves the request, so the notification is
  what tells the quoted post's owner the quote happened even when the
  quoting note's own ``Create`` never reaches this inbox.

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

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from pubby.audience import addressees, is_public, mentioned_actors
from pubby.moderation import extract_domain
from pubby.quotes import extract_quote_target
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Visibility
from ..models.activity import Activity, ActivityMention
from ..models.mention_record import MentionSource
from ..models.notification import Notification, NotificationType
from ..models.track import Track
from ..models.user import FollowersApproval, User
from ..services import acl
from ..services import mention_records as mention_records_service
from ..services.activities import (
    _activity_object_type,
    _actor_doc_avatar_url,
    _actor_doc_display_name,
    resolve_entity,
)
from ..services.notifications import (
    OBJECT_SNAPSHOT_KEYS,
    _push_updated,
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


def strip_quote_fallback(content: Any, quote_target: Optional[str] = None) -> Any:
    """
    Remove the ``RE: <link>`` quote fallback remote servers embed.

    Mastodon (and compatible servers) add a ``quote-inline`` element to a
    quote note's ``content`` so clients that cannot render quotes still
    show a link to the quoted object — Mastodon prepends a
    ``<p class="quote-inline">`` paragraph, Akkoma appends a
    ``<span class="quote-inline">``, and Misskey/Threads append a bare
    ``RE: <url>`` tail. Songhive renders the quoted activity itself, so
    the fallback only produces a dangling ``RE:`` link — which can point
    at an object URL that does not dereference to a browser page.

    Elements carrying a ``quote-inline`` class are always stripped; the
    bare ``RE:`` tail is stripped only when it links to ``quote_target``,
    so legitimate "RE:" text a user wrote is never touched. Other content
    is returned untouched.
    """
    if not isinstance(content, str):
        return content
    stripped = _QUOTE_INLINE_RE.sub("", content) if "quote-inline" in content else content
    if quote_target:
        tail = re.compile(
            rf"\s*RE:\s*(?:<a\b[^>]*?href=[\"']?{re.escape(quote_target)}[\"']?[^>]*>.*?</a>"
            rf"|{re.escape(quote_target)})\s*$",
            re.IGNORECASE | re.DOTALL,
        )
        stripped = tail.sub("", stripped)
    if stripped is content:
        return content
    return _EMPTY_PARA_RE.sub("", stripped).strip()


# ``<span|p|div class="…quote-inline…">…</tag>`` — the wrapper Mastodon
# and Akkoma emit for their plain-text ``RE:`` quote fallback.
_QUOTE_INLINE_RE = re.compile(
    r"<(span|p|div)\b[^>]*\bclass=\"[^\"]*quote-inline[^\"]*\"[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Paragraphs emptied by the strip (``<p></p>``, ``<p><br/></p>``, ``<p> </p>``).
_EMPTY_PARA_RE = re.compile(r"<p>(?:\s|<br\s*/?>)*</p>", re.IGNORECASE)


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
    content = strip_quote_fallback(obj.get("content"), extract_quote_target(obj))
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

    A ``source_id`` match resolves any stored activity — local
    ``{actor_url}/objects/{id}`` permalinks and materialized remote
    objects (e.g. replies stored under the remote object's own id)
    regardless of the URL's path form. Otherwise the
    ``{actor_url}/objects/{id}`` form is matched against
    ``Track.federation_object_id`` and ``Activity.local_object_id``/
    ``source_id``, and ``/{plural}/{id}`` page URLs on the instance domain
    resolve to their entity. Returns ``{item_type, item_id, item_title,
    local_url}`` so the client can render the referenced track or entity
    with its own link; ``None`` when the object is not local.

    Objects that resolve to an ``Activity`` additionally carry
    ``object_activity_id``/``object_type``/``object_page_url`` so clients
    can render the activity card itself and link to its
    ``/activities/{id}`` page; ``user`` entities (standalone statuses)
    have no item page, so ``item_type``/``item_id`` are omitted and
    ``local_url`` is the author's profile.
    """
    if not isinstance(url, str) or not url:
        return None

    parsed = urlparse(url)
    path = parsed.path or ""

    item = None
    item_type: Optional[str] = None
    # A stored ``source_id`` covers every activity permalink form —
    # including remote object ids materialized into local reply rows,
    # whose path shape is the remote server's own.
    activity = await session.scalar(select(Activity).where(Activity.source_id == url).limit(1))
    objects_match = re.search(r"/objects/([^/?#]+)/?$", path)
    if activity is None and objects_match:
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
                    )
                )
            )
    elif activity is None and instance_domain and parsed.netloc == instance_domain:
        parts = [p for p in path.split("/") if p]
        candidate_type = _PLURAL_TO_ITEM_TYPE.get(parts[0]) if len(parts) == 2 else None
        if candidate_type is not None:
            item_type = candidate_type
            item = await acl.get_item(session, candidate_type, parts[1])

    if activity is not None:
        item = await resolve_entity(session, activity.entity_type, activity.entity_id)
        item_type = activity.entity_type

    if item is None or item_type is None:
        return None
    if item_type == "user":
        resolved: Dict[str, Any] = {
            "item_title": getattr(item, "display_name", None) or getattr(item, "username", None),
            "local_url": f"/@{getattr(item, 'username', item.id)}",
        }
    else:
        plural = acl.get_item_plural(item_type) or item_type
        resolved = {
            "item_type": item_type,
            "item_id": str(item.id),
            "item_title": getattr(item, "title", None) or getattr(item, "name", None),
            "local_url": f"/{plural}/{item.id}",
        }
    if activity is not None:
        resolved["object_activity_id"] = str(activity.id)
        resolved["object_page_url"] = f"/activities/{activity.id}"
        object_type = _activity_object_type(activity)
        if object_type:
            resolved["object_type"] = object_type
    return resolved


def _mentions_recipient(obj: dict, actor_url: Optional[str]) -> bool:
    """Return True when the object's Mention tags address ``actor_url``."""
    return bool(actor_url) and actor_url in mentioned_actors(obj)


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


def _mention_visibility(obj: dict, actor_url: str) -> str:
    """Classify a remote object's audience for the mention archive."""
    if is_public(obj):
        return Visibility.PUBLIC.value
    if f"{actor_url}/followers" in addressees(obj):
        return Visibility.FOLLOWERS.value
    return Visibility.MENTIONED.value


async def _record_inbox_mention(
    session: AsyncSession,
    *,
    recipient: User,
    actor_url: str,
    source_url: str,
    obj: dict,
    payload: Dict[str, Any],
    self_fields: Optional[Dict[str, Any]] = None,
    merge_payload: bool = False,
) -> None:
    """Upsert the recipient's permanent mention record for a remote note.

    Runs even when the mention notification was suppressed (a reply/quote
    notification already covered the note) or the recipient's delivery
    preferences drop in-app rows — the ``/mentions`` archive is a record
    of every object that addressed the user, not of what was delivered.
    ``self_fields`` carries ``object_activity_id`` when the note was
    materialized into a local ``Activity`` row. With ``merge_payload``
    the given fields merge into the stored snapshot — the path an
    ``Update`` takes so actor/activity fields recorded at ``Create`` are
    preserved.
    """
    fields = self_fields or {}
    await mention_records_service.upsert_mention(
        session,
        user_id=str(recipient.id),
        source=MentionSource.ACTIVITYPUB,
        source_url=source_url,
        actor_url=actor_url,
        activity_id=fields.get("object_activity_id"),
        visibility=_mention_visibility(obj, actor_url),
        payload=payload,
        merge_payload=merge_payload,
    )


async def _create_or_update_quote_notification(
    session: AsyncSession,
    *,
    recipient: User,
    actor_url: str,
    source_url: Optional[str],
    payload: Dict[str, Any],
) -> Optional[Notification]:
    """
    Create the ``quote`` notification for a quote note, refreshing any
    existing row instead of duplicating it.

    A quote can reach the inbox twice — once as a FEP-044f
    ``QuoteRequest`` and again as the quoting note's ``Create`` — and the
    first row may already be seen when the second arrives, so the generic
    unseen-only dedup in ``create_notification`` cannot cover it. Any
    existing row for the same quoting object (seen or not) is enriched
    with the newer payload — a ``Create`` snapshot fills the gaps a bare
    ``instrument`` id left — and returned; its seen state is preserved.
    """
    if source_url:
        existing = await session.scalar(
            select(Notification)
            .where(
                Notification.user_id == recipient.id,
                Notification.type == NotificationType.QUOTE,
                Notification.actor_url == actor_url,
                Notification.source_url == source_url,
            )
            .limit(1)
        )
        if existing is not None:
            merged = {**(existing.payload or {}), **payload}
            if merged != (existing.payload or {}):
                existing.payload = merged
                await session.flush()
                _push_updated({str(recipient.id): [existing]})
            return existing
    return await create_notification(
        session,
        user_id=recipient.id,
        type=NotificationType.QUOTE,
        actor_url=actor_url,
        source_url=source_url,
        payload=payload,
    )


def _activity_audience_urls(activity: dict) -> set:
    """
    Collect the URLs an inbound activity addresses or targets.

    Covers ``to``/``cc``/``bto``/``bcc`` on the activity and its object,
    ``Mention`` tag ``href``s, a string ``object`` (Follow/Like/Announce
    target), the object's ``inReplyTo``/quote targets, and an ``Undo``'s
    inner object. Only HTTP(S) URLs are returned.
    """
    candidates = set()

    def _add(container: Any) -> None:
        candidates.update(addressees(container))

    _add(activity)
    obj = activity.get("object")
    if isinstance(obj, str):
        candidates.add(obj)
    elif isinstance(obj, dict):
        _add(obj)
        candidates.update(mentioned_actors(obj))
        for key in ("inReplyTo", "quote", "quoteUri", "quoteUrl", "_misskey_quote"):
            target = obj.get(key)
            if isinstance(target, str) and target:
                candidates.add(target)
        inner = obj.get("object")
        if isinstance(inner, str):
            candidates.add(inner)
        elif isinstance(inner, dict):
            inner_id = inner.get("id")
            if isinstance(inner_id, str) and inner_id:
                candidates.add(inner_id)
    return {url for url in candidates if isinstance(url, str) and url.startswith(("http://", "https://"))}


async def _target_owner_users(
    session: AsyncSession,
    urls: set,
    instance_domain: Optional[str] = None,
) -> List[User]:
    """
    Resolve object URLs to the local users owning them.

    Activity targets are matched by ``source_id`` (or ``local_object_id``
    for the ``/objects/{id}`` permalink form); track ``Audio`` objects are
    matched by ``federation_object_id``; ``/{plural}/{id}`` page URLs on
    the instance domain resolve to their entity's ``owner_id``. Each
    distinct owner is returned once.
    """
    users: List[User] = []
    seen = set()
    for url in urls:
        conditions = [Activity.source_id == url]
        parsed = urlparse(url)
        match = re.search(r"/objects/([^/?#]+)/?$", parsed.path or "")
        if match:
            object_id = match.group(1)
            conditions += [Activity.local_object_id == object_id, Activity.source_id == object_id]
        owner_id = await session.scalar(select(Activity.owner_user_id).where(or_(*conditions)).limit(1))
        if owner_id is None and match:
            track = await session.scalar(
                select(Track.owner_id).where(Track.federation_object_id == match.group(1)).limit(1)
            )
            owner_id = track
        if owner_id is None and instance_domain and parsed.netloc == instance_domain:
            parts = [p for p in parsed.path.split("/") if p]
            item_type = _PLURAL_TO_ITEM_TYPE.get(parts[0]) if len(parts) == 2 else None
            if item_type is not None:
                item = await acl.get_item(session, item_type, parts[1])
                owner_id = getattr(item, "owner_id", None) if item is not None else None
        if owner_id and str(owner_id) not in seen:
            user = await session.get(User, owner_id)
            if user is not None:
                seen.add(str(owner_id))
                users.append(user)
    return users


def _object_is_public(obj: Any) -> bool:
    """Return whether an embedded object addresses the public collection."""
    if not isinstance(obj, dict):
        return True
    return is_public(obj)


async def resolve_inbox_recipients(
    session: AsyncSession,
    *,
    activity: dict,
    instance_domain: Optional[str] = None,
) -> List[User]:
    """
    Return the local users an inbound activity addresses or targets.

    Used for shared-inbox deliveries (``/ap/inbox``), where no single
    recipient username is known: recipients are the local users found in
    the activity's audience fields — ``to``/``cc`` addressees, ``Mention``
    tag targets, a string ``object`` such as a Follow target — plus the
    local owners of the objects it targets (``inReplyTo``, quote, or
    Like/Announce object). Owner-via-target resolution is skipped for
    non-public ``Create`` objects so a restricted reply cannot notify a
    user outside its audience.
    """
    candidates = _activity_audience_urls(activity)
    if not candidates:
        return []
    rows = await session.execute(select(User).where(User.actor_url.in_(candidates)))
    recipients = list(rows.scalars().all())
    seen = {str(user.id) for user in recipients}
    obj = activity.get("object")
    resolve_target_owners = activity.get("type") != "Create" or _object_is_public(obj)
    if resolve_target_owners:
        for user in await _target_owner_users(session, candidates, instance_domain=instance_domain):
            if str(user.id) not in seen:
                seen.add(str(user.id))
                recipients.append(user)

    # Suspended local users receive no inbound activity.
    from ..services import moderation as moderation_service

    recipients = [user for user in recipients if not await moderation_service.user_is_suspended(session, user.id)]
    return recipients


async def _create_follow_inbox_notification(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    payload: Dict[str, Any],
    obj: Optional[Any] = None,
    instance_domain: Optional[str] = None,
    storage: Optional[Any] = None,
) -> None:
    """
    Create notifications for an incoming ``Follow`` activity.

    The Follow object is normally the followed actor (the recipient);
    object-scoped Follows (FEP-efda thread subscriptions, e.g. Friendica)
    target a local object instead. Object follows carry the resolved
    ``target_*`` fields so clients can render "followed your post", and only
    the object's owner is notified — a personal-inbox delivery to someone else
    is misaddressed.

    When the recipient's approval policy held the follow as a pending
    request, the payload carries ``follow_request_pending`` so clients can
    render accept/reject controls. Rejected follows produce no
    notification.
    """
    target = obj if isinstance(obj, str) else obj.get("id") if isinstance(obj, dict) else None
    actor_follow = not (isinstance(target, str) and target) or target == recipient.actor_url
    if actor_follow:
        if recipient.followers_approval == FollowersApproval.REJECT.value:
            return
        if storage is not None:
            request = await asyncio.to_thread(storage.get_follow_request, actor_url, recipient.actor_url or "")
            if request is not None:
                payload = {**payload, "follow_request_pending": True}
    elif isinstance(target, str) and target:
        resolved = await _resolve_local_object(session, target, instance_domain)
        if resolved is not None:
            owners = await _target_owner_users(session, {target}, instance_domain=instance_domain)
            if not any(str(owner.id) == str(recipient.id) for owner in owners):
                return
            payload = {
                **payload,
                "target_url": target,
                **{f"target_{key}": value for key, value in resolved.items()},
            }

    await create_notification(
        session,
        user_id=recipient.id,
        type=NotificationType.FOLLOW,
        actor_url=actor_url,
        source_url=actor_url,
        payload=payload,
    )


async def resolve_follow_request_notification(
    session: AsyncSession,
    *,
    recipient: User,
    actor_url: str,
    status: str,
) -> Optional[Notification]:
    """
    Mark a pending follow-request notification as resolved.

    The requester's ``follow`` notification carrying
    ``follow_request_pending`` is rewritten with
    ``follow_request_status`` set to ``accepted`` or ``rejected`` so
    clients can drop the action buttons, and the change is pushed to live
    clients. Returns the updated row, ``None`` when none is pending.
    """
    rows = await session.execute(
        select(Notification).where(
            Notification.user_id == recipient.id,
            Notification.type == NotificationType.FOLLOW,
            Notification.actor_url == actor_url,
        )
    )
    for notification in rows.scalars():
        payload = dict(notification.payload or {})
        if not payload.pop("follow_request_pending", None):
            continue
        payload["follow_request_status"] = status
        notification.payload = payload
        await session.flush()
        _push_updated({str(recipient.id): [notification]})
        return notification
    return None


async def _create_quote_inbox_notification(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    activity: Dict[str, Any],
    payload: Dict[str, Any],
    activity_id: Optional[str] = None,
    obj: Optional[Any] = None,
    instance_domain: Optional[str] = None,
) -> None:
    """
    FEP-044f: ``object`` is the quoted post, ``instrument`` the
    quoting post (a bare id, or the embedded object when the remote
    server includes it). Pubby auto-approves the request; this
    notification is what tells the quoted post's owner it happened —
    the quote's own ``Create`` may never reach this inbox. The
    instrument id is used as ``source_url`` so a later ``Create``
    notification for the same note dedupes onto this row while it
    stays unseen.
    """

    quoted_uri = obj if isinstance(obj, str) else obj.get("id") if isinstance(obj, dict) else None
    if not isinstance(quoted_uri, str) or not quoted_uri:
        return
    resolved = await _resolve_local_object(session, quoted_uri, instance_domain)
    if resolved is None:
        # The quoted object is unknown — the request does not concern
        # a local post of this recipient.
        return
    owners = await _target_owner_users(session, {quoted_uri}, instance_domain=instance_domain)
    if not any(str(owner.id) == str(recipient.id) for owner in owners):
        # The quoted object is local but belongs to someone else — the
        # request was misaddressed to this recipient's inbox.
        return
    instrument = activity.get("instrument")
    instrument_doc = instrument if isinstance(instrument, dict) else {}
    quoting_id = instrument_doc.get("id") if instrument_doc else instrument
    source_url = quoting_id if isinstance(quoting_id, str) and quoting_id else activity_id
    target_fields = {f"target_{key}": value for key, value in resolved.items()}
    await _create_or_update_quote_notification(
        session,
        recipient=recipient,
        actor_url=actor_url,
        source_url=source_url,
        payload={
            **payload,
            **_note_snapshot(instrument_doc),
            "target_url": quoted_uri,
            **target_fields,
        },
    )


async def _process_incoming_quote_notification(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    payload: Dict[str, Any],
    quote_target: str,
    source_url: Optional[str],
    instance_domain: Optional[str],
    note: Dict[str, Any],
    self_fields: Dict[str, Any],
) -> bool:
    """
    FEP-044f: ``object`` is the quoted post, ``instrument`` the
    quoting post (a bare id, or the embedded object when the remote
    server includes it). Pubby auto-approves the request; this
    notification is what tells the quoted post's owner it happened —
    the quote's own ``Create`` may never reach this inbox. The
    instrument id is used as ``source_url`` so a later ``Create``
    notification for the same note dedupes onto this row while it
    stays unseen.
    """
    resolved = await _resolve_local_object(session, quote_target, instance_domain)
    owners = await _target_owner_users(session, {quote_target}, instance_domain=instance_domain)
    if resolved is not None and any(str(owner.id) == str(recipient.id) for owner in owners):
        # Only the quoted post's owner gets "quoted your post" — a
        # quote of someone else's post that merely tags the recipient
        # stays a ``mention``.
        target_fields = {f"target_{key}": value for key, value in resolved.items()}
        return (
            await _create_or_update_quote_notification(
                session,
                recipient=recipient,
                actor_url=actor_url,
                source_url=source_url,
                payload={
                    **payload,
                    **note,
                    **self_fields,
                    "target_url": quote_target,
                    **target_fields,
                },
            )
            is not None
        )

    return False


async def _process_incoming_reply_notification(
    session: AsyncSession,
    *,
    actor_url: str,
    recipient: User,
    payload: Dict[str, Any],
    in_reply_to: str,
    source_url: Optional[str],
    instance_domain: Optional[str],
    note: Dict[str, Any],
    self_fields: Dict[str, Any],
) -> bool:
    """
    FEP-044f: ``object`` is the replied-to post, ``instrument`` the
    replying post (a bare id, or the embedded object when the remote
    server includes it). Pubby auto-approves the request; this
    notification is what tells the replied-to post's owner it happened
    — the reply's own ``Create`` may never reach this inbox. The
    instrument id is used as ``source_url`` so a later ``Create``
    notification for the same note dedupes onto this row while it
    stays unseen.
    """

    resolved = await _resolve_local_object(session, in_reply_to, instance_domain)
    owners = await _target_owner_users(session, {in_reply_to}, instance_domain=instance_domain)
    if resolved is not None and any(str(owner.id) == str(recipient.id) for owner in owners):
        # Only the replied-to post's owner gets "replied to your
        # post" — a reply to someone else's post that merely tags
        # the recipient stays a ``mention``.
        target_fields = {f"target_{key}": value for key, value in resolved.items()}
        return (
            await create_notification(
                session,
                user_id=recipient.id,
                type=NotificationType.REPLY,
                actor_url=actor_url,
                source_url=source_url,
                payload={
                    **payload,
                    **note,
                    **self_fields,
                    "target_url": in_reply_to,
                    **target_fields,
                },
            )
            is not None
        )

    return False


async def create_inbox_notifications(
    session: AsyncSession,
    *,
    activity: dict,
    recipient: User,
    actor_doc: Optional[dict] = None,
    instance_domain: Optional[str] = None,
    storage: Optional[Any] = None,
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
    ``storage`` is the pubby backend used to flag follow notifications
    whose request is still pending approval.
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
        await _create_follow_inbox_notification(
            session,
            actor_url=actor_url,
            recipient=recipient,
            payload=payload,
            obj=obj,
            instance_domain=instance_domain,
            storage=storage,
        )
        return

    if activity_type in ("Like", "Announce"):
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
        return

    if activity_type == "QuoteRequest":
        await _create_quote_inbox_notification(
            session,
            actor_url=actor_url,
            recipient=recipient,
            activity=activity,
            activity_id=activity_id,
            payload=payload,
            obj=obj,
            instance_domain=instance_domain,
        )
        return

    if activity_type != "Create" or not isinstance(obj, dict):
        return

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

    mentioned = _mentions_recipient(obj, recipient.actor_url)
    if not thread_notified and mentioned:
        mention = await create_notification(
            session,
            user_id=recipient.id,
            type=NotificationType.MENTION,
            actor_url=actor_url,
            source_url=source_url,
            payload={**payload, **note, **self_fields},
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
            payload={**payload, **note, **self_fields},
            self_fields=self_fields,
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
