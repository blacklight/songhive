"""
Activity domain service.

This module provides the service layer for the multi-activity federation
model: entity resolution, local activity creation, and the parameter schema
used by the API layer.  Functions take an ``AsyncSession`` and follow the
codebase convention of flushing (not committing) so callers control the
transaction boundary.
"""

import asyncio
import base64
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple, Type, TypedDict
from urllib.parse import urlparse

from fastapi import HTTPException
from pubby import InteractionType
from pubby.content import render_post_html, set_object_content
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, delete, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..config.schema import SonghiveConfig
from ..federation import get_track_url
from ..federation.storage import create_activitypub_storage
from ..models import Visibility
from ..models.activity import (
    ACTIVITY_ENTITY_TYPES,
    ACTIVITY_TYPES,
    Activity,
    ActivityMention,
    ActivityTag,
    ActivityTarget,
)
from ..models.album import Album
from ..models.artist import Artist
from ..models.library import Library
from ..models.notification import NotificationType
from ..models.playlist import Playlist
from ..models.stored_file import StoredFile
from ..models.tag import Tag
from ..models.track import Track
from ..models.user import User
from . import federation as federation_service
from .acl import _activity_visibility_filter, can_access, can_manage, get_item_plural
from .mentions import (
    CONTENT_TYPE_MARKDOWN,
    STATUS_CONTENT_TYPES,
    process_mentions,
    tag_url_factory,
)
from .sharing import create_share_grant
from .tags import _entity_access_predicate, get_or_create_tag, validate_tag_name

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVITY_ENTITY_TYPES",
    "ACTIVITY_TYPES",
    "ActivityCreateParams",
    "VisibilityRules",
    "activity_page_url",
    "boost_activity",
    "can_view_activity",
    "create_local_activity",
    "create_status",
    "fan_out_activity",
    "fan_out_activity_update",
    "fan_out_boost_activity",
    "fan_out_like_activity",
    "fan_out_unreaction_activity",
    "like_activity",
    "list_activities",
    "list_activities_for_tag",
    "list_activity_interactors",
    "list_activity_replies",
    "list_user_activities",
    "reply_to_activity",
    "resolve_interaction_summaries",
    "resolve_audience",
    "resolve_entity",
    "resolve_source_actor_profiles",
    "sync_track_publications",
    "unreact_activity",
    "update_activity",
]

_ENTITY_MODELS: Dict[str, Type[Any]] = {
    "track": Track,
    "album": Album,
    "artist": Artist,
    "playlist": Playlist,
    "library": Library,
    "user": User,
}


class ActivityCreateParams(BaseModel):
    """Validated parameters for recording a new activity."""

    entity_type: str
    entity_id: str
    activity_type: str
    source_type: str
    source_actor: str
    source_id: str
    visibility: Visibility
    content: Optional[str] = None
    content_source: Optional[str] = None
    content_type: Optional[str] = "text/plain"
    in_reply_to_activity_id: Optional[str] = None
    payload: Optional[dict] = None
    owner_user_id: Optional[str] = None

    @field_validator("entity_type")
    @classmethod
    def _validate_entity_type(cls, value: str) -> str:
        if value not in ACTIVITY_ENTITY_TYPES:
            raise ValueError(f"Invalid entity_type: {value}")
        return value

    @field_validator("activity_type")
    @classmethod
    def _validate_activity_type(cls, value: str) -> str:
        if value not in ACTIVITY_TYPES:
            raise ValueError(f"Invalid activity_type: {value}")
        return value


async def resolve_entity(session: AsyncSession, entity_type: str, entity_id: str) -> Optional[Any]:
    """Resolve an entity by ``(entity_type, entity_id)``.

    Returns ``None`` when ``entity_type`` is not a supported activity entity
    type or when no row exists for ``entity_id``.
    """
    model = _ENTITY_MODELS.get(entity_type)
    if model is None:
        return None
    return await session.get(model, entity_id)


def _entity_visibility(entity: Any) -> Visibility:
    """Return the visibility of an entity, treating entities without a
    visibility column (e.g. ``Artist``) as public containers."""
    value = getattr(entity, "visibility", None)
    if value is None:
        return Visibility.PUBLIC
    return Visibility(value)


def _local_actor_url(author: User) -> str:
    """Return the author's actor URL, falling back to a local URN when the
    user has no provisioned federation identity."""
    if author.actor_url:
        return author.actor_url
    return f"urn:songhive:user:{author.username}"


def _extract_hashtags(text: Optional[str]) -> List[str]:
    """
    Extract normalized, deduplicated hashtags from raw or rendered text.

    ``render_post_html`` already recognizes ``#tags`` and normalizes them,
    so we reuse its parser and discard the rendered HTML.
    """
    if not text:
        return []
    rendered = render_post_html(text, lambda name: f"/tags/{name}")
    return rendered.hashtags


async def _sync_activity_tags(session: AsyncSession, activity: Activity, tag_names: List[str]) -> None:
    """
    Replace ``activity``'s hashtag associations with ``tag_names``.

    Missing ``Tag`` rows are created on demand. Flushes without committing.
    """
    seen: set[str] = set()
    normalized: List[str] = []
    for name in tag_names:
        try:
            validated = validate_tag_name(name)
        except ValueError:
            continue
        if validated not in seen:
            seen.add(validated)
            normalized.append(validated)

    result = await session.execute(
        select(ActivityTag, Tag.name)
        .join(Tag, ActivityTag.tag_id == Tag.id)
        .where(ActivityTag.activity_id == str(activity.id))
    )
    current: Dict[str, ActivityTag] = {name: assoc for assoc, name in result.all()}
    to_remove = [assoc for name, assoc in current.items() if name not in seen]

    for name in normalized:
        if name in current:
            continue
        tag = await get_or_create_tag(session, name)
        session.add(ActivityTag(activity_id=str(activity.id), tag_id=tag.id))

    for assoc in to_remove:
        await session.delete(assoc)

    if to_remove or any(n not in current for n in normalized):
        await session.flush()


async def can_view_activity(
    session: AsyncSession,
    user: Optional[User],
    activity: Activity,
) -> bool:
    """
    Return whether ``user`` may view ``activity``.

    The containing entity must be accessible (``acl.can_access``) and the
    activity's own visibility applies on top: ``public`` follows the entity
    check; ``local`` and ``followers`` require an authenticated user (the
    instance has no local follow graph, so followers-visible content is
    treated like local content for viewing); ``mentioned`` additionally
    requires the activity owner or a user named in the activity's
    mentions; ``private`` is limited to the owner. Admins get no extra
    reach over other authenticated users — ``mentioned`` and ``private``
    replies stay confined to their audience. Retracted (soft-deleted)
    activities are never viewable.
    """
    if activity.deleted_at is not None:
        return False

    try:
        visibility = Visibility(activity.visibility)
    except ValueError:
        return False

    if not await can_access(session, user, activity.entity_type, activity.entity_id):
        return False

    if visibility == Visibility.PUBLIC:
        return True

    if user is None:
        return False

    if activity.owner_user_id is not None and str(activity.owner_user_id) == str(user.id):
        return True

    if visibility in (Visibility.LOCAL, Visibility.FOLLOWERS):
        return True

    if visibility == Visibility.MENTIONED:
        result = await session.execute(
            select(ActivityMention.id)
            .where(
                ActivityMention.activity_id == activity.id,
                ActivityMention.user_id == user.id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    return False


def _encode_activity_cursor(activity: Activity) -> str:
    """Encode a keyset pagination cursor from the activity's sort position."""
    raw = f"{activity.published_at.isoformat()}|{activity.id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_activity_cursor(cursor: str) -> Tuple[datetime, str]:
    """Decode a keyset cursor into ``(published_at, activity_id)``.

    Raises ``HTTPException`` 400 for malformed cursors.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts_raw, sep, activity_id = raw.rpartition("|")
        if not sep or not activity_id:
            raise ValueError("malformed cursor")
        published_at = datetime.fromisoformat(ts_raw)
    except ValueError as e:
        raise HTTPException(400, detail="Invalid cursor") from e
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at, activity_id


async def list_activities(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    user: Optional[User] = None,
    activity_type: Optional[str] = None,
    source_type: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 20,
) -> Tuple[List[Activity], Optional[str]]:
    """List the activities attached to an entity, newest first.

    Applies the same per-activity visibility rules as ``can_view_activity``
    in SQL so pagination stays correct; the caller is responsible for
    checking that ``user`` may access the containing entity. Results are
    keyset-paginated on ``(published_at, id)`` — pass the returned cursor to
    fetch the next page. ``activity_type`` and ``source_type`` filter on
    exact matches. Raises ``HTTPException`` 400 for a malformed ``cursor``.
    """
    stmt = (
        select(Activity)
        .where(
            Activity.entity_type == entity_type,
            Activity.entity_id == str(entity_id),
            Activity.deleted_at.is_(None),
            _activity_visibility_filter(user),
        )
        .order_by(Activity.published_at.desc(), Activity.id.desc())
    )
    if activity_type is not None:
        stmt = stmt.where(Activity.activity_type == activity_type)
    if source_type is not None:
        stmt = stmt.where(Activity.source_type == source_type)
    if cursor:
        published_at, activity_id = _decode_activity_cursor(cursor)
        stmt = stmt.where(
            or_(
                Activity.published_at < published_at,
                and_(Activity.published_at == published_at, Activity.id < activity_id),
            )
        )

    result = await session.execute(stmt.limit(limit + 1))
    activities = list(result.scalars().all())
    next_cursor = None
    if len(activities) > limit:
        activities = activities[:limit]
        next_cursor = _encode_activity_cursor(activities[-1])
    return activities, next_cursor


def _accessible_activities_for_tag_cte(tag_name: str, user: Optional[User]) -> Any:
    """
    Build a CTE of activity ids that mention ``tag_name`` and are visible.

    Each subquery joins ``Activity`` with the entity it belongs to and applies
    the same list access predicate used elsewhere, so public tag pages only
    surface activities the requester may actually view.
    """
    subqueries = []
    for entity_type, model in _ENTITY_MODELS.items():
        pred = _entity_access_predicate(model, user, entity_type)
        subq = (
            select(Activity.id)
            .join(ActivityTag, ActivityTag.activity_id == Activity.id)
            .join(Tag, ActivityTag.tag_id == Tag.id)
            .join(model, and_(Activity.entity_type == entity_type, Activity.entity_id == model.id))
            .where(
                Tag.name == tag_name,
                Activity.deleted_at.is_(None),
                _activity_visibility_filter(user),
                pred,
            )
        )
        subqueries.append(subq)
    return union_all(*subqueries).cte("accessible_activities")


async def list_activities_for_tag(
    session: AsyncSession,
    tag_name: str,
    user: Optional[User] = None,
    cursor: Optional[str] = None,
    limit: int = 20,
) -> Tuple[List[Activity], Optional[str]]:
    """List visible activities that include ``tag_name``, newest first.

    Results are keyset-paginated on ``(published_at, id)`` like the other
    activity listers. Malformed tag names raise ``HTTPException`` 404.
    """
    try:
        validate_tag_name(tag_name)
    except ValueError:
        raise HTTPException(status_code=404, detail="Tag not found") from None

    cte = _accessible_activities_for_tag_cte(tag_name, user)
    stmt = (
        select(Activity).join(cte, Activity.id == cte.c.id).order_by(Activity.published_at.desc(), Activity.id.desc())
    )

    if cursor:
        published_at, activity_id = _decode_activity_cursor(cursor)
        stmt = stmt.where(
            or_(
                Activity.published_at < published_at,
                and_(Activity.published_at == published_at, Activity.id < activity_id),
            )
        )

    result = await session.execute(stmt.limit(limit + 1))
    activities = list(result.scalars().all())
    next_cursor = None
    if len(activities) > limit:
        activities = activities[:limit]
        next_cursor = _encode_activity_cursor(activities[-1])
    return activities, next_cursor


async def list_user_activities(
    session: AsyncSession,
    *,
    owner_user_id: str,
    user: Optional[User] = None,
    mode: str = "posts",
    include_boosts: bool = True,
    include_replies: bool = False,
    source_type: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 20,
) -> Tuple[List[Activity], Optional[str]]:
    """
    List activities by a user, newest first.

    ``mode="posts"`` returns the user's published local ``create``
    activities (Note posts and published-track shares); ``include_boosts``
    folds their ``announce`` activities into the timeline (the Mastodon
    default) and ``include_replies`` their ``reply`` activities (hidden by
    default). ``mode="all"`` returns every visible activity they authored
    or relayed (create, announce, like, reply, ...) and ignores the include
    flags. Visibility is applied through ``_activity_visibility_filter``.
    """
    if mode not in ("posts", "all"):
        raise HTTPException(status_code=400, detail="Invalid mode")

    stmt = (
        select(Activity)
        .where(
            Activity.owner_user_id == owner_user_id,
            Activity.deleted_at.is_(None),
            _activity_visibility_filter(user),
        )
        .order_by(Activity.published_at.desc(), Activity.id.desc())
    )

    if mode == "posts":
        activity_types = ["create"]
        if include_boosts:
            activity_types.append("announce")
        if include_replies:
            activity_types.append("reply")
        stmt = stmt.where(
            Activity.activity_type.in_(activity_types),
            Activity.source_type == "local",
        )

    if source_type is not None:
        stmt = stmt.where(Activity.source_type == source_type)

    if cursor:
        published_at, activity_id = _decode_activity_cursor(cursor)
        stmt = stmt.where(
            or_(
                Activity.published_at < published_at,
                and_(Activity.published_at == published_at, Activity.id < activity_id),
            )
        )

    result = await session.execute(stmt.limit(limit + 1))
    activities = list(result.scalars().all())
    next_cursor = None
    if len(activities) > limit:
        activities = activities[:limit]
        next_cursor = _encode_activity_cursor(activities[-1])
    return activities, next_cursor


def _actor_doc_avatar_url(actor_doc: Optional[dict]) -> Optional[str]:
    """Extract an avatar URL from a cached ActivityPub actor document."""
    if not actor_doc:
        return None
    icon = actor_doc.get("icon")
    if isinstance(icon, dict):
        return icon.get("url")
    if isinstance(icon, list):
        for item in icon:
            if isinstance(item, dict):
                url = item.get("url")
                if url:
                    return url
    if isinstance(icon, str):
        return icon
    return None


def _actor_doc_display_name(actor_doc: Optional[dict]) -> Optional[str]:
    """Extract a display name from a cached ActivityPub actor document."""
    if not actor_doc:
        return None
    name = actor_doc.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


class ActorProfile(NamedTuple):
    """Resolved avatar and display name for an activity's source actor."""

    avatar_url: Optional[str] = None
    display_name: Optional[str] = None


async def resolve_source_actor_profiles(
    session: AsyncSession,
    activities: List[Activity],
    config: SonghiveConfig,
) -> Dict[str, ActorProfile]:
    """Map each activity id to its source actor's profile.

    Local activities resolve to the owner's ``display_name`` and ``avatar_url``,
    falling back to the username when no display name is set. Remote activities
    are looked up in the federation actor cache, which avoids network calls.
    Activities whose actor cannot be resolved fall back to an empty profile.
    """
    if not activities:
        return {}

    result: Dict[str, ActorProfile] = {str(a.id): ActorProfile() for a in activities}

    local_activities = [a for a in activities if a.source_type == "local"]
    if local_activities:
        owner_ids = {a.owner_user_id for a in local_activities if a.owner_user_id}
        if owner_ids:
            rows = await session.execute(
                select(User.id, User.username, User.display_name, User.avatar_url).where(User.id.in_(owner_ids))
            )
            by_user = {str(row[0]): (row[1], row[2], row[3]) for row in rows}
            for activity in local_activities:
                if activity.owner_user_id:
                    username, display_name, avatar_url = by_user.get(str(activity.owner_user_id), (None, None, None))
                    if username:
                        result[str(activity.id)] = ActorProfile(
                            avatar_url=avatar_url,
                            display_name=display_name or username,
                        )

        # Some local activities may use a ``urn:songhive:user:<username>``
        # actor without an ``owner_user_id``. Resolve those by username.
        usernames = [
            a.source_actor[len("urn:songhive:user:") :]
            for a in local_activities
            if not a.owner_user_id and a.source_actor.startswith("urn:songhive:user:")
        ]
        if usernames:
            rows = await session.execute(
                select(User.username, User.display_name, User.avatar_url).where(User.username.in_(usernames))
            )
            by_username = {row[0]: (row[1], row[2]) for row in rows}
            for activity in local_activities:
                if not activity.owner_user_id and activity.source_actor.startswith("urn:songhive:user:"):
                    username = activity.source_actor[len("urn:songhive:user:") :]
                    display_name, avatar_url = by_username.get(username, (None, None))
                    if username:
                        result[str(activity.id)] = ActorProfile(
                            avatar_url=avatar_url,
                            display_name=display_name or username,
                        )

    if not config.federation.enabled or not config.federation.instance_domain:
        return result

    remote_actors: Dict[str, str] = {
        str(a.id): a.source_actor
        for a in activities
        if a.source_type == "remote" and a.source_actor.startswith(("http://", "https://"))
    }
    if remote_actors:
        try:
            storage = await asyncio.to_thread(create_activitypub_storage, config.database.url)

            async def _get_remote_profile(actor_url: str) -> ActorProfile:
                doc = await asyncio.to_thread(storage.get_cached_actor, actor_url)
                return ActorProfile(
                    avatar_url=_actor_doc_avatar_url(doc),
                    display_name=_actor_doc_display_name(doc),
                )

            unique_actors = list(set(remote_actors.values()))
            resolved = await asyncio.gather(*[_get_remote_profile(url) for url in unique_actors])
            profile_by_actor = dict(zip(unique_actors, resolved))
            for activity_id, actor_url in remote_actors.items():
                result[activity_id] = profile_by_actor.get(actor_url, ActorProfile())
        except Exception:
            logger.exception("Failed to resolve remote actor profiles")

    return result


# Mapping between ``activities.activity_type`` values and the pubby
# ``InteractionType`` stored for remote actors in ``federation_interactions``.
_REMOTE_INTERACTION_TYPES = {
    "like": InteractionType.LIKE,
    "announce": InteractionType.BOOST,
    "reply": InteractionType.REPLY,
}


def _fetch_remote_interactions(
    config: SonghiveConfig,
    source_ids: Iterable[str],
    interaction_type: Optional[InteractionType] = None,
) -> Dict[str, List[Any]]:
    """
    Fetch confirmed remote interactions for each object id in ``source_ids``.

    Reads Pubby's ``federation_interactions`` storage — remote likes, boosts
    and replies targeting a local object are recorded there by the inbox
    processor, keyed by the object's ActivityPub id (``Activity.source_id``).
    Synchronous and session-per-call; must be invoked through
    ``asyncio.to_thread`` from async code. Failures (e.g. the table not
    existing yet) surface as exceptions for the caller to swallow.
    """
    storage = create_activitypub_storage(config.database.url)
    result: Dict[str, List[Any]] = {}
    for source_id in source_ids:
        try:
            result[source_id] = list(storage.get_interactions(source_id, interaction_type=interaction_type))
        except Exception:
            result[source_id] = []
    return result


async def _remote_interactions(
    config: SonghiveConfig,
    source_ids: Iterable[str],
    interaction_type: Optional[InteractionType] = None,
) -> Dict[str, List[Any]]:
    """
    ``_fetch_remote_interactions`` wrapper: thread offloaded, best-effort.

    Returns an empty mapping when federation is disabled or unconfigured and
    swallows storage errors — remote interactions are additive context and
    must never break the surrounding request.
    """
    if not config.federation.enabled or not config.federation.instance_domain:
        return {}
    try:
        return await asyncio.to_thread(_fetch_remote_interactions, config, source_ids, interaction_type)
    except Exception:
        logger.exception("Failed to fetch remote interactions")
        return {}


def _interaction_sort_key(published: Optional[datetime]) -> datetime:
    """Normalize possibly-naive interaction timestamps for sorting."""
    if published is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if published.tzinfo is None:
        return published.replace(tzinfo=timezone.utc)
    return published


def _activity_descendants_cte(root_ids: Iterable[str]) -> Any:
    """
    Build a recursive CTE over the ``in_reply_to`` graph.

    Returns a ``(root_id, id, activity_type, source_id, deleted_at,
    visibility, owner_user_id)`` CTE with one row per activity reachable
    from ``root_ids`` by following ``in_reply_to_activity_id``, each
    tagged with the root it descends from. Traversal crosses activity
    types and soft-deleted rows — a deleted or non-reply node still
    anchors its own replies to the thread; callers filter by
    ``activity_type``/``deleted_at`` for display and counting. ``UNION``
    dedup keeps the walk finite even if the graph ever contained a cycle.
    """
    seed = (
        select(
            Activity.in_reply_to_activity_id.label("root_id"),
            Activity.id.label("id"),
            Activity.activity_type.label("activity_type"),
            Activity.source_id.label("source_id"),
            Activity.deleted_at.label("deleted_at"),
            Activity.visibility.label("visibility"),
            Activity.owner_user_id.label("owner_user_id"),
        )
        .where(Activity.in_reply_to_activity_id.in_(list(root_ids)))
        .cte("activity_descendants", recursive=True)
    )
    return seed.union(
        select(
            seed.c.root_id,
            Activity.id,
            Activity.activity_type,
            Activity.source_id,
            Activity.deleted_at,
            Activity.visibility,
            Activity.owner_user_id,
        ).join(seed, Activity.in_reply_to_activity_id == seed.c.id)
    )


async def _remote_thread_interactions(
    config: SonghiveConfig,
    target_ids: Iterable[str],
    interaction_type: InteractionType,
) -> List[Any]:
    """
    Collect confirmed remote interactions across whole reply threads.

    ``get_interactions`` is keyed by the target object id, so this performs
    a breadth-first walk seeded with ``target_ids``: interactions targeting
    a collected reply's own ``object_id`` (replies to remote replies) are
    gathered too. Results are in discovery order — a reply always precedes
    the replies that target it.
    """
    collected: List[Any] = []
    seen: Set[str] = set()
    frontier: List[str] = []
    for target_id in target_ids:
        if target_id and target_id not in seen:
            seen.add(target_id)
            frontier.append(target_id)
    while frontier:
        batch = await _remote_interactions(config, frontier, interaction_type)
        next_frontier: List[str] = []
        for source_id in frontier:
            for interaction in batch.get(source_id, []):
                collected.append(interaction)
                object_id = getattr(interaction, "object_id", None)
                if object_id and object_id not in seen:
                    seen.add(object_id or "")
                    next_frontier.append(object_id or "")
        frontier = next_frontier
    return collected


class InteractionSummary(NamedTuple):
    """Interaction counters and the requester's own state for an activity."""

    like_count: int = 0
    boost_count: int = 0
    reply_count: int = 0
    liked: bool = False
    boosted: bool = False


async def resolve_interaction_summaries(
    session: AsyncSession,
    activities: List[Activity],
    user: Optional[User],
    config: SonghiveConfig,
) -> Dict[str, InteractionSummary]:
    """
    Map each activity id to its like/boost/reply counters and viewer state.

    Counts combine local ``Activity`` rows (likes, boosts and replies stored
    with ``in_reply_to_activity_id``) and confirmed remote interactions
    recorded in Pubby's ``federation_interactions`` storage against the
    activity's ``source_id``. Likes and boosts count direct interactions
    only; ``reply_count`` covers the whole sub-thread — every ``reply``
    descendant reachable through the ``in_reply_to`` chain, plus remote
    replies targeting any node in it (remote replies to remote replies
    included). ``liked``/``boosted`` reflect whether ``user`` already has a
    live like/announce row targeting the activity.
    """
    if not activities:
        return {}

    ids = [str(a.id) for a in activities]
    counts: Dict[str, Dict[str, int]] = {i: {"like": 0, "announce": 0, "reply": 0} for i in ids}
    rows = await session.execute(
        select(Activity.in_reply_to_activity_id, Activity.activity_type, func.count())
        .where(
            Activity.in_reply_to_activity_id.in_(ids),
            Activity.activity_type.in_(("like", "announce")),
            Activity.deleted_at.is_(None),
        )
        .group_by(Activity.in_reply_to_activity_id, Activity.activity_type)
    )
    for target_id, activity_type, count in rows.all():
        counts[str(target_id)][activity_type] += count

    reacted: Dict[str, Set[str]] = {"like": set(), "announce": set()}
    if user is not None:
        rows = await session.execute(
            select(Activity.in_reply_to_activity_id, Activity.activity_type).where(
                Activity.in_reply_to_activity_id.in_(ids),
                Activity.activity_type.in_(("like", "announce")),
                Activity.owner_user_id == user.id,
                Activity.deleted_at.is_(None),
            )
        )
        for target_id, activity_type in rows.all():
            reacted[activity_type].add(str(target_id))

    remote = await _remote_interactions(config, {a.source_id for a in activities})
    for activity in activities:
        for interaction in remote.get(activity.source_id, []):
            for activity_type, interaction_type in _REMOTE_INTERACTION_TYPES.items():
                if activity_type == "reply":
                    continue
                if interaction.interaction_type == interaction_type:
                    counts[str(activity.id)][activity_type] += 1

    await _add_threaded_reply_counts(session, activities, counts, config, user)

    return {
        str(a.id): InteractionSummary(
            like_count=counts[str(a.id)]["like"],
            boost_count=counts[str(a.id)]["announce"],
            reply_count=counts[str(a.id)]["reply"],
            liked=str(a.id) in reacted["like"],
            boosted=str(a.id) in reacted["announce"],
        )
        for a in activities
    }


async def _add_threaded_reply_counts(
    session: AsyncSession,
    activities: List[Activity],
    counts: Dict[str, Dict[str, int]],
    config: SonghiveConfig,
    user: Optional[User],
) -> None:
    """
    Fold whole-thread reply counts into ``counts`` in place.

    ``reply_count`` covers every non-deleted ``reply`` descendant of an
    activity (replies to replies included) plus confirmed remote replies
    targeting any node in the sub-thread — replies to remote replies are
    reached breadth-first through their ``object_id`` chains. Remote
    replies already materialized into ``Activity`` rows are counted once,
    by the row pass; their interaction records are skipped. Descendants
    the viewer may not see (``mentioned`` replies naming someone else,
    ``private`` rows owned by another user) are not counted, so restricted
    replies don't leak through the counter — traversal still crosses them
    to reach their public children.
    """
    ids = [str(a.id) for a in activities]
    cte = _activity_descendants_cte(ids)
    rows = (
        await session.execute(
            select(
                cte.c.root_id,
                cte.c.id,
                cte.c.activity_type,
                cte.c.source_id,
                cte.c.deleted_at,
                cte.c.visibility,
                cte.c.owner_user_id,
            )
        )
    ).all()

    mentioned_ids: Set[str] = set()
    if user is not None:
        restricted = [
            row.id
            for row in rows
            if row.deleted_at is None and row.activity_type == "reply" and row.visibility == Visibility.MENTIONED.value
        ]
        if restricted:
            mentioned_ids = {
                str(row[0])
                for row in (
                    await session.execute(
                        select(ActivityMention.activity_id).where(
                            ActivityMention.activity_id.in_(restricted),
                            ActivityMention.user_id == user.id,
                        )
                    )
                ).all()
            }

    def _countable(row: Any) -> bool:
        if row.deleted_at is not None or row.activity_type != "reply":
            return False
        try:
            visibility = Visibility(row.visibility)
        except ValueError:
            return False
        if visibility == Visibility.PUBLIC:
            return True
        if user is None:
            return False
        if row.owner_user_id is not None and str(row.owner_user_id) == str(user.id):
            return True
        if visibility in (Visibility.LOCAL, Visibility.FOLLOWERS):
            return True
        return visibility == Visibility.MENTIONED and str(row.id) in mentioned_ids

    # ``membership`` maps a node id to the requested activities whose
    # sub-thread contains it (a node always contains itself); ``scope``
    # does the same keyed by object id — local ``source_id``s plus the
    # ``object_id``s remote replies expose — so a remote reply is credited
    # to every requested ancestor of its target. ``materialized`` tracks
    # the object ids already backed by a row so their interaction records
    # are not counted twice.
    membership: Dict[str, Set[str]] = {i: {i} for i in ids}
    source_node: Dict[str, str] = {a.source_id: str(a.id) for a in activities}
    materialized: Set[str] = {a.source_id for a in activities}
    for row in rows:
        if _countable(row):
            counts[str(row.root_id)]["reply"] += 1
        membership.setdefault(str(row.id), set()).add(str(row.root_id))
        source_node[row.source_id] = str(row.id)
        materialized.add(row.source_id)

    scope: Dict[str, Set[str]] = {}
    for source_id, node_id in source_node.items():
        roots = membership.get(node_id)
        if roots:
            scope.setdefault(source_id, set()).update(roots)

    if not scope:
        return

    replies = await _remote_thread_interactions(config, set(scope), InteractionType.REPLY)
    for interaction in replies:
        roots = scope.get(interaction.target_resource) or set()
        if interaction.object_id and interaction.object_id not in scope:
            scope[interaction.object_id] = set(roots)
        if interaction.object_id and interaction.object_id in materialized:
            continue
        for root_id in roots:
            counts[root_id]["reply"] += 1


class InteractionActor(NamedTuple):
    """A known account that liked or boosted an activity."""

    actor: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    username: Optional[str] = None
    profile_url: Optional[str] = None
    published_at: Optional[datetime] = None


async def list_activity_interactors(
    session: AsyncSession,
    *,
    activity: Activity,
    interaction_type: str,
    config: SonghiveConfig,
) -> List[InteractionActor]:
    """
    List the known accounts that liked or boosted ``activity``.

    ``interaction_type`` is ``"like"`` or ``"announce"``. Local interactions
    resolve to user profiles (``username`` set → the actor can be linked to
    its local profile page); remote ones come from Pubby's interaction
    storage and carry whatever identity the remote activity exposed
    (``author_name``/``author_url``/``author_photo``). Results are merged
    newest-first.
    """
    if interaction_type not in ("like", "announce"):
        raise HTTPException(400, detail=f"Invalid interaction_type: {interaction_type}")

    rows = (
        (
            await session.execute(
                select(Activity)
                .where(
                    Activity.in_reply_to_activity_id == str(activity.id),
                    Activity.activity_type == interaction_type,
                    Activity.deleted_at.is_(None),
                )
                .order_by(Activity.published_at.desc())
            )
        )
        .scalars()
        .all()
    )

    profiles = await resolve_source_actor_profiles(session, list(rows), config)
    owner_ids = {a.owner_user_id for a in rows if a.owner_user_id}
    usernames: Dict[str, str] = {}
    if owner_ids:
        user_rows = await session.execute(select(User.id, User.username).where(User.id.in_(owner_ids)))
        usernames = {str(row[0]): row[1] for row in user_rows}

    actors: List[InteractionActor] = []
    for row in rows:
        profile = profiles.get(str(row.id), ActorProfile())
        username = usernames.get(str(row.owner_user_id)) if row.owner_user_id else None
        if username is None and row.source_actor.startswith("urn:songhive:user:"):
            username = row.source_actor[len("urn:songhive:user:") :]
        actors.append(
            InteractionActor(
                actor=row.source_actor,
                display_name=profile.display_name,
                avatar_url=profile.avatar_url,
                username=username,
                published_at=row.published_at,
            )
        )

    remote = await _remote_interactions(config, [activity.source_id], _REMOTE_INTERACTION_TYPES[interaction_type])
    for interaction in remote.get(activity.source_id, []):
        actors.append(
            InteractionActor(
                actor=interaction.source_actor_id,
                display_name=interaction.author_name or None,
                avatar_url=interaction.author_photo or None,
                profile_url=interaction.author_url or interaction.source_actor_id,
                published_at=interaction.published,
            )
        )

    actors.sort(key=lambda a: _interaction_sort_key(a.published_at), reverse=True)
    return actors


async def list_activity_replies(
    session: AsyncSession,
    *,
    activity: Activity,
    user: Optional[User],
    config: SonghiveConfig,
) -> Tuple[List[Activity], List[Any]]:
    """
    List the known replies in ``activity``'s thread, oldest first.

    Returns ``(local, remote)``: local replies are every ``reply``-type
    descendant of ``activity`` — the whole sub-thread, not just direct
    children — filtered by the requester's visibility rules; remote replies
    are confirmed Pubby ``Interaction`` rows collected breadth-first across
    the thread (each carries its ``target_resource`` so clients can
    re-attach it to its parent), minus replies already materialized into
    ``Activity`` rows. The caller merges and serializes the two lists;
    clients group them into threads by parent.
    """
    cte = _activity_descendants_cte([str(activity.id)])
    rows = (await session.execute(select(cte.c.id, cte.c.activity_type, cte.c.source_id, cte.c.deleted_at))).all()

    reply_ids = [row.id for row in rows if row.activity_type == "reply" and row.deleted_at is None]
    local: List[Activity] = []
    if reply_ids:
        local = list(
            (
                await session.execute(
                    select(Activity)
                    .where(
                        Activity.id.in_(reply_ids),
                        _activity_visibility_filter(user),
                    )
                    .order_by(Activity.published_at.asc(), Activity.id.asc())
                )
            )
            .scalars()
            .all()
        )

    source_ids = [activity.source_id, *[row.source_id for row in rows]]
    remote = await _remote_thread_interactions(config, source_ids, InteractionType.REPLY)
    materialized = set(source_ids)
    remote_replies = sorted(
        (i for i in remote if getattr(i, "object_id", None) not in materialized),
        key=lambda i: _interaction_sort_key(i.published),
    )
    return local, remote_replies


async def _fan_out_visibility_update(
    session: AsyncSession,
    activity: Activity,
    new_visibility: Visibility,
) -> None:
    """
    Deliver the visibility change to the inboxes an activity already reached.

    Inboxes recorded as ``sent`` in ``activity_targets`` receive an ``Update``
    carrying the new audience when the new visibility still federates, or a
    ``Delete(Tombstone)`` when it no longer does (``private``/``local``) so
    remote instances drop their cached copy.
    """
    from ..federation.activities import (
        create_tombstone_delete_activity,
        create_visibility_update_activity,
    )
    from .deletion import enqueue_activity_delivery, get_activity_unpublish_info

    info = await get_activity_unpublish_info(session, activity)
    if not info.inboxes:
        return

    owner = await session.get(User, activity.owner_user_id) if activity.owner_user_id else None
    if Visibility.federates(new_visibility):
        mention_rows = await session.execute(
            select(ActivityMention.actor_url).where(
                ActivityMention.activity_id == activity.id,
                ActivityMention.actor_url.is_not(None),
            )
        )
        mention_actor_urls: List[str] = [url for url in mention_rows.scalars().all() if url]  # type: ignore
        payload = create_visibility_update_activity(
            info.actor_url,
            info.source_id,
            new_visibility,
            mention_actor_urls=mention_actor_urls,
        )
    else:
        payload = create_tombstone_delete_activity(info.actor_url, info.source_id)

    enqueue_activity_delivery(info, owner, payload)


class VisibilityRules:
    """Activity visibility enforcement and federation cascade helpers."""

    @staticmethod
    def can_contain(
        activity_visibility: "Visibility | str",
        entity_visibility: "Visibility | str",
    ) -> bool:
        """
        Return whether an entity with ``entity_visibility`` may contain an
        activity with ``activity_visibility``.
        """
        return Visibility.can_contain(
            Visibility(activity_visibility),
            Visibility(entity_visibility),
        )

    @staticmethod
    def enforce_activity_visibility(
        activity_visibility: "Visibility | str",
        entity_visibility: "Visibility | str",
    ) -> None:
        """
        Raise ``HTTPException`` 422 when ``activity_visibility`` is invalid
        or exceeds ``entity_visibility``.
        """
        try:
            child = Visibility(activity_visibility)
            parent = Visibility(entity_visibility)
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from e

        if not Visibility.can_contain(child, parent):
            raise HTTPException(
                422,
                detail=f"Activity visibility '{child.value}' exceeds entity visibility '{parent.value}'",
            )

    @staticmethod
    async def cascade_visibility_update(
        session: AsyncSession,
        activity: Activity,
        new_visibility: "Visibility | str",
    ) -> None:
        """
        Update an activity's visibility and cascade the change to remote
        instances that already received it.

        A no-op when the visibility is unchanged. The containing entity is
        re-validated so the new visibility cannot exceed it (404 when the
        entity no longer exists, 422 on violation). Only local activities fan
        out — remote instances own the visibility of remote activities.
        ``updated_at`` is maintained by the ORM ``onupdate`` hook. Flushes
        without committing; the caller owns the transaction.
        """
        try:
            new_value = Visibility(new_visibility)
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from e

        if activity.visibility == new_value.value:
            return

        entity = await resolve_entity(session, activity.entity_type, activity.entity_id)
        if entity is None:
            raise HTTPException(404, detail="Entity not found")

        VisibilityRules.enforce_activity_visibility(new_value, _entity_visibility(entity))

        if activity.source_type == "local" and activity.deleted_at is None:
            await _fan_out_visibility_update(session, activity, new_value)

        activity.visibility = new_value.value

        # Keep the stored payload's addressing in sync so re-deliveries of
        # the original activity (e.g. ``fan_out_activity`` sending the
        # ``Create`` to inboxes first reached by a later edit) carry the
        # current audience rather than the one recorded at creation.
        payload = activity.payload
        if isinstance(payload, dict):
            from ..federation.activities import activity_audience

            mention_rows = await session.execute(
                select(ActivityMention.actor_url).where(
                    ActivityMention.activity_id == activity.id,
                    ActivityMention.actor_url.is_not(None),
                )
            )
            mention_actor_urls: List[str] = [url for url in mention_rows.scalars().all() if url]  # type: ignore
            to, cc = activity_audience(new_value, activity.source_actor, mention_actor_urls)
            payload["to"] = to
            payload["cc"] = cc
            obj = payload.get("object")
            if isinstance(obj, dict):
                obj["to"] = to
                obj["cc"] = cc
            flag_modified(activity, "payload")

        await session.flush()


async def create_local_activity(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    activity_type: str,
    author: User,
    visibility: Visibility | str,
    content: Optional[str] = None,
    content_source: Optional[str] = None,
    content_type: Optional[str] = None,
    language: Optional[str] = None,
    in_reply_to_activity_id: Optional[str] = None,
    mentions: Optional[List[dict]] = None,
    payload: Optional[dict] = None,
    require_manage: bool = True,
) -> Activity:
    """
    Create a local activity attached to an entity.

    Raises ``HTTPException`` with status 404 when the entity does not exist,
    422 when the requested visibility exceeds the entity's visibility (or the
    ``activity_type``/``visibility`` values are invalid), and 403 when
    ``author`` may not manage the entity.

    ``mentions`` is a list of pre-resolved mention dicts (``handle``,
    optional ``actor_url`` and ``user_id``); mention extraction, resolution,
    and content rendering are layered on top of this service separately.

    ``require_manage`` gates creation on ``acl.can_manage`` and should stay
    enabled for content-producing activity types; interaction types (likes,
    replies) pass ``require_manage=False`` after performing their own
    view-level access check on the target activity and entity.
    """
    if activity_type not in ACTIVITY_TYPES:
        raise HTTPException(422, detail=f"Invalid activity_type: {activity_type}")

    entity = await resolve_entity(session, entity_type, entity_id)
    if entity is None:
        raise HTTPException(404, detail="Entity not found")

    try:
        activity_visibility = Visibility(visibility)
    except ValueError:
        raise HTTPException(422, detail=f"Invalid visibility: {visibility}")

    VisibilityRules.enforce_activity_visibility(activity_visibility, _entity_visibility(entity))

    if require_manage and not await can_manage(session, author, entity_type, entity_id):
        raise HTTPException(403, detail="Not authorized to create activity for this entity")

    source_actor = _local_actor_url(author)
    object_id = str(uuid.uuid4())
    rendered_content = content if content is not None else content_source

    activity = Activity(
        entity_type=entity_type,
        entity_id=str(entity_id),
        activity_type=activity_type,
        source_type="local",
        source_actor=source_actor,
        source_id=f"{source_actor}/objects/{object_id}",
        local_object_id=object_id,
        owner_user_id=author.id,
        visibility=activity_visibility.value,
        in_reply_to_activity_id=in_reply_to_activity_id,
        content=rendered_content,
        content_source=content_source,
        content_type=content_type or ("text/markdown" if content_source else "text/plain"),
        language=language,
        payload=payload,
    )
    session.add(activity)
    await session.flush()

    for mention in mentions or []:
        session.add(
            ActivityMention(
                activity_id=activity.id,
                handle=mention["handle"],
                actor_url=mention.get("actor_url"),
                user_id=mention.get("user_id"),
            )
        )

    await session.flush()

    source_text = content_source if content_source is not None else content
    await _sync_activity_tags(session, activity, _extract_hashtags(source_text))
    return activity


# Sentinel distinguishing "field not provided" (keep) from an explicit
# ``None``/empty value (clear) in partial updates.
_UNSET: Any = object()


def _is_user_attachment(doc: Any) -> bool:
    """Whether an attachment doc was user-added and is editable."""
    from ..federation.serializers import ATTACHMENT_FILE_ID_KEY, ATTACHMENT_TRACK_ID_KEY

    return isinstance(doc, dict) and (ATTACHMENT_FILE_ID_KEY in doc or ATTACHMENT_TRACK_ID_KEY in doc)


def _attachment_source_ids(obj: dict) -> Tuple[List[str], List[str]]:
    """Return ``(media_ids, track_ids)`` of an object's user attachments."""
    from ..federation.serializers import ATTACHMENT_FILE_ID_KEY, ATTACHMENT_TRACK_ID_KEY

    media_ids: List[str] = []
    track_ids: List[str] = []
    for doc in obj.get("attachment") or []:
        if not isinstance(doc, dict):
            continue
        file_id = doc.get(ATTACHMENT_FILE_ID_KEY)
        track_id = doc.get(ATTACHMENT_TRACK_ID_KEY)
        if isinstance(file_id, str) and file_id:
            media_ids.append(file_id)
        if isinstance(track_id, str) and track_id:
            track_ids.append(track_id)
    return media_ids, track_ids


async def _activity_mention_user_ids(session: AsyncSession, activity: Activity) -> List[str]:
    """Return the local user ids currently mentioned by ``activity``."""
    rows = await session.execute(
        select(ActivityMention.user_id).where(
            ActivityMention.activity_id == activity.id,
            ActivityMention.user_id.is_not(None),
        )
    )
    return [uid for (uid,) in rows.all()]


async def update_activity(
    session: AsyncSession,
    activity: Activity,
    *,
    content_source: Optional[str] = None,
    config: SonghiveConfig,
    content_type: Any = _UNSET,
    language: Any = _UNSET,
    media_ids: Any = _UNSET,
    track_ids: Any = _UNSET,
    editor: Optional[User] = None,
    audience: Optional[Visibility] = None,
) -> Activity:
    """
    Update an activity's content, format, language, and/or attachments.

    ``content_source`` is the new raw text: ``process_mentions`` re-resolves
    its ``@handle`` mentions and re-renders safe HTML, which replaces the
    stored ``content``/``content_source`` columns and the activity's
    ``activity_mentions`` rows.  ``None`` leaves the text untouched;
    ``content_type``/``language``/``media_ids``/``track_ids`` default to a
    sentinel meaning "unchanged" — pass an explicit value (including
    ``None`` for ``language`` or ``[]`` for the id lists) to overwrite.

    When the stored ``payload`` embeds a dict ``object`` (e.g. a ``Create``
    activity's object), its ``content`` and ``tag`` are rebuilt too:
    ``pubby.set_object_content`` merges tag tags while preserving
    pre-existing tags, the mention-aware pipeline rendering wins for
    ``content``, and the pipeline's ``Mention`` tags are merged in.
    Attachment edits rebuild the object's ``attachment`` list from the
    resolved files/tracks while preserving entity-owned docs (e.g. the
    shared track itself, which carries no ``songhive:`` source marker);
    ``_resolve_status_media`` re-applies ownership checks and escalates
    file visibility to the ``audience`` (or the activity's current
    visibility) requirement. ``language`` is mirrored into the object's
    ``contentMap``. The object is also stamped with ``updated`` so served
    documents and remote copies can surface the edit. Remote propagation
    is layered on top by :func:`fan_out_activity_update`, which the caller
    invokes separately so a pending visibility change can be applied first.

    ``editor`` is the user performing the edit — file ownership and track
    access are checked against it (falls back to the activity owner).

    Raises ``HTTPException`` 404 when the activity has been retracted, 422
    for an invalid ``content_type``/``language``, for edits that would
    leave a ``user``-entity status with neither text nor attachments, for
    over-limit attachment lists, or when attachment ids are supplied for an
    activity without an embedded object. Flushes without committing; the
    caller owns the transaction.
    """
    if activity.deleted_at is not None:
        raise HTTPException(404, detail="Activity not found")

    from ..federation.serializers import (
        normalize_post_content,
        stored_file_to_attachment,
        track_to_attachment,
    )

    payload = activity.payload
    obj = payload.get("object") if isinstance(payload, dict) else None
    if not isinstance(obj, dict):
        obj = None

    attachments_requested = media_ids is not _UNSET or track_ids is not _UNSET

    # A status on the author's ``user`` entity must keep some text or at
    # least one user-managed attachment.
    if activity.entity_type == "user":
        new_text = content_source if content_source is not None else (activity.content_source or "")
        kept_media, kept_tracks = _attachment_source_ids(obj) if obj is not None else ([], [])
        if media_ids is not _UNSET:
            kept_media = list(dict.fromkeys(media_ids or ()))
        if track_ids is not _UNSET:
            kept_tracks = list(dict.fromkeys(track_ids or ()))
        if not new_text.strip() and not kept_media and not kept_tracks:
            raise HTTPException(422, detail="Status must not be empty")

    if content_type is not _UNSET:
        if content_type not in STATUS_CONTENT_TYPES:
            raise HTTPException(422, detail=f"Invalid content_type: {content_type}")
        activity.content_type = content_type

    processed = None
    if content_source is not None or content_type is not _UNSET:
        # The stored ``content_type`` declares how ``content_source`` is
        # interpreted; ``text/markdown`` sources re-render as Markdown while
        # everything else uses the plain-text pipeline.
        text = content_source if content_source is not None else (activity.content_source or "")
        processed = await process_mentions(session, text, config, content_type=activity.content_type or "text/plain")
        activity.content = processed.html or None
        activity.content_source = text

        await session.execute(delete(ActivityMention).where(ActivityMention.activity_id == activity.id))
        for mention in processed.mentions:
            session.add(ActivityMention(activity_id=activity.id, **mention.as_dict()))
        session.expire(activity, ["mentions"])

    if language is not _UNSET:
        activity.language = _validate_status_language(language)

    if attachments_requested:
        if obj is None:
            raise HTTPException(422, detail="Activity has no editable attachments")
        new_media_ids, new_track_ids = _attachment_source_ids(obj)
        if media_ids is not _UNSET:
            new_media_ids = list(dict.fromkeys(media_ids or ()))
        if track_ids is not _UNSET:
            new_track_ids = list(dict.fromkeys(track_ids or ()))
        if len(new_media_ids) > _MAX_STATUS_ATTACHMENTS or len(new_track_ids) > _MAX_STATUS_ATTACHMENTS:
            raise HTTPException(422, detail="Too many attachments")

        resolver = editor or (await session.get(User, activity.owner_user_id) if activity.owner_user_id else None)
        if resolver is None:
            raise HTTPException(422, detail="Cannot resolve attachments for this activity")

        mention_user_ids = (
            [m.user_id for m in processed.mentions if m.user_id]
            if processed is not None
            else await _activity_mention_user_ids(session, activity)
        )
        required = audience or Visibility(activity.visibility)
        files = await _resolve_status_media(session, resolver, new_media_ids, required, mention_user_ids)
        tracks = await _resolve_status_tracks(session, resolver, new_track_ids)

        domain = (config.federation.instance_domain or "").strip()
        rebuilt = [stored_file_to_attachment(stored, domain) for stored in files]
        for track in tracks:
            artist = await session.get(Artist, track.artist_id) if track.artist_id else None
            audio_object_id = (
                f"{activity.source_actor}/objects/{track.federation_object_id}" if track.federation_object_id else None
            )
            rebuilt.append(track_to_attachment(track, artist, domain, audio_object_id=audio_object_id))
        obj["attachment"] = [a for a in obj.get("attachment") or [] if not _is_user_attachment(a)] + rebuilt

    if obj is not None and (processed is not None or attachments_requested or language is not _UNSET):
        domain = (config.federation.instance_domain or "").strip()
        if processed is not None:
            set_object_content(obj, activity.content_source or "", tag_url_factory(domain))
            if processed.html:
                obj["content"] = processed.html
            else:
                obj.pop("content", None)

            # The appended page link targets the entity's frontend page — a
            # share's object ``url`` is its own id, so the page cannot be
            # derived from the stored object for ``Note`` shares.
            link_href = (
                get_track_url(activity.entity_id, domain=domain) if activity.entity_type == "track" and domain else None
            )
            normalize_post_content(obj, link_href=link_href)
            activity.content = obj.get("content")
            if processed.tags:
                tags = obj.setdefault("tag", [])
                seen = {str(tag.get("name", "")).lower() for tag in tags if isinstance(tag, dict)}
                tags.extend(tag for tag in processed.tags if str(tag.get("name", "")).lower() not in seen)

        if activity.language and obj.get("content"):
            obj["contentMap"] = {activity.language: obj["content"]}
        else:
            obj.pop("contentMap", None)

        obj["updated"] = datetime.now(timezone.utc).isoformat()
        flag_modified(activity, "payload")
        await _refresh_notifications_for_object(session, activity.source_id, obj)

    if processed is not None:
        await _sync_activity_tags(session, activity, processed.tag_names)
    await session.flush()
    await session.refresh(activity, ["mentions"])
    return activity


async def _refresh_notifications_for_object(
    session: AsyncSession,
    object_id: Optional[str],
    object_doc: dict,
) -> None:
    """Rewrite notification snapshots of ``object_doc`` after an edit.

    ``mention``/``reply``/``quote`` notifications denormalize the object's
    renderable fields at creation (``object_*`` payload keys); when the
    object is edited, rows sourced from it are patched so they keep
    rendering current content. Flushes without committing; the caller owns
    the transaction.
    """
    if not object_id:
        return
    from ..federation.notifications import _note_snapshot
    from . import notifications as notifications_service

    note = _note_snapshot(object_doc)
    fields = {key: note.get(key) for key in notifications_service.OBJECT_SNAPSHOT_KEYS}
    await notifications_service.update_notifications_referencing(
        session,
        [object_id],
        fields_for=(lambda notification: dict(fields) if notification.type in ("mention", "reply", "quote") else None),
    )


async def _find_live_reaction(
    session: AsyncSession,
    *,
    activity: Activity,
    author: User,
    activity_type: str,
) -> Optional[Activity]:
    """Return ``author``'s live ``activity_type`` reaction to ``activity``."""
    return (
        await session.execute(
            select(Activity)
            .where(
                Activity.entity_type == activity.entity_type,
                Activity.entity_id == activity.entity_id,
                Activity.activity_type == activity_type,
                Activity.source_actor == _local_actor_url(author),
                Activity.in_reply_to_activity_id == activity.id,
                Activity.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def like_activity(
    session: AsyncSession,
    *,
    activity: Activity,
    author: User,
) -> Activity:
    """
    Record ``author``'s like of ``activity``.

    The like inherits the target activity's visibility and is attached to the
    same entity.  Raises ``HTTPException`` 404 when the target activity has
    been retracted and 400 when the author already liked it (a retracted like
    does not block a new one).  The stored ``payload`` is the ActivityPub
    ``Like`` activity, addressed — for ``mentioned`` visibility — to the
    target's author and mentioned actors.  Flushes without committing; the
    caller owns the transaction.
    """
    if activity.deleted_at is not None:
        raise HTTPException(404, detail="Activity not found")

    source_actor = _local_actor_url(author)
    if await _find_live_reaction(session, activity=activity, author=author, activity_type="like") is not None:
        raise HTTPException(400, detail="Already liked")

    like = await create_local_activity(
        session,
        entity_type=activity.entity_type,
        entity_id=activity.entity_id,
        activity_type="like",
        author=author,
        visibility=activity.visibility,
        in_reply_to_activity_id=str(activity.id),
        require_manage=False,
    )

    from ..federation.activities import create_like_activity

    mention_rows = await session.execute(
        select(ActivityMention.actor_url).where(
            ActivityMention.activity_id == activity.id,
            ActivityMention.actor_url.is_not(None),
        )
    )
    addressed: Set[str] = {url for url in mention_rows.scalars().all() if url}  # type: ignore
    addressed.add(activity.source_actor)
    addressed.discard(source_actor)

    like.payload = create_like_activity(
        source_actor,
        activity.source_id,
        activity.visibility,
        mention_actor_urls=sorted(addressed),
        activity_id=like.source_id,
    )
    await session.flush()
    await _notify_reaction(session, activity=activity, interaction=like, author=author, type=NotificationType.LIKE)
    return like


async def boost_activity(
    session: AsyncSession,
    *,
    activity: Activity,
    author: User,
) -> Activity:
    """Record ``author``'s boost (``Announce``) of ``activity``.

    Mirrors :func:`like_activity`: the boost inherits the target activity's
    visibility and is attached to the same entity. Raises ``HTTPException``
    404 when the target activity has been retracted and 400 when the author
    already boosted it. The stored ``payload`` is the ActivityPub
    ``Announce`` activity, addressed — for ``mentioned`` visibility — to the
    target's author and mentioned actors. Flushes without committing; the
    caller owns the transaction.
    """
    if activity.deleted_at is not None:
        raise HTTPException(404, detail="Activity not found")

    source_actor = _local_actor_url(author)
    if await _find_live_reaction(session, activity=activity, author=author, activity_type="announce") is not None:
        raise HTTPException(400, detail="Already boosted")

    boost = await create_local_activity(
        session,
        entity_type=activity.entity_type,
        entity_id=activity.entity_id,
        activity_type="announce",
        author=author,
        visibility=activity.visibility,
        in_reply_to_activity_id=str(activity.id),
        require_manage=False,
    )

    from ..federation.activities import create_announce_activity

    mention_rows = await session.execute(
        select(ActivityMention.actor_url).where(
            ActivityMention.activity_id == activity.id,
            ActivityMention.actor_url.is_not(None),
        )
    )
    addressed: Set[str] = {url for url in mention_rows.scalars().all() if url}  # type: ignore
    addressed.add(activity.source_actor)
    addressed.discard(source_actor)

    boost.payload = create_announce_activity(
        source_actor,
        activity.source_id,
        activity.visibility,
        mention_actor_urls=sorted(addressed),
        activity_id=boost.source_id,
    )
    await session.flush()
    await _notify_reaction(session, activity=activity, interaction=boost, author=author, type=NotificationType.BOOST)
    return boost


async def unreact_activity(
    session: AsyncSession,
    *,
    activity: Activity,
    author: User,
    interaction_type: str,
) -> Activity:
    """
    Retract ``author``'s ``interaction_type`` reaction to ``activity``.

    ``interaction_type`` is ``"like"`` or ``"announce"``. The reaction row
    is soft-deleted so a later like/boost of the same target creates a
    fresh activity, and the notification the reaction produced on the
    target's owner is retracted (it references the reaction's ``source_id``
    through ``payload.activity_id``). Raises ``HTTPException`` 400 for an
    invalid ``interaction_type``, 404 when the target has been retracted or
    no live reaction exists. Flushes without committing; the caller owns
    the transaction.
    """
    if interaction_type not in ("like", "announce"):
        raise HTTPException(400, detail=f"Invalid interaction_type: {interaction_type}")
    if activity.deleted_at is not None:
        raise HTTPException(404, detail="Activity not found")

    reaction = await _find_live_reaction(session, activity=activity, author=author, activity_type=interaction_type)
    if reaction is None:
        raise HTTPException(404, detail="Interaction not found")

    reaction.deleted_at = datetime.now(timezone.utc)
    await session.flush()

    if reaction.source_id:
        from . import notifications as notifications_service

        await notifications_service.retract_notifications_referencing(session, [reaction.source_id])
    return reaction


def activity_page_url(entity_type: str, entity_id: str, entity: Any = None) -> str:
    """
    Return the SPA route listing an entity's activities.

    This is the closest thing to an activity's own page: user-entity
    objects (standalone statuses) point at the author's profile page —
    pass the resolved ``User`` row as ``entity`` so the username is
    available; the user id falls back into the path when it is not — and
    other entities point at their activity feed.
    """
    if entity_type == "user":
        username = getattr(entity, "username", None) or entity_id
        return f"/@{username}"
    plural = get_item_plural(entity_type) or f"{entity_type}s"
    return f"/{plural}/{entity_id}/activities"


def _activity_object_type(activity: Activity) -> Optional[str]:
    """Return the ActivityStreams type of an activity's embedded object."""
    payload = activity.payload
    if not isinstance(payload, dict):
        return None
    obj = payload.get("object")
    if not isinstance(obj, dict):
        return None
    object_type = obj.get("type")
    return object_type if isinstance(object_type, str) and object_type else None


async def _entity_link_fields(session: AsyncSession, activity: Activity) -> Dict[str, Any]:
    """
    Return the link fields describing an activity's entity and page.

    ``item_*``/``local_url`` identify the containing entity for item-card
    rendering (``item_type``/``item_id`` are omitted for ``user`` entities —
    statuses have no item page); ``object_activity_id``/``object_type``/
    ``object_page_url`` identify the activity itself so clients can render
    its card and link to its ``/activities/{id}`` page.
    """
    entity = await resolve_entity(session, activity.entity_type, activity.entity_id)
    if entity is None:
        return {}
    fields: Dict[str, Any] = {
        "item_title": getattr(entity, "title", None)
        or getattr(entity, "name", None)
        or getattr(entity, "display_name", None)
        or getattr(entity, "username", None),
        "object_page_url": f"/activities/{activity.id}",
        "object_activity_id": str(activity.id),
    }
    object_type = _activity_object_type(activity)
    if object_type:
        fields["object_type"] = object_type
    if activity.entity_type == "user":
        # Statuses have no item page — the author's profile is the link.
        fields["local_url"] = activity_page_url(activity.entity_type, activity.entity_id, entity)
    else:
        plural = get_item_plural(activity.entity_type) or f"{activity.entity_type}s"
        fields["item_type"] = activity.entity_type
        fields["item_id"] = str(activity.entity_id)
        fields["local_url"] = f"/{plural}/{activity.entity_id}"
    return fields


async def _notify_reaction(
    session: AsyncSession,
    *,
    activity: Activity,
    interaction: Activity,
    author: User,
    type: NotificationType,
) -> None:
    """Notify the reacted activity's local owner, never failing the reaction.

    Mirrors the federated ``Like``/``Announce`` inbox path: ``source_url``
    is the reacted object's id and ``payload.activity_id`` the reaction's own
    object id, so ``retract_activity`` removes the row when the reaction is
    undone or the reacted activity is retracted. Remote-authored activities
    carry no ``owner_user_id`` — their authors are reached through fan-out
    instead. Self-reactions and notification failures never produce or break
    anything.
    """
    if activity.owner_user_id is None or str(activity.owner_user_id) == str(author.id):
        return
    try:
        from . import notifications as notifications_service

        payload: Dict[str, Any] = {
            "activity_id": interaction.source_id,
            "actor_name": author.display_name or author.username,
            "actor_avatar_url": author.avatar_url,
        }
        if author.display_name:
            payload["actor_display_name"] = author.display_name
        # ``object_*`` fields identify the reacted activity itself so clients
        # can render it and link the action text to it; ``item_*``/
        # ``local_url`` keep the containing entity as a card fallback.
        payload.update(await _entity_link_fields(session, activity))
        await notifications_service.create_notification(
            session,
            user_id=str(activity.owner_user_id),
            type=type,
            actor_url=_local_actor_url(author),
            source_url=activity.source_id,
            payload=payload,
        )
    except Exception as exc:
        logger.warning("Failed to create %s notification for activity %s: %s", type, activity.id, exc)


def _remote_actor_handle(actor_url: str) -> str:
    """Derive a ``@name@host`` handle from a remote actor URL."""
    parsed = urlparse(actor_url)
    name = parsed.path.rstrip("/").rsplit("/", 1)[-1] or parsed.netloc
    return f"@{name}@{parsed.netloc}" if parsed.netloc else f"@{name}"


async def reply_to_activity(
    session: AsyncSession,
    *,
    activity: Activity,
    author: User,
    config: SonghiveConfig,
    status_text: Optional[str] = None,
    content_type: str = CONTENT_TYPE_MARKDOWN,
    visibility: "Visibility | str | None" = None,
    language: Optional[str] = None,
    media_ids: Optional[Iterable[str]] = None,
    track_ids: Optional[Iterable[str]] = None,
) -> Activity:
    """Record ``author``'s reply to ``activity`` as a ``reply`` activity.

    The reply is a ``Create(Note)`` whose object carries
    ``inReplyTo: activity.source_id`` and is attached to the same entity —
    it shows up in the entity's feed and in the target's reply list. The
    requested ``visibility`` defaults to the target's own and may never
    exceed it (``Visibility.can_contain``) nor the containing entity's.

    The replied-to author is always addressed: a local owner gets an
    ``ActivityMention`` row (so ``mentioned``-visibility replies stay
    viewable to them) plus a ``reply`` notification; a remote author gets an
    ``ActivityMention`` row and a ``Mention`` tag so ``resolve_audience``
    delivers the reply to their inbox.

    Raises ``HTTPException`` 404 when the target activity has been retracted
    or its entity is gone, and 422 for an empty reply, an invalid
    ``content_type``/``visibility``/``language``, an over-limit attachment
    list, or a visibility exceeding the target's. Flushes without
    committing; the caller owns the transaction.
    """
    from ..federation.activities import create_status_activity
    from ..federation.serializers import stored_file_to_attachment, track_to_attachment

    if activity.deleted_at is not None:
        raise HTTPException(404, detail="Activity not found")

    text = (status_text or "").strip()
    media_ids = list(media_ids or [])
    track_ids = list(track_ids or [])
    if not text and not media_ids and not track_ids:
        raise HTTPException(422, detail="Reply must not be empty")
    if len(set(media_ids)) > _MAX_STATUS_ATTACHMENTS or len(set(track_ids)) > _MAX_STATUS_ATTACHMENTS:
        raise HTTPException(422, detail="Too many attachments")
    if content_type not in STATUS_CONTENT_TYPES:
        raise HTTPException(422, detail=f"Invalid content_type: {content_type}")
    language = _validate_status_language(language)

    try:
        target_visibility = Visibility(activity.visibility)
    except ValueError:
        raise HTTPException(422, detail=f"Invalid target visibility: {activity.visibility}")
    if visibility is None:
        reply_visibility = target_visibility
    else:
        try:
            reply_visibility = Visibility(visibility)
        except ValueError:
            raise HTTPException(422, detail=f"Invalid visibility: {visibility}")
    if not Visibility.can_contain(reply_visibility, target_visibility):
        raise HTTPException(422, detail="Reply visibility exceeds the replied-to activity's visibility")

    entity = await resolve_entity(session, activity.entity_type, activity.entity_id)
    if entity is None:
        raise HTTPException(404, detail="Entity not found")
    VisibilityRules.enforce_activity_visibility(reply_visibility, _entity_visibility(entity))

    processed = await process_mentions(session, text, config, content_type=content_type)
    mentioned_user_ids = [m.user_id for m in processed.mentions if m.user_id]
    files = await _resolve_status_media(session, author, media_ids, reply_visibility, mentioned_user_ids)
    tracks = await _resolve_status_tracks(session, author, track_ids)

    domain = (config.federation.instance_domain or "").strip()
    actor_url = _local_actor_url(author)
    attachments = [stored_file_to_attachment(stored, domain) for stored in files]
    for track in tracks:
        artist = await session.get(Artist, track.artist_id) if track.artist_id else None
        audio_object_id = f"{actor_url}/objects/{track.federation_object_id}" if track.federation_object_id else None
        attachments.append(track_to_attachment(track, artist, domain, audio_object_id=audio_object_id))

    # The replied-to author is always addressed — locally through a mention
    # row (which keeps ``mentioned``-visibility replies viewable to them) and
    # a ``reply`` notification, remotely through a mention row + ``Mention``
    # tag so ``resolve_audience`` reaches their inbox.
    mention_dicts = [m.as_dict() for m in processed.mentions]
    mention_actor_urls: List[str] = [m.actor_url for m in processed.mentions if m.actor_url]  # type: ignore
    extra_tags: List[dict] = []
    target_owner = await session.get(User, activity.owner_user_id) if activity.owner_user_id else None
    if target_owner is not None:
        if all(m.user_id != target_owner.id for m in processed.mentions):
            handle = f"@{target_owner.username}"
            mention_dicts.append({"handle": handle, "actor_url": target_owner.actor_url, "user_id": target_owner.id})
            if target_owner.actor_url:
                mention_actor_urls.append(target_owner.actor_url)
                extra_tags.append({"type": "Mention", "href": target_owner.actor_url, "name": handle})
    elif activity.source_actor.startswith(("http://", "https://")) and all(
        m.actor_url != activity.source_actor for m in processed.mentions
    ):
        handle = _remote_actor_handle(activity.source_actor)
        mention_dicts.append({"handle": handle, "actor_url": activity.source_actor, "user_id": None})
        mention_actor_urls.append(activity.source_actor)
        extra_tags.append({"type": "Mention", "href": activity.source_actor, "name": handle})

    object_uuid = str(uuid.uuid4())
    object_id = f"{actor_url}/objects/{object_uuid}"
    note: dict = {
        "type": "Note",
        "id": object_id,
        "url": object_id,
        "attributedTo": actor_url,
        "published": datetime.now(timezone.utc).isoformat(),
        "inReplyTo": activity.source_id,
    }
    if processed.html:
        note["content"] = processed.html
        if language:
            note["contentMap"] = {language: processed.html}
    if attachments:
        note["attachment"] = attachments
    if processed.tags or extra_tags:
        tags = list(processed.tags)
        seen = {str(tag.get("name", "")).lower() for tag in tags if isinstance(tag, dict)}
        tags.extend(tag for tag in extra_tags if tag["name"].lower() not in seen)
        note["tag"] = tags

    payload = create_status_activity(
        actor_url,
        note,
        reply_visibility,
        mention_actor_urls=mention_actor_urls,
    )

    reply = Activity(
        entity_type=activity.entity_type,
        entity_id=activity.entity_id,
        activity_type="reply",
        source_type="local",
        source_actor=actor_url,
        source_id=object_id,
        local_object_id=object_uuid,
        owner_user_id=author.id,
        visibility=reply_visibility.value,
        in_reply_to_activity_id=str(activity.id),
        content=processed.html or None,
        content_source=text or None,
        content_type=content_type,
        language=language,
        payload=payload,
    )
    session.add(reply)
    await session.flush()

    for mention in mention_dicts:
        session.add(ActivityMention(activity_id=reply.id, **mention))
    await session.flush()

    await _sync_activity_tags(session, reply, processed.tag_names)
    await _notify_reply(session, activity=activity, reply=reply, author=author)
    await _notify_status_mentions(
        session,
        activity=reply,
        author=author,
        mentions=processed.mentions,
        skip_user_ids={str(activity.owner_user_id)} if activity.owner_user_id else set(),
    )

    try:
        await fan_out_activity(session, reply, config, owner=author)
    except Exception as e:
        # Fan-out is best-effort: a broker or resolution failure must not
        # fail the reply itself — the activity row still records it.
        logger.exception("Failed to fan out reply for activity %s: %s: %s", activity.id, type(e), e)
    return reply


async def _notify_reply(
    session: AsyncSession,
    *,
    activity: Activity,
    reply: Activity,
    author: User,
) -> None:
    """Notify the replied-to activity's local owner, never failing the reply.

    Mirrors the federated reply inbox path: the payload carries a snapshot
    of the reply note (``object_*`` fields) plus the ``target_*`` fields
    resolving the replied-to entity, and ``source_url``/``payload.
    activity_id`` point at the reply's own object id so ``retract_activity``
    removes the row when the reply is deleted. Self-replies and remote
    targets (no ``owner_user_id``) produce nothing.
    """
    if activity.owner_user_id is None or str(activity.owner_user_id) == str(author.id):
        return
    try:
        from ..federation.notifications import _note_snapshot
        from . import notifications as notifications_service

        note = reply.payload.get("object") if isinstance(reply.payload, dict) else {}
        snapshot = _note_snapshot(note) if isinstance(note, dict) else {}
        # The reply's own link fields let clients render its real card and
        # link to its page; ``target_*`` resolves the replied-to activity.
        object_fields = await _entity_link_fields(session, reply)
        target_fields = {
            f"target_{key}": value for key, value in (await _entity_link_fields(session, activity)).items()
        }
        payload: Dict[str, Any] = {
            "activity_id": reply.source_id,
            "actor_name": author.display_name or author.username,
            "actor_avatar_url": author.avatar_url,
            **snapshot,
            **object_fields,
            "target_url": activity.source_id,
            **target_fields,
        }
        if author.display_name:
            payload["actor_display_name"] = author.display_name
        await notifications_service.create_notification(
            session,
            user_id=str(activity.owner_user_id),
            type=NotificationType.REPLY,
            actor_url=_local_actor_url(author),
            source_url=reply.source_id,
            payload=payload,
        )
    except Exception as exc:
        logger.warning("Failed to create reply notification for activity %s: %s", activity.id, exc)


async def _remote_mention_actor_urls(
    session: AsyncSession,
    activity: Activity,
    config: SonghiveConfig,
) -> List[str]:
    """
    Return the remote actor URLs mentioned by ``activity``.

    Mentions that resolved to a local user (``user_id`` set) or carry an
    actor URL on this instance's domain are excluded — there is no remote
    inbox to deliver to — as are non-HTTP(S) actor URLs.
    """
    rows = await session.execute(
        select(ActivityMention.actor_url).where(
            ActivityMention.activity_id == activity.id,
            ActivityMention.actor_url.is_not(None),
            ActivityMention.user_id.is_(None),
        )
    )
    instance_domain = federation_service.normalize_instance_domain(config.federation.instance_domain or "")

    urls: List[str] = []
    seen: Set[str] = set()
    for url in rows.scalars().all():
        if not url or not url.startswith(("http://", "https://")) or url in seen:
            continue
        if instance_domain and federation_service.extract_domain(url) == instance_domain:
            continue
        seen.add(url)
        urls.append(url)
    return urls


async def resolve_audience(
    session: AsyncSession,
    activity: Activity,
    config: SonghiveConfig,
    *,
    signer: Optional[User] = None,
) -> Set[str]:
    """
    Resolve the remote inbox URLs an activity should be delivered to.

    The audience is derived from the activity's visibility:

    - ``public`` and ``followers`` reach the author's follower inboxes (from
      Pubby's follower storage via ``get_follower_inboxes``, which prefers
      each follower's ``shared_inbox``) plus every remote mentioned actor.
    - ``mentioned`` reaches only the remote mentioned actors.
    - ``private`` and ``local`` never federate and produce an empty audience.

    Mentioned actors' inboxes are resolved through
    ``services.federation.resolve_actor_inbox`` (``federation_actor_cache``
    first, then a remote actor-document fetch). ``signer`` — normally the
    activity owner — supplies the key used to sign those fetches. Blocking
    storage and HTTP calls run in threads; the function returns ``set()``
    when federation is disabled or the visibility is invalid.
    """
    if not config.federation.enabled:
        return set()
    try:
        visibility = Visibility(activity.visibility)
    except ValueError:
        return set()
    if not Visibility.federates(visibility):
        return set()

    inboxes: Set[str] = set()
    if visibility in (Visibility.PUBLIC, Visibility.FOLLOWERS) and activity.source_actor.startswith(
        ("http://", "https://")
    ):
        inboxes.update(
            await asyncio.to_thread(
                federation_service.get_follower_inboxes,
                activity.source_actor,
                config.database.url,
            )
        )

    actor_urls = await _remote_mention_actor_urls(session, activity, config)
    if actor_urls:
        key_id = f"{signer.actor_url}#main-key" if signer and signer.actor_url else None
        private_key_pem = signer.private_key_pem if signer else None
        resolved = await asyncio.gather(
            *(
                asyncio.to_thread(
                    federation_service.resolve_actor_inbox,
                    actor_url,
                    config,
                    key_id=key_id,
                    private_key_pem=private_key_pem,
                )
                for actor_url in actor_urls
            )
        )
        inboxes.update(inbox for inbox in resolved if inbox)

    return inboxes


async def fan_out_activity(
    session: AsyncSession,
    activity: Activity,
    config: SonghiveConfig,
    *,
    owner: Optional[User] = None,
    extra_inboxes: Iterable[str] = (),
) -> int:
    """
    Deliver ``activity.payload`` to its audience, recording delivery targets.

    The inbox set is ``resolve_audience`` union ``extra_inboxes`` (for
    activity-type-specific recipients such as a liked object's author), minus
    inboxes already recorded in ``activity_targets``. Each new inbox gets an
    ``ActivityTarget`` row: ``sent`` when the Celery delivery is enqueued,
    ``failed`` (with ``last_error``) when enqueueing raises, and ``skipped``
    for blocked or non-allowed instances. ``pending`` remains the default
    state for rows staged without a dispatch attempt.

    Returns the number of newly enqueued deliveries. The function no-ops —
    recording nothing — for remote or retracted activities, missing payloads,
    non-federating visibilities, disabled federation, and owners without a
    signing key. Flushes without committing; the caller owns the
    transaction.
    """
    if (
        activity.source_type != "local"
        or activity.deleted_at is not None
        or not activity.payload
        or not config.federation.enabled
        or not config.federation.instance_domain
    ):
        return 0
    try:
        federates = Visibility.federates(Visibility(activity.visibility))
    except ValueError:
        return 0
    if not federates:
        return 0

    if owner is None and activity.owner_user_id:
        owner = await session.get(User, activity.owner_user_id)
    if owner is None or not owner.private_key_pem:
        return 0

    inboxes = {inbox for inbox in extra_inboxes if inbox}
    inboxes.update(await resolve_audience(session, activity, config, signer=owner))
    recorded = await session.execute(select(ActivityTarget.inbox_url).where(ActivityTarget.activity_id == activity.id))
    inboxes.difference_update(recorded.scalars().all())
    if not inboxes:
        return 0

    from ..tasks.federation import deliver_activity

    actor_key_id = f"{owner.actor_url or activity.source_actor}#main-key"
    now = datetime.now(timezone.utc)
    sent = 0
    for inbox in sorted(inboxes):
        target = ActivityTarget(activity_id=activity.id, inbox_url=inbox)
        session.add(target)
        if federation_service.is_domain_blocked(federation_service.extract_domain(inbox), config):
            target.state = "skipped"
            target.last_error = "blocked or non-allowed instance"
            continue
        target.attempts = 1
        target.last_attempt_at = now
        try:
            deliver_activity.delay(activity.payload, inbox, actor_key_id, owner.private_key_pem)  # type: ignore
        except Exception as e:
            target.state = "failed"
            target.last_error = f"{type(e).__name__}: {e}"
        else:
            target.state = "sent"
            sent += 1

    await session.flush()
    return sent


async def _fan_out_reaction_activity(
    session: AsyncSession,
    *,
    reaction: Activity,
    target: Activity,
    author: User,
    config: SonghiveConfig,
    timeout: float = 10.0,
) -> int:
    """
    Fan a ``Like``/``Announce`` out to its target's author and audience.

    Reacting to a remote activity additionally targets the remote author's
    inbox — resolved via ``services.federation.resolve_actor_inbox`` — on top
    of the reaction's own visibility audience. The resolution is skipped
    (and the reaction may still fan out to its own audience when it has one)
    when the target is local, federation is disabled, or the author has no
    signing key. Flushes without committing; the caller owns the
    transaction.
    """
    if not reaction.payload:
        return 0
    try:
        if not Visibility.federates(Visibility(reaction.visibility)):
            return 0
    except ValueError:
        return 0

    extra_inboxes: Set[str] = set()
    if (
        config.federation.enabled
        and config.federation.instance_domain
        and target.source_type == "remote"
        and author.actor_url
        and author.private_key_pem
    ):
        inbox = await asyncio.to_thread(
            federation_service.resolve_actor_inbox,
            target.source_actor,
            config,
            key_id=f"{author.actor_url}#main-key",
            private_key_pem=author.private_key_pem,
            timeout=timeout,
        )
        if inbox:
            extra_inboxes.add(inbox)

    return await fan_out_activity(session, reaction, config, owner=author, extra_inboxes=extra_inboxes)


async def fan_out_like_activity(
    session: AsyncSession,
    *,
    like: Activity,
    target: Activity,
    author: User,
    config: SonghiveConfig,
    timeout: float = 10.0,
) -> int:
    """
    Fan ``like`` out to the liked activity's author and its own audience.

    Liking a remote activity additionally targets the remote author's inbox —
    resolved via ``services.federation.resolve_actor_inbox`` — on top of the
    like's own visibility audience. The resolution is skipped (and the like
    may still fan out to its own audience when it has one) when the target is
    local, federation is disabled, or the author has no signing key.
    Flushes without committing; the caller owns the transaction.
    """
    return await _fan_out_reaction_activity(
        session, reaction=like, target=target, author=author, config=config, timeout=timeout
    )


async def fan_out_boost_activity(
    session: AsyncSession,
    *,
    boost: Activity,
    target: Activity,
    author: User,
    config: SonghiveConfig,
    timeout: float = 10.0,
) -> int:
    """
    Fan ``boost`` out to the boosted activity's author and its own audience.

    Behaves exactly like :func:`fan_out_like_activity` for ``Announce``
    payloads. Flushes without committing; the caller owns the transaction.
    """
    return await _fan_out_reaction_activity(
        session, reaction=boost, target=target, author=author, config=config, timeout=timeout
    )


async def fan_out_unreaction_activity(
    session: AsyncSession,
    *,
    reaction: Activity,
    author: User,
    config: SonghiveConfig,
) -> None:
    """
    Fan an ``Undo`` of a ``Like``/``Announce`` out to its delivered inboxes.

    The ``Undo`` wraps the originally federated reaction payload, so its
    ``to``/``cc`` audience is inherited from the reaction itself, and is
    delivered to exactly the inboxes recorded as ``sent`` for the reaction
    — including the remote target author's inbox when the reaction was
    federated to it. No-ops when federation is disabled, the reaction is
    remote, its payload is missing, or it never reached any inbox.
    Enqueues delivery tasks; the caller owns the transaction.
    """
    if not config.federation.enabled or reaction.source_type != "local" or not isinstance(reaction.payload, dict):
        return

    from ..federation.activities import create_undo_activity
    from .deletion import enqueue_activity_delivery, get_activity_unpublish_info

    info = await get_activity_unpublish_info(session, reaction)
    if not info.inboxes:
        return
    payload = create_undo_activity(
        info.actor_url,
        reaction.payload,
        activity_id=f"{reaction.source_id}#undo",
    )
    enqueue_activity_delivery(info, author, payload)


async def fan_out_activity_update(
    session: AsyncSession,
    activity: Activity,
    config: SonghiveConfig,
    *,
    owner: Optional[User] = None,
) -> int:
    """
    Deliver an ``Update`` for ``activity``'s edited object to its audience.

    The ``Update`` — built by
    ``federation.activities.create_object_update_activity`` — embeds the
    full updated object document (the dict ``object`` of the stored
    ``payload``, already rebuilt by :func:`update_activity`) and is sent to
    every inbox recorded as ``sent`` in ``activity_targets`` so remote
    instances refresh their cached copy. Afterwards the stored payload is
    re-delivered through :func:`fan_out_activity`, which dedupes on recorded
    targets: any inbox first reached by the edit (e.g. an actor newly added
    to the mentions) receives the original ``Create`` carrying the updated
    object rather than an ``Update`` for an object it has never seen.

    Returns the number of ``Update`` deliveries enqueued. The function
    no-ops for remote or retracted activities, payloads without an embedded
    dict ``object`` (e.g. a ``Like``, whose object is a bare id and carries
    no editable content), non-federating visibilities, disabled federation,
    and owners without a signing key. Flushes without committing; the caller
    owns the transaction.
    """
    if (
        activity.source_type != "local"
        or activity.deleted_at is not None
        or not config.federation.enabled
        or not config.federation.instance_domain
    ):
        return 0
    try:
        visibility = Visibility(activity.visibility)
    except ValueError:
        return 0
    if not Visibility.federates(visibility):
        return 0

    payload = activity.payload
    object_doc = payload.get("object") if isinstance(payload, dict) else None
    if not isinstance(object_doc, dict):
        return 0

    if owner is None and activity.owner_user_id:
        owner = await session.get(User, activity.owner_user_id)
    if owner is None or not owner.private_key_pem:
        return 0

    from ..federation.activities import create_object_update_activity
    from .deletion import enqueue_activity_delivery, get_activity_unpublish_info

    info = await get_activity_unpublish_info(session, activity)
    sent = 0
    if info.inboxes:
        mention_rows = await session.execute(
            select(ActivityMention.actor_url).where(
                ActivityMention.activity_id == activity.id,
                ActivityMention.actor_url.is_not(None),
            )
        )
        mention_actor_urls: List[str] = [url for url in mention_rows.scalars().all() if url]  # type: ignore
        update = create_object_update_activity(
            info.actor_url,
            {**object_doc, "id": object_doc.get("id") or activity.source_id},
            visibility,
            mention_actor_urls=mention_actor_urls,
        )
        enqueue_activity_delivery(info, owner, update)
        sent = len(info.inboxes)

    await fan_out_activity(session, activity, config, owner=owner)
    return sent


# Loose BCP-47 shape for status language tags (``en``, ``en-US``, ``zh-Hant``).
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{1,8}(-[A-Za-z0-9]{1,8})*$")

# Per-category cap on status attachments, mirroring common fediverse limits.
_MAX_STATUS_ATTACHMENTS = 4


def _validate_status_language(language: Optional[str]) -> Optional[str]:
    """Normalize and validate a status's BCP-47-ish language tag."""
    if language is None:
        return None
    value = language.strip()
    if not value:
        return None
    if len(value) > 35 or not _LANGUAGE_RE.match(value):
        raise HTTPException(422, detail=f"Invalid language: {language}")
    return value


def _status_file_visibility(visibility: Visibility) -> Visibility:
    """Return the visibility a status's file attachments must have.

    Statuses federating to remote audiences (``public``/``followers``) need
    publicly fetchable media, ``local`` statuses keep files instance-local,
    and ``mentioned``/``private`` statuses keep them private — ``mentioned``
    additionally grants each mentioned local user access to the file.
    """
    if visibility in (Visibility.PUBLIC, Visibility.FOLLOWERS):
        return Visibility.PUBLIC
    if visibility == Visibility.LOCAL:
        return Visibility.LOCAL
    return Visibility.PRIVATE


async def _resolve_status_media(
    session: AsyncSession,
    author: User,
    media_ids: Iterable[str],
    visibility: Visibility,
    mentioned_user_ids: Iterable[str],
) -> List[StoredFile]:
    """
    Resolve status attachment files, enforcing ownership and visibility.

    Files must be owned by ``author`` (unless they are an admin); unknown
    ids raise ``HTTPException`` 404.  Each file's visibility is escalated —
    never downgraded — to what the status's audience requires, and
    ``mentioned``-visibility statuses additionally create ``file`` share
    grants for every mentioned local user so they can fetch the media.
    """
    required = _status_file_visibility(visibility)
    grantees = [uid for uid in dict.fromkeys(mentioned_user_ids) if uid != author.id]
    files: List[StoredFile] = []
    for file_id in dict.fromkeys(media_ids):
        stored_file = await session.get(StoredFile, file_id)
        if stored_file is None or (stored_file.owner_id != author.id and not author.is_admin):
            raise HTTPException(404, detail="Attachment not found")
        try:
            current = Visibility(stored_file.visibility)
        except ValueError:
            current = Visibility.PRIVATE
        if Visibility.rank(current) < Visibility.rank(required):
            stored_file.visibility = required.value
        if required == Visibility.PRIVATE and visibility == Visibility.MENTIONED:
            for user_id in grantees:
                await create_share_grant(
                    session,
                    "file",
                    str(stored_file.id),
                    user_id,
                    created_by=str(author.id),
                )
        files.append(stored_file)
    return files


async def _resolve_status_tracks(
    session: AsyncSession,
    author: User,
    track_ids: Iterable[str],
) -> List[Track]:
    """
    Resolve status attachment tracks, enforcing view-level access.

    Every id must point at a track ``author`` may access (``acl.can_access``)
    — unknown or inaccessible ids both raise ``HTTPException`` 404 so track
    existence is not leaked.
    """
    tracks: List[Track] = []
    for track_id in dict.fromkeys(track_ids):
        track = await session.get(Track, track_id)
        if track is None or not await can_access(session, author, "track", track_id):
            raise HTTPException(404, detail="Track not found")
        tracks.append(track)
    return tracks


async def create_status(
    session: AsyncSession,
    *,
    author: User,
    config: SonghiveConfig,
    status_text: Optional[str] = None,
    content_type: str = CONTENT_TYPE_MARKDOWN,
    visibility: "Visibility | str" = Visibility.PUBLIC,
    language: Optional[str] = None,
    media_ids: Optional[Iterable[str]] = None,
    track_ids: Optional[Iterable[str]] = None,
) -> Activity:
    """
    Create a standalone status as a ``create`` activity on the author's
    ``user`` entity.

    The status is recorded as a local ``Create(Note)`` activity attached to
    ``(user, author.id)`` — it federates through :func:`fan_out_activity`
    when federation is enabled and ``visibility`` federates, and stays
    local otherwise. Unlike :func:`record_track_publication` it never
    returns ``None`` and never requires federation to be configured.

    ``status_text`` is the raw source text, interpreted as Markdown when
    ``content_type`` is ``text/markdown`` (the default) and as escaped
    plain text otherwise; the mention pipeline renders safe ``content``
    HTML, resolves ``@handle`` mentions into ``activity_mentions`` rows,
    and builds the object's ``Mention``/``Hashtag`` tags. ``language`` is a
    BCP-47 tag persisted on the activity and mirrored into the object's
    ``contentMap``.

    ``media_ids`` attach uploaded files (see :func:`_resolve_status_media`
    for ownership and visibility handling) and ``track_ids`` attach hosted
    tracks the author may access — both are serialized into the object's
    ``attachment`` list. Each category is capped at
    ``_MAX_STATUS_ATTACHMENTS`` entries.

    Raises ``HTTPException`` 422 for an empty status, an invalid
    ``content_type``/``visibility``/``language``, or an over-limit
    attachment list; 404 for unknown or inaccessible media/tracks. Flushes
    without committing; the caller owns the transaction.
    """
    from ..federation.activities import create_status_activity
    from ..federation.serializers import stored_file_to_attachment, track_to_attachment

    text = (status_text or "").strip()
    media_ids = list(media_ids or [])
    track_ids = list(track_ids or [])
    if not text and not media_ids and not track_ids:
        raise HTTPException(422, detail="Status must not be empty")
    if len(set(media_ids)) > _MAX_STATUS_ATTACHMENTS or len(set(track_ids)) > _MAX_STATUS_ATTACHMENTS:
        raise HTTPException(422, detail="Too many attachments")
    if content_type not in STATUS_CONTENT_TYPES:
        raise HTTPException(422, detail=f"Invalid content_type: {content_type}")
    try:
        activity_visibility = Visibility(visibility)
    except ValueError:
        raise HTTPException(422, detail=f"Invalid visibility: {visibility}")
    language = _validate_status_language(language)

    processed = await process_mentions(session, text, config, content_type=content_type)
    mentioned_user_ids = [m.user_id for m in processed.mentions if m.user_id]
    files = await _resolve_status_media(session, author, media_ids, activity_visibility, mentioned_user_ids)
    tracks = await _resolve_status_tracks(session, author, track_ids)

    domain = (config.federation.instance_domain or "").strip()
    actor_url = _local_actor_url(author)
    attachments = [stored_file_to_attachment(stored, domain) for stored in files]
    for track in tracks:
        artist = await session.get(Artist, track.artist_id) if track.artist_id else None
        audio_object_id = f"{actor_url}/objects/{track.federation_object_id}" if track.federation_object_id else None
        attachments.append(track_to_attachment(track, artist, domain, audio_object_id=audio_object_id))

    object_uuid = str(uuid.uuid4())
    object_id = f"{actor_url}/objects/{object_uuid}"
    note: dict = {
        "type": "Note",
        "id": object_id,
        "url": object_id,
        "attributedTo": actor_url,
        "published": datetime.now(timezone.utc).isoformat(),
    }
    if processed.html:
        note["content"] = processed.html
        if language:
            note["contentMap"] = {language: processed.html}
    if attachments:
        note["attachment"] = attachments
    if processed.tags:
        note["tag"] = processed.tags

    mention_actor_urls: List[str] = [m.actor_url for m in processed.mentions if m.actor_url]  # type: ignore
    payload = create_status_activity(
        actor_url,
        note,
        activity_visibility,
        mention_actor_urls=mention_actor_urls,
    )

    activity = Activity(
        entity_type="user",
        entity_id=str(author.id),
        activity_type="create",
        source_type="local",
        source_actor=actor_url,
        source_id=object_id,
        local_object_id=object_uuid,
        owner_user_id=author.id,
        visibility=activity_visibility.value,
        content=processed.html or None,
        content_source=text or None,
        content_type=content_type,
        language=language,
        payload=payload,
    )
    session.add(activity)
    await session.flush()

    for mention in processed.mentions:
        session.add(ActivityMention(activity_id=activity.id, **mention.as_dict()))
    await session.flush()

    await _sync_activity_tags(session, activity, processed.tag_names)
    await _notify_status_mentions(session, activity=activity, author=author, mentions=processed.mentions)

    try:
        await fan_out_activity(session, activity, config, owner=author)
    except Exception as e:
        # Fan-out is best-effort: a broker or resolution failure must not
        # fail the post itself — the activity row still records it.
        logger.exception("Failed to fan out status for user %s: %s: %s", author.id, type(e), e)
    return activity


async def _notify_status_mentions(
    session: AsyncSession,
    *,
    activity: Activity,
    author: User,
    mentions: Iterable[Any],
    skip_user_ids: Iterable[str] = (),
) -> None:
    """Notify local users mentioned by a status, never failing the post.

    Mirrors the federated mention inbox path: the notification payload
    carries the author's identity plus a snapshot of the status object so
    clients can render the mention without re-fetching it. Self-mentions,
    ``skip_user_ids`` (e.g. a reply's already-notified target owner), and
    notification failures produce nothing.
    """
    from ..federation.notifications import _note_snapshot
    from . import notifications as notifications_service

    skipped = {str(uid) for uid in skip_user_ids}
    note = activity.payload.get("object") if isinstance(activity.payload, dict) else {}
    snapshot = _note_snapshot(note) if isinstance(note, dict) else {}
    # The status's own link fields let clients render its real card and
    # link to its page rather than its object URL.
    object_fields = await _entity_link_fields(session, activity)
    for mention in mentions:
        if not mention.user_id or mention.user_id == author.id or str(mention.user_id) in skipped:
            continue
        try:
            await notifications_service.create_notification(
                session,
                user_id=mention.user_id,
                type=NotificationType.MENTION,
                actor_url=activity.source_actor,
                source_url=activity.source_id,
                payload={
                    "actor_name": author.display_name or author.username,
                    "actor_avatar_url": author.avatar_url,
                    **snapshot,
                    **object_fields,
                },
            )
        except Exception as exc:
            logger.warning("Failed to create mention notification for activity %s: %s", activity.id, exc)


class _TrackPublicationKwargs(TypedDict):
    """Keyword arguments shared by ``create_note_activity``/``create_audio_activity``."""

    actor_url: str
    track: Track
    artist: Artist
    domain: str
    description: Optional[str]
    ap_object_id: Optional[str]
    visibility: "Visibility | str"
    mention_actor_urls: Iterable[str]


async def record_track_publication(
    session: AsyncSession,
    *,
    track: Track,
    artist: Optional[Artist],
    owner: Optional[User],
    config: SonghiveConfig,
    status: Optional[str] = None,
    visibility: "Visibility | str" = Visibility.PUBLIC,
    object_type: str = "audio",
    content_type: Optional[str] = None,
    language: Optional[str] = None,
    media_ids: Optional[Iterable[str]] = None,
) -> Optional[Activity]:
    """
    Record and fan out a ``create`` activity for a fediverse track share.

    ``object_type`` selects the federated object shape:

    - ``"audio"`` publishes the track as an ``Audio`` object built from the
      track's current ``federation_object_id`` (the caller must mint it
      first, and commit the session so remote fetches of
      ``{actor_url}/objects/{id}`` can resolve). ``Audio`` is the canonical
      track publication, but it is a "converted" type on Mastodon — the
      remote status shows only the track name and link, not ``content``.
    - ``"note"`` publishes the track as a ``Note`` whose ``content`` is the
      post body (which Mastodon does render) and whose ``attachment``
      embeds the stream as an ``Audio``-typed media object. A fresh object
      id is minted per share — the share is a distinct object, not the
      track's canonical ``Audio`` — and the attachment's ``id`` points at
      the published ``Audio`` object when the track has one.

    The payload is stored as a local ``create`` activity attached to the
    track — making the publication visible in the entity's activity feed —
    and delivered through :func:`fan_out_activity`, which records one
    ``ActivityTarget`` per inbox so the publication can later be retracted
    with a ``Delete(Tombstone)``.

    ``status`` is an optional one-off post text used as the object's
    ``content`` instead of the track's stored ``description``; it is never
    persisted on the track itself. When given, its ``@handle`` mentions are
    resolved through the ``process_mentions`` pipeline: mention-aware HTML
    replaces the rendered content, ``Mention`` tags are merged into the
    object's ``tag`` list, and ``activity_mentions`` rows are persisted so
    ``resolve_audience`` can reach remote mentioned actors. ``content_type``
    selects the source-text renderer (``text/markdown`` or the default
    ``text/plain``) and ``language`` a BCP-47 tag mirrored into the object's
    ``contentMap``; both are persisted on the activity. ``media_ids`` attach
    uploaded files to the post object (see ``_resolve_status_media`` for
    ownership and visibility handling).

    ``visibility`` selects the post's audience (``public`` by default):
    ``public`` and ``followers`` reach the owner's follower inboxes plus
    remote mentioned actors, ``mentioned`` reaches only the mentioned actors
    (a ``status`` carrying ``@handle``s is required for it to deliver
    anywhere), and ``private``/``local`` record the activity without
    federating it. The ``to``/``cc`` addressing derived from the visibility
    is applied to both the ``Create`` envelope and the embedded object.

    Returns ``None`` — recording nothing — when federation is disabled, the
    track is not public, the artist is missing, the owner has no actor
    credentials, or (for ``"audio"``) no ``federation_object_id`` is set.
    Raises ``HTTPException`` 422 for an invalid ``visibility`` or
    ``object_type`` value. Flushes without committing; the caller owns the
    transaction.
    """
    from ..federation._common import get_track_url
    from ..federation.activities import create_audio_activity, create_note_activity
    from ..federation.serializers import normalize_post_content, stored_file_to_attachment

    if object_type not in ("audio", "note"):
        raise HTTPException(422, detail=f'Invalid object_type: {object_type}. Valid types: "audio", "note"')
    if content_type is not None and content_type not in STATUS_CONTENT_TYPES:
        raise HTTPException(422, detail=f"Invalid content_type: {content_type}")
    language = _validate_status_language(language)
    if not config.federation.enabled or not config.federation.instance_domain:
        return None
    if not track or track.visibility != Visibility.PUBLIC.value:
        return None
    if not artist or not owner or not owner.actor_url or not owner.private_key_pem:
        return None
    if object_type == "audio" and not track.federation_object_id:
        return None

    try:
        activity_visibility = Visibility(visibility)
    except ValueError:
        raise HTTPException(422, detail=f"Invalid visibility: {visibility}")
    VisibilityRules.enforce_activity_visibility(activity_visibility, _entity_visibility(track))

    processed = (
        await process_mentions(session, status, config, content_type=content_type or "text/plain") if status else None
    )
    mention_actor_urls: List[str] = (  # type: ignore
        [m.actor_url for m in processed.mentions if m.actor_url] if processed else []
    )
    media_files = await _resolve_status_media(
        session,
        owner,
        media_ids or [],
        activity_visibility,
        [m.user_id for m in processed.mentions if m.user_id] if processed else [],
    )

    if object_type == "audio":
        object_uuid = str(track.federation_object_id)
        audio_object_id = None
    else:
        object_uuid = str(uuid.uuid4())
        audio_object_id = (
            f"{owner.actor_url}/objects/{track.federation_object_id}" if track.federation_object_id else None
        )
    object_id = f"{owner.actor_url}/objects/{object_uuid}"
    args: _TrackPublicationKwargs = {
        "actor_url": owner.actor_url,
        "track": track,
        "artist": artist,
        "domain": config.federation.instance_domain,
        "description": status,
        "ap_object_id": object_id,
        "visibility": activity_visibility,
        "mention_actor_urls": mention_actor_urls,
    }

    payload = (
        create_note_activity(**args, audio_object_id=audio_object_id)
        if object_type == "note"
        else create_audio_activity(**args)
    )

    if not payload:
        return None

    obj = payload.get("object")
    if isinstance(obj, dict):
        if processed is not None:
            if processed.html:
                obj["content"] = processed.html
            normalize_post_content(obj, link_href=get_track_url(track, config.federation.instance_domain))
            if processed.tags:
                tags = obj.setdefault("tag", [])
                seen = {str(tag.get("name", "")).lower() for tag in tags if isinstance(tag, dict)}
                tags.extend(tag for tag in processed.tags if str(tag.get("name", "")).lower() not in seen)
        if media_files:
            attachments = obj.setdefault("attachment", [])
            attachments.extend(
                stored_file_to_attachment(stored, config.federation.instance_domain) for stored in media_files
            )
        if language and obj.get("content"):
            obj["contentMap"] = {language: obj["content"]}

    activity = Activity(
        entity_type="track",
        entity_id=str(track.id),
        activity_type="create",
        source_type="local",
        source_actor=owner.actor_url,
        source_id=object_id,
        local_object_id=object_uuid,
        owner_user_id=owner.id,
        visibility=activity_visibility.value,
        content=obj.get("content") if isinstance(obj, dict) else None,
        content_source=status,
        content_type=content_type or "text/plain",
        language=language,
        payload=payload,
    )
    session.add(activity)
    await session.flush()

    if processed is not None:
        for mention in processed.mentions:
            session.add(ActivityMention(activity_id=activity.id, **mention.as_dict()))
        await session.flush()

    tag_names = processed.tag_names if processed is not None else []
    if not tag_names and status is None:
        tag_names = _extract_hashtags(getattr(track, "description", None) or "")
    if not tag_names and isinstance(obj, dict) and obj.get("content"):
        tag_names = _extract_hashtags(obj["content"])
    await _sync_activity_tags(session, activity, tag_names)

    try:
        await fan_out_activity(session, activity, config, owner=owner)
    except Exception as e:
        # Fan-out is best-effort: a broker or resolution failure must not
        # fail the publication itself — the activity row still records it.
        logger.exception("Failed to fan out publication for track %s: %s: %s", track.id, type(e), e)
    return activity


async def retract_track_publications(
    session: AsyncSession,
    track: Track,
) -> int:
    """
    Soft-delete a track's live publication activities, with tombstones.

    Used when the track leaves the fediverse (e.g. a public → non-public
    visibility transition) so stale ``create`` rows stop appearing in the
    feed and stop serving their stored payload from the object-dereference
    route. Every publication — the canonical ``Audio`` and any ``Note``
    shares, which mint their own object ids — is retracted through
    :func:`retract_activity` so a ``Delete(Tombstone)`` for its own
    ``source_id`` is delivered to the inboxes that received it. Returns the
    number of retracted rows. Flushes without committing; the caller owns
    the transaction.
    """
    rows = (
        (
            await session.execute(
                select(Activity).where(
                    Activity.entity_type == "track",
                    Activity.entity_id == str(track.id),
                    Activity.activity_type == "create",
                    Activity.source_type == "local",
                    Activity.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await retract_activity(session, row)
    return len(rows)


async def sync_track_publications(
    session: AsyncSession,
    track: Track,
    *,
    config: SonghiveConfig,
) -> int:
    """
    Re-sync the stored ``Create`` objects after a track edit.

    Finds the track's live local ``create`` activities and rebuilds each
    payload's embedded ``Audio`` or ``Note`` object — dispatched on the
    stored object's ``type`` — from the track's current metadata
    (title, artist, description, genres, duration, URLs) so remote
    instances see the edits. The object id is kept stable: the fanned-out
    ``Update`` replaces the remote copy rather than re-creating it. When a
    publication was recorded with a one-off ``status`` (stored as
    ``content_source``), that text is re-applied as the object's content so
    a metadata edit does not clobber the intentional post body. The
    activity's ``content`` column is refreshed to match the rebuilt object.
    The rebuilt object and the stored ``Create`` envelope also get their
    ``to``/``cc`` re-derived from the activity's current visibility and
    mention rows (via ``activity_audience``), and the resolved ``Mention``
    tags are re-attached, so ``followers``/``mentioned`` publications keep
    their addressing across metadata edits.

    Each re-synced activity then goes through
    :func:`fan_out_activity_update`, which delivers an ``Update`` to the
    inboxes that already received it.

    Returns the number of re-synced activities. No-ops — returning 0 —
    when federation is disabled, the track is not public or has no artist,
    or no live local ``create`` activities exist. Flushes without
    committing; the caller owns the transaction.
    """
    from ..federation import get_track_url
    from ..federation.activities import activity_audience
    from ..federation.serializers import (
        set_post_content,
        track_to_audio_object,
        track_to_note_object,
    )

    if not config.federation.enabled or not config.federation.instance_domain:
        return 0
    if not track or track.visibility != Visibility.PUBLIC.value or not track.artist_id:
        return 0

    rows = (
        (
            await session.execute(
                select(Activity).where(
                    Activity.entity_type == "track",
                    Activity.entity_id == str(track.id),
                    Activity.activity_type == "create",
                    Activity.source_type == "local",
                    Activity.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0

    artist = await session.get(Artist, track.artist_id)
    if artist is None:
        return 0

    domain = config.federation.instance_domain
    now = datetime.now(timezone.utc).isoformat()
    synced: List[Activity] = []
    for activity in rows:
        payload = activity.payload
        if not isinstance(payload, dict):
            continue
        object_doc = payload.get("object")
        if not isinstance(object_doc, dict):
            continue

        if object_doc.get("type") == "Note":
            # ``Note`` shares keep their own object id and stamp the
            # attachment with the track's current ``Audio`` object id; the
            # stored ``published`` is preserved so the remote post keeps its
            # original date.
            published = None
            stored_published = object_doc.get("published")
            if isinstance(stored_published, str):
                try:
                    published = datetime.fromisoformat(stored_published)
                except ValueError:
                    published = None
            rebuilt = track_to_note_object(
                track,
                artist,
                domain,
                actor_url=activity.source_actor,
                ap_object_id=object_doc.get("id") or activity.source_id,
                audio_object_id=(
                    f"{activity.source_actor}/objects/{track.federation_object_id}"
                    if track.federation_object_id
                    else None
                ),
                published=published,
            )
        else:
            rebuilt = track_to_audio_object(
                track,
                artist,
                domain,
                actor_url=activity.source_actor,
                ap_object_id=object_doc.get("id") or activity.source_id,
            )
        if rebuilt is None:
            continue
        # A publication recorded with a one-off status keeps that text as
        # the post body; the track description only fills the content when
        # no status was given.
        if activity.content_source:
            set_post_content(rebuilt, activity.content_source, domain, link_href=get_track_url(track, domain))
        # Re-apply the activity's current audience (visibility may have
        # changed since publication) and re-attach its Mention tags.
        mention_rows = await session.execute(
            select(ActivityMention).where(
                ActivityMention.activity_id == activity.id,
                ActivityMention.actor_url.is_not(None),
            )
        )
        mentions = list(mention_rows.scalars().all())
        mention_actor_urls: List[str] = [m.actor_url for m in mentions if m.actor_url]  # type: ignore
        to, cc = activity_audience(activity.visibility, activity.source_actor, mention_actor_urls)
        rebuilt["to"] = to
        rebuilt["cc"] = cc
        payload["to"] = to
        payload["cc"] = cc
        if mentions:
            tags = rebuilt.setdefault("tag", [])
            seen = {str(tag.get("name", "")).lower() for tag in tags if isinstance(tag, dict)}
            for mention in mentions:
                if mention.actor_url and mention.handle.lower() not in seen:
                    tags.append({"type": "Mention", "href": mention.actor_url, "name": mention.handle})
                    seen.add(mention.handle.lower())
        rebuilt["updated"] = now
        payload["object"] = rebuilt
        flag_modified(activity, "payload")
        activity.content = rebuilt.get("content")
        await _refresh_notifications_for_object(session, activity.source_id, rebuilt)

        source_text = activity.content_source or getattr(track, "description", None) or ""
        if not source_text and rebuilt.get("content"):
            source_text = rebuilt["content"]
        await _sync_activity_tags(session, activity, _extract_hashtags(source_text))

        synced.append(activity)

    if not synced:
        return 0
    await session.flush()

    for activity in synced:
        try:
            await fan_out_activity_update(session, activity, config)
        except Exception as e:
            # Fan-out is best-effort: a broker or resolution failure must
            # not fail the track edit itself — the rebuilt payload remains
            # stored and is served by the object-dereference route.
            logger.exception(
                "Failed to fan out update for activity %s: %s: %s",
                activity.id,
                type(e),
                e,
            )
    return len(synced)


async def retract_activity(session: AsyncSession, activity: Activity) -> None:
    """
    Retract a single activity.

    Local activities are soft-deleted (``deleted_at``) and a retraction is
    enqueued for every inbox the activity was previously delivered to
    (``sent`` targets only): ``Undo(Like)``/``Undo(Announce)`` for
    reactions — ``Delete`` is not the ActivityPub way to retract them —
    and ``Delete(Tombstone)`` for everything else, mirroring
    :func:`deletion.cascade_delete_entity` for a single row. Remote
    activities are soft-deleted without fan-out — the remote instance owns
    retraction, and keeping the row stops the stored Pubby interaction
    from resurfacing the reply. Already-retracted activities are a no-op.
    Flushes without committing; the caller owns the transaction.
    """
    from ..federation.activities import create_tombstone_delete_activity, create_undo_activity
    from . import notifications as notifications_service
    from .deletion import enqueue_activity_delivery, get_activity_unpublish_info

    if activity.deleted_at is not None:
        return

    # Notifications that point at this activity (likes/boosts on it,
    # replies and quotes targeting it) are no longer applicable.
    if activity.source_id:
        await notifications_service.retract_notifications_referencing(session, [activity.source_id])

    if activity.source_type != "local":
        activity.deleted_at = datetime.now(timezone.utc)
        await session.flush()
        return

    info = await get_activity_unpublish_info(session, activity)
    activity.deleted_at = datetime.now(timezone.utc)
    if info.inboxes:
        owner = await session.get(User, activity.owner_user_id) if activity.owner_user_id else None
        if activity.activity_type in ("like", "announce") and isinstance(activity.payload, dict):
            payload = create_undo_activity(
                info.actor_url,
                activity.payload,
                activity_id=f"{info.source_id}#undo",
            )
        else:
            payload = create_tombstone_delete_activity(info.actor_url, info.source_id)
        enqueue_activity_delivery(info, owner, payload)
    await session.flush()
