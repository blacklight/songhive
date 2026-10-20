"""Notification helpers — snapshots, resolution, audience."""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from pubby.audience import addressees, is_public, mentioned_actors
from pubby.quotes import extract_quote_target
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Visibility
from ..models.activity import Activity, ActivityMention
from ..models.mention_record import MentionSource
from ..models.notification import Notification, NotificationType
from ..models.track import Track
from ..models.user import User
from ..services import acl
from ..services import mention_records as mention_records_service
from ..services.activities import (
    _activity_object_type,
    resolve_entity,
)
from ..services.notifications import (
    _push_updated,
    create_notification,
)

logger = logging.getLogger(__name__)


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


def _actor_name(actor_url: Optional[str], actor_doc: Optional[dict] = None) -> Optional[str]:
    """
    Derive a display name for an actor.

    The actor document's ``preferredUsername`` wins — actor ids may be
    opaque URIs (e.g. Mastodon ``/ap/users/<id>``) whose last segment is
    an internal id, not the username — and the URL tail is only a
    fallback when no usable document is at hand.
    """
    if not actor_url:
        return None
    if isinstance(actor_doc, dict):
        preferred = actor_doc.get("preferredUsername")
        if isinstance(preferred, str) and preferred.strip():
            return preferred.strip()
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

    activity, item, item_type = await _lookup_local_object_target(session, url, instance_domain)

    if item is None or item_type is None:
        return None
    resolved = _local_object_fields(item, item_type)
    if activity is not None:
        resolved["object_activity_id"] = str(activity.id)
        resolved["object_page_url"] = f"/activities/{activity.id}"
        object_type = _activity_object_type(activity)
        if object_type:
            resolved["object_type"] = object_type
    return resolved


async def _lookup_local_object_target(
    session: AsyncSession,
    url: str,
    instance_domain: Optional[str],
) -> Tuple[Optional[Activity], Any, Optional[str]]:
    """Resolve the URL to ``(activity, item, item_type)``."""
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
    return activity, item, item_type


def _local_object_fields(item, item_type: str) -> Dict[str, Any]:
    """Build the ``item_*``/``local_url`` payload fields for a resolved item."""
    if item_type == "user":
        return {
            "item_title": getattr(item, "display_name", None) or getattr(item, "username", None),
            "local_url": f"/@{getattr(item, 'username', item.id)}",
        }
    plural = acl.get_item_plural(item_type) or item_type
    return {
        "item_type": item_type,
        "item_id": str(item.id),
        "item_title": getattr(item, "title", None) or getattr(item, "name", None),
        "local_url": f"/{plural}/{item.id}",
    }


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
    public = "https://www.w3.org/ns/activitystreams#Public"
    addressing = obj.get("to") or obj.get("cc") or []
    if isinstance(addressing, str):
        addressing = [addressing]
    return public in addressing
