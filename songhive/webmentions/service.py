"""
Webmention domain service.

Incoming Webmentions are materialized as ``webmention`` activities attached
to the resolved Songhive entity: a ``urn:songhive:webmention:<hash>``
``source_id`` keyed on ``(source, target)`` makes the upsert idempotent so
source edits update the existing row and source removals retract it. Entity
owners are notified through the regular notification pipeline with a
``webmention`` notification type.

Outgoing delivery is scheduled through :func:`enqueue_outgoing_webmentions`,
which gates on the activity actually carrying URLs (or a Webmention
interaction marker, or a previously recorded delivery) before queueing the
Celery task — federation failures never fail local activity writes.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from webmentions import Webmention

from ..config.schema import SonghiveConfig
from ..models import Visibility
from ..models.activity import Activity, ActivityTag
from ..models.notification import NotificationType
from ..models.tag import Tag
from ..models.track import Track
from ..models.user import User

logger = logging.getLogger(__name__)

WEBMENTION_SOURCE_TYPE = "webmention"
WEBMENTION_PAYLOAD_KEY = "webmention"
WEBMENTION_SENT_KEY = "webmentions_sent"

# URL path → entity type for object permalinks (``/tracks/<id>`` etc.).
_ENTITY_PATH_TYPES = {
    "tracks": "track",
    "albums": "album",
    "artists": "artist",
    "playlists": "playlist",
    "libraries": "library",
    "radios": "radio",
}

_USER_PATH_RE = re.compile(r"^/users/(?P<username>[^/]+)/?$")
_USER_AT_PATH_RE = re.compile(r"^/@(?P<username>[^/]+)/?$")
_OBJECT_PATH_RE = re.compile(r"^/users/(?P<username>[^/]+)/objects/(?P<object_id>[^/]+)$")
_ACTIVITY_PATH_RE = re.compile(r"^/activities/(?P<activity_id>[^/]+)$")
_ENTITY_PATH_RE = re.compile(r"^/(?P<plural>[a-z]+)/(?P<entity_id>[^/]+)/?$")

# Media endpoints are mentionable too: an ``<audio src>``/``<img src>``
# embed of ``/api/v1/files/{id}/download`` is a mention of the entity the
# file belongs to (e.g. the track it backs).
_FILE_PATH_RE = re.compile(r"^/api/v1/files/(?P<file_id>[^/]+?)(?:/download)?/?$")
_TRACK_DOWNLOAD_PATH_RE = re.compile(r"^/api/v1/tracks/(?P<entity_id>[^/]+)/download/?$")
_STREAM_PATH_RE = re.compile(r"^/api/v1/stream/(?P<entity_id>[^/]+?)/?$")


def webmentions_enabled(config: SonghiveConfig) -> bool:
    """Whether Webmention support is on and a public domain is configured."""
    return bool(config.webmentions.enabled and config.federation.instance_domain.strip())


def webmention_activity_source_id(source: str, target: str) -> str:
    """Return the deterministic activity ``source_id`` for a (source, target) pair."""
    digest = hashlib.sha256(f"{source}|{target}".encode("utf-8")).hexdigest()[:40]
    return f"urn:songhive:webmention:{digest}"


def outgoing_source_url(config: SonghiveConfig, activity_id: str) -> str:
    """Return the public URL of an activity's outgoing Webmention source page."""
    return f"https://{config.federation.instance_domain.strip()}/webmentions/source/{activity_id}"


def webmention_endpoint_url(config: SonghiveConfig) -> str:
    """Return the public URL of the incoming Webmention endpoint."""
    return f"https://{config.federation.instance_domain.strip()}/webmentions"


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_EXCERPT_MAX_LENGTH = 240


def _html_to_text(html: str) -> str:
    """Reduce an HTML fragment to whitespace-collapsed plain text."""
    try:
        text = BeautifulSoup(html, "html.parser").get_text(" ")  # type: ignore
    except Exception:
        text = _HTML_TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _truncate_text(text: str, limit: int = _EXCERPT_MAX_LENGTH) -> str:
    """Truncate ``text`` at a word boundary within ``limit`` characters."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip()
    return f"{cut}…" if cut else f"{text[:limit]}…"


def webmention_display_excerpt(excerpt: Any, content: Any) -> Optional[str]:
    """Return a mention's displayable plain-text summary, or ``None``.

    What qualifies: an explicit ``p-summary`` — which is not a prefix of
    the content — a text-only content fallback such as an
    ``og:description``, or a derived excerpt that captures the complete
    (short) entry text, e.g. a one-line reply. Excerpts the parser
    derived by truncating the entry's markup are dropped: cards link to
    the source rather than dumping partial content verbatim.
    """
    excerpt_text = _html_to_text(excerpt) if isinstance(excerpt, str) else ""
    content_text = _html_to_text(content) if isinstance(content, str) else ""
    if excerpt_text and not content_text.startswith(excerpt_text.rstrip("…").rstrip()):
        return _truncate_text(excerpt_text)
    if isinstance(content, str) and content and "<" not in content:
        return _truncate_text(content_text)
    if excerpt_text and excerpt_text.rstrip("…").rstrip() == content_text:
        return _truncate_text(content_text)
    return None


def webmention_interaction_target(activity: Activity) -> Optional[str]:
    """Return the remote source URL when ``activity`` materializes a Webmention."""
    if activity.activity_type != "webmention":
        return None
    payload = activity.payload if isinstance(activity.payload, dict) else None
    mention = payload.get(WEBMENTION_PAYLOAD_KEY) if payload else None
    source = mention.get("source") if isinstance(mention, dict) else None
    return source if isinstance(source, str) and source else None


def set_webmention_marker(activity: Activity, property_name: str, target_url: str) -> None:
    """Flag a local activity so its outgoing source page carries ``u-<property>``.

    The marker makes the rendered ``h-entry`` a ``like-of``/``repost-of``/
    ``in-reply-to``/``quotation-of`` of the given remote URL — the interaction
    equivalent of an ActivityPub ``Like``/``Announce``/reply/quote.
    """
    payload = dict(activity.payload or {})
    payload[WEBMENTION_PAYLOAD_KEY] = {"property": property_name, "target": target_url}
    activity.payload = payload


def _content_has_url(activity: Activity) -> bool:
    return any(
        text and ("http://" in text or "https://" in text) for text in (activity.content, activity.content_source)
    )


def activity_has_webmention_targets(activity: Activity) -> bool:
    """Whether outgoing processing could have (or have had) targets."""
    payload = activity.payload if isinstance(activity.payload, dict) else {}
    marker = payload.get(WEBMENTION_PAYLOAD_KEY)
    return bool(
        (isinstance(marker, dict) and marker.get("target"))
        or payload.get(WEBMENTION_SENT_KEY)
        or _content_has_url(activity)
    )


def enqueue_outgoing_webmentions(activity: Activity, config: Optional[SonghiveConfig] = None) -> bool:
    """Queue outgoing Webmention processing for a local activity.

    Returns ``True`` when the Celery task was queued. ``config`` — when
    provided — short-circuits disabled/unconfigured instances; the task
    itself re-applies the same gates so it is safe to call without one.
    Only enqueued when the activity carries URLs, a Webmention interaction
    marker, or a recorded previous delivery (so edits/removals propagate).
    """
    if config is not None and not webmentions_enabled(config):
        return False
    if activity.source_type != "local":
        return False
    if not activity_has_webmention_targets(activity):
        return False
    try:
        from ..tasks.webmentions import process_outgoing_webmentions

        process_outgoing_webmentions.delay(str(activity.id))  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("Could not queue outgoing webmentions for activity %s: %s", activity.id, exc)
        return False
    return True


@dataclass
class ResolvedWebmentionTarget:
    """A local Songhive entity (or activity) a Webmention target URL resolves to."""

    entity_type: str
    entity_id: str
    entity: Any
    activity: Optional[Activity] = None


def _same_host(url: str, domain: str) -> bool:
    return (urlparse(url).hostname or "").lower() == domain.lower()


async def resolve_webmention_target(
    session: AsyncSession,
    target_url: str,
    domain: str,
) -> Optional[ResolvedWebmentionTarget]:
    """Map a local target URL to the Songhive entity/activity it identifies.

    Recognized shapes: ``/@user`` and ``/users/{username}`` profiles,
    ``/users/{username}/objects/{id}`` federation objects (published tracks
    and activities), ``/activities/{id}`` permalinks, the public entity
    pages (``/tracks/``, ``/albums/``, ``/artists/``, ``/playlists/``,
    ``/libraries/``, ``/radios/``), and the media endpoints remote pages
    embed via ``<audio>``/``<video>``/``<img>`` — ``/api/v1/stream/{id}``,
    ``/api/v1/tracks/{id}/download``, and ``/api/v1/files/{id}[/download]``
    (resolved to the entity the file backs). Returns ``None`` for foreign
    or unknown URLs — the mention stays recorded in storage without an
    activity.
    """
    if not _same_host(target_url, domain):
        return None
    path = str(urlparse(target_url).path.rstrip("/") or "/")

    match = _USER_PATH_RE.match(path) or _USER_AT_PATH_RE.match(path)
    if match:
        user = (
            await session.execute(select(User).where(User.username == match.group("username")))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            return None
        return ResolvedWebmentionTarget("user", str(user.id), user)

    match = _OBJECT_PATH_RE.match(path)
    if match:
        user = (
            await session.execute(select(User).where(User.username == match.group("username")))
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            return None
        object_id = match.group("object_id")
        track = (
            await session.execute(
                select(Track).where(
                    Track.federation_object_id == object_id,
                    Track.owner_id == str(user.id),
                    Track.visibility == Visibility.PUBLIC.value,
                )
            )
        ).scalar_one_or_none()
        if track is not None:
            return ResolvedWebmentionTarget("track", str(track.id), track)
        activity = (
            await session.execute(
                select(Activity).where(
                    or_(
                        Activity.local_object_id == object_id,
                        Activity.source_id == object_id,
                    ),
                    Activity.owner_user_id == str(user.id),
                )
            )
        ).scalar_one_or_none()
        return _activity_target(activity)

    match = _ACTIVITY_PATH_RE.match(path)
    if match:
        return _activity_target(await session.get(Activity, match.group("activity_id")))

    match = _TRACK_DOWNLOAD_PATH_RE.match(path) or _STREAM_PATH_RE.match(path)
    if match:
        from ..services.activities import resolve_entity

        entity = await resolve_entity(session, "track", match.group("entity_id"))
        if entity is not None:
            return ResolvedWebmentionTarget("track", match.group("entity_id"), entity)
        return None

    match = _FILE_PATH_RE.match(path)
    if match:
        return await _file_target(session, match.group("file_id"))

    match = _ENTITY_PATH_RE.match(path)
    if match:
        entity_type = _ENTITY_PATH_TYPES.get(match.group("plural"))
        if entity_type is None:
            return None
        from ..services.activities import resolve_entity

        entity = await resolve_entity(session, entity_type, match.group("entity_id"))
        if entity is None:
            return None
        return ResolvedWebmentionTarget(entity_type, match.group("entity_id"), entity)

    return None


async def _file_target(session: AsyncSession, file_id: str) -> Optional[ResolvedWebmentionTarget]:
    """Resolve a ``/api/v1/files/{id}`` URL to the entity the file backs.

    A stored file is content-addressed and may be referenced by several
    rows (audio dedup); the earliest-created match wins. Track audio is
    preferred since ``<audio>``/``<video>`` embeds are the common
    mentionable case.
    """
    from ..models.album import Album
    from ..models.artist import Artist
    from ..models.library import Library
    from ..models.playlist import Playlist
    from ..models.stored_file import StoredFile

    if await session.get(StoredFile, file_id) is None:
        return None

    candidates = (
        ("track", Track, Track.audio_file_id),
        ("track", Track, Track.image_file_id),
        ("album", Album, Album.cover_file_id),
        ("artist", Artist, Artist.image_file_id),
        ("artist", Artist, Artist.cover_file_id),
        ("playlist", Playlist, Playlist.image_file_id),
        ("playlist", Playlist, Playlist.cover_file_id),
        ("library", Library, Library.image_file_id),
        ("library", Library, Library.cover_file_id),
    )
    for entity_type, model, column in candidates:
        entity = (
            await session.execute(select(model).where(column == file_id).order_by(model.created_at).limit(1))
        ).scalar_one_or_none()
        if entity is not None:
            return ResolvedWebmentionTarget(entity_type, str(entity.id), entity)
    return None


def _activity_target(activity: Optional[Activity]) -> Optional[ResolvedWebmentionTarget]:
    if activity is None or activity.deleted_at is not None:
        return None
    return ResolvedWebmentionTarget(activity.entity_type, str(activity.entity_id), None, activity=activity)


def _mention_categories(mention: Webmention) -> List[str]:
    metadata = mention.metadata if isinstance(mention.metadata, dict) else {}
    raw_mf2 = metadata.get("mf2")
    mf2 = raw_mf2 if isinstance(raw_mf2, dict) else {}
    categories = mf2.get("category")
    if not isinstance(categories, list):
        return []
    return [str(name) for name in categories if isinstance(name, str) and name]


def _normalize_published(mention: Webmention) -> datetime:
    published = mention.published or mention.created_at
    if published is None:
        return datetime.now(timezone.utc)
    return published if published.tzinfo else published.replace(tzinfo=timezone.utc)


def _webmention_activity_payload(mention: Webmention) -> dict:
    return {
        "type": "webmention",
        "object": mention.source,
        WEBMENTION_PAYLOAD_KEY: mention.to_dict(),
    }


async def _find_webmention_activity(session: AsyncSession, mention: Webmention) -> Optional[Activity]:
    return (
        await session.execute(
            select(Activity).where(
                Activity.source_type == WEBMENTION_SOURCE_TYPE,
                Activity.source_id == webmention_activity_source_id(mention.source, mention.target),
            )
        )
    ).scalar_one_or_none()


def _target_visibility(target: ResolvedWebmentionTarget) -> str:
    """Clamp the materialized activity's visibility to its target's."""
    from ..services.activities import _entity_visibility

    # Mentions of non-public activities stay confined to that audience —
    # public entity pages expose the mention to the same readers.
    if target.activity is not None:
        return target.activity.visibility
    entity_visibility = _entity_visibility(target.entity)
    if Visibility.can_contain(Visibility.PUBLIC, entity_visibility):
        return Visibility.PUBLIC.value
    return entity_visibility.value


def _author_is_recipient(recipient: User, mention: Webmention, domain: str) -> bool:
    """Whether the mention's author URL identifies ``recipient`` (self-mention)."""
    author_url = (mention.author_url or "").rstrip("/")
    if not author_url:
        return False
    candidates = {
        (recipient.actor_url or "").rstrip("/"),
        f"https://{domain}/@{recipient.username}",
        f"https://{domain}/users/{recipient.username}",
    }
    return author_url in candidates


async def _webmention_recipient(session: AsyncSession, target: ResolvedWebmentionTarget) -> Optional[User]:
    """Resolve the local user to notify for a materialized mention."""
    if target.activity is not None and target.activity.owner_user_id:
        return await session.get(User, target.activity.owner_user_id)
    if target.entity_type == "user":
        return target.entity if isinstance(target.entity, User) else None
    owner_id = getattr(target.entity, "owner_id", None)
    return await session.get(User, owner_id) if owner_id else None


async def _notify_webmention(
    session: AsyncSession,
    *,
    activity: Activity,
    mention: Webmention,
    recipient: User,
    domain: str,
) -> None:
    """Notify ``recipient`` of the materialized mention, never failing it."""
    if _author_is_recipient(recipient, mention, domain):
        return
    try:
        from ..services import notifications as notifications_service
        from ..services.activities import _entity_link_fields

        actor_name = mention.author_name or urlparse(mention.author_url or mention.source).hostname
        payload = {
            "activity_id": activity.source_id,
            "object_url": mention.source,
            "object_name": mention.title,
            "object_content": webmention_display_excerpt(mention.excerpt, mention.content),
            "published": mention.published.isoformat() if mention.published else None,
            "webmention_type": mention.mention_type.value,
            "actor_name": actor_name,
            "actor_avatar_url": mention.author_photo,
            **(await _entity_link_fields(session, activity)),
        }
        if mention.author_name:
            payload["actor_display_name"] = mention.author_name
        await notifications_service.create_notification(
            session,
            user_id=str(recipient.id),
            type=NotificationType.WEBMENTION,
            actor_url=mention.author_url or mention.source,
            source_url=mention.source,
            payload=payload,
        )
    except Exception as exc:
        logger.warning("Failed to create webmention notification for activity %s: %s", activity.id, exc)


async def materialize_webmention(
    session: AsyncSession,
    mention: Webmention,
    config: SonghiveConfig,
) -> Optional[Activity]:
    """Create or update the ``webmention`` activity for a processed mention.

    Keyed on ``(source, target)`` through the deterministic ``source_id``:
    re-sent mentions update the existing row (content, payload snapshot,
    tags, visibility) and resurrect it when it was retracted, while the
    owning entity's ``Activities`` feed picks it up like any other row.
    The mention's mf2 ``category`` values are synced as Songhive tags.
    """
    from ..services.activities import _sync_activity_tags

    domain = config.federation.instance_domain.strip()
    target = await resolve_webmention_target(session, mention.target, domain)
    if target is None:
        logger.info("Webmention target %s does not resolve to a local entity; skipping", mention.target)
        return None

    activity = await _find_webmention_activity(session, mention)
    is_new = activity is None
    if activity is None:
        activity = Activity(
            entity_type=target.entity_type,
            entity_id=target.entity_id,
            activity_type="webmention",
            source_type=WEBMENTION_SOURCE_TYPE,
            source_actor=mention.author_url or mention.source,
            source_id=webmention_activity_source_id(mention.source, mention.target),
            owner_user_id=None,
            visibility=_target_visibility(target),
        )
        session.add(activity)

    activity.deleted_at = None
    activity.in_reply_to_activity_id = str(target.activity.id) if target.activity is not None else None
    activity.content = webmention_display_excerpt(mention.excerpt, mention.content)
    activity.content_type = "text/plain"
    activity.published_at = _normalize_published(mention)
    activity.payload = _webmention_activity_payload(mention)
    await session.flush()

    await _sync_activity_tags(session, activity, _mention_categories(mention))

    if is_new:
        recipient = await _webmention_recipient(session, target)
        if recipient is not None:
            await _notify_webmention(session, activity=activity, mention=mention, recipient=recipient, domain=domain)
    return activity


async def retract_webmention(
    session: AsyncSession,
    mention: Webmention,
) -> Optional[Activity]:
    """Soft-delete the activity materialized for a removed Webmention."""
    activity = await _find_webmention_activity(session, mention)
    if activity is None or activity.deleted_at is not None:
        return None
    from ..services.activities import retract_activity

    await retract_activity(session, activity)
    return activity


async def activity_tag_names(session: AsyncSession, activity: Activity) -> List[str]:
    """Return the tag names associated with an activity."""
    rows = await session.execute(
        select(Tag.name).join(ActivityTag, ActivityTag.tag_id == Tag.id).where(ActivityTag.activity_id == activity.id)
    )
    return [name for (name,) in rows.all()]
