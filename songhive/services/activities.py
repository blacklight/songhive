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
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple, Type

from fastapi import HTTPException
from pubby.content import set_object_content
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, delete, exists, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..config.schema import SonghiveConfig
from ..federation.storage import create_activitypub_storage
from ..models._enums import Visibility
from ..models.activity import (
    ACTIVITY_ENTITY_TYPES,
    ACTIVITY_TYPES,
    Activity,
    ActivityMention,
    ActivityTarget,
)
from ..models.album import Album
from ..models.artist import Artist
from ..models.library import Library
from ..models.playlist import Playlist
from ..models.track import Track
from ..models.user import User
from . import federation as federation_service
from .acl import can_access, can_manage
from .mentions import hashtag_url_factory, process_mentions

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVITY_ENTITY_TYPES",
    "ACTIVITY_TYPES",
    "ActivityCreateParams",
    "VisibilityRules",
    "can_view_activity",
    "create_local_activity",
    "fan_out_activity",
    "fan_out_activity_update",
    "fan_out_like_activity",
    "like_activity",
    "list_activities",
    "resolve_audience",
    "resolve_entity",
    "resolve_source_actor_profiles",
    "sync_track_publications",
    "update_activity",
]

_ENTITY_MODELS: Dict[str, Type[Any]] = {
    "track": Track,
    "album": Album,
    "artist": Artist,
    "playlist": Playlist,
    "library": Library,
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
    requires the activity owner, an admin, or a user named in the activity's
    mentions; ``private`` is limited to the owner and admins.  Retracted
    (soft-deleted) activities are never viewable.
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

    if user.is_admin or (activity.owner_user_id is not None and str(activity.owner_user_id) == str(user.id)):
        return True

    # TODO: Once support for remote users is added, Visibility.LOCAL access should imply user.is_local.
    # TODO: Once proper storage for the followers graph is implemented ,that should be checked against
    #  Visibility.FOLLOWERS access
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


def _activity_visibility_filter(user: Optional[User]):
    """
    Return a WHERE clause applying per-activity visibility for list queries.

    Mirrors ``can_view_activity`` minus the entity-access check — the caller
    authorizes the containing entity once for the whole page: ``public``
    activities are visible to everyone, ``local`` and ``followers`` to
    authenticated users, ``mentioned`` to the users they name, and every
    visibility to the activity's owner and to admins.
    """
    if user is not None and user.is_admin:
        return true()

    conditions = [Activity.visibility == Visibility.PUBLIC.value]
    if user is not None:
        conditions.append(Activity.owner_user_id == user.id)
        conditions.append(Activity.visibility.in_([Visibility.LOCAL.value, Visibility.FOLLOWERS.value]))
        conditions.append(
            and_(
                Activity.visibility == Visibility.MENTIONED.value,
                exists().where(
                    ActivityMention.activity_id == Activity.id,
                    ActivityMention.user_id == user.id,
                ),
            )
        )
    return or_(*conditions)


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

            mention_actor_urls = [m.actor_url for m in activity.mentions if m.actor_url]
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
    in_reply_to_activity_id: Optional[str] = None,
    mentions: Optional[List[dict]] = None,
    payload: Optional[dict] = None,
    require_manage: bool = True,
) -> Activity:
    """Create a local activity attached to an entity.

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
        content_type="text/markdown" if content_source else "text/plain",
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
    return activity


async def update_activity(
    session: AsyncSession,
    activity: Activity,
    *,
    content_source: str,
    config: SonghiveConfig,
) -> Activity:
    """Update an activity's content, re-running the mention pipeline.

    ``content_source`` is the new raw text: ``process_mentions`` re-resolves
    its ``@handle`` mentions and re-renders safe HTML, which replaces the
    stored ``content``/``content_source``/``content_type`` columns and the
    activity's ``activity_mentions`` rows.  When the stored ``payload``
    embeds a dict ``object`` (e.g. a ``Create`` activity's object), its
    ``content`` and ``tag`` are rebuilt too: ``pubby.set_object_content``
    merges hashtag tags while preserving pre-existing tags, the
    mention-aware pipeline rendering wins for ``content``, and the
    pipeline's ``Mention`` tags are merged in. The object is also stamped
    with ``updated`` so served documents and remote copies can surface the
    edit. Remote propagation of the edit is layered on top by
    :func:`fan_out_activity_update`, which the caller invokes separately so
    a pending visibility change can be applied first.

    Raises ``HTTPException`` 404 when the activity has been retracted.
    Flushes without committing; the caller owns the transaction.
    """
    if activity.deleted_at is not None:
        raise HTTPException(404, detail="Activity not found")

    processed = await process_mentions(session, content_source, config)

    activity.content = processed.html or None
    activity.content_source = content_source
    activity.content_type = "text/markdown" if content_source else "text/plain"

    await session.execute(delete(ActivityMention).where(ActivityMention.activity_id == activity.id))
    for mention in processed.mentions:
        session.add(ActivityMention(activity_id=activity.id, **mention.as_dict()))
    session.expire(activity, ["mentions"])

    payload = activity.payload
    if isinstance(payload, dict) and isinstance(payload.get("object"), dict):
        obj = payload["object"]
        domain = (config.federation.instance_domain or "").strip()
        set_object_content(obj, content_source, hashtag_url_factory(domain))
        if processed.html:
            obj["content"] = processed.html
        else:
            obj.pop("content", None)
        from ..federation.serializers import mirror_content_to_summary

        mirror_content_to_summary(obj)
        if processed.tags:
            tags = obj.setdefault("tag", [])
            seen = {str(tag.get("name", "")).lower() for tag in tags if isinstance(tag, dict)}
            tags.extend(tag for tag in processed.tags if str(tag.get("name", "")).lower() not in seen)
        obj["updated"] = datetime.now(timezone.utc).isoformat()
        flag_modified(activity, "payload")

    await session.flush()
    return activity


async def like_activity(
    session: AsyncSession,
    *,
    activity: Activity,
    author: User,
) -> Activity:
    """Record ``author``'s like of ``activity``.

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
    existing = await session.execute(
        select(Activity.id)
        .where(
            Activity.entity_type == activity.entity_type,
            Activity.entity_id == activity.entity_id,
            Activity.activity_type == "like",
            Activity.source_actor == source_actor,
            Activity.in_reply_to_activity_id == activity.id,
            Activity.deleted_at.is_(None),
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
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
    return like


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


async def fan_out_like_activity(
    session: AsyncSession,
    *,
    like: Activity,
    target: Activity,
    author: User,
    config: SonghiveConfig,
    timeout: float = 10.0,
) -> int:
    """Fan ``like`` out to the liked activity's author and its own audience.

    Liking a remote activity additionally targets the remote author's inbox —
    resolved via ``services.federation.resolve_actor_inbox`` — on top of the
    like's own visibility audience. The resolution is skipped (and the like
    may still fan out to its own audience when it has one) when the target is
    local, federation is disabled, or the author has no signing key.
    Flushes without committing; the caller owns the transaction.
    """
    if not like.payload:
        return 0
    try:
        if not Visibility.federates(Visibility(like.visibility)):
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

    return await fan_out_activity(session, like, config, owner=author, extra_inboxes=extra_inboxes)


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


async def record_track_publication(
    session: AsyncSession,
    *,
    track: Track,
    artist: Optional[Artist],
    owner: Optional[User],
    config: SonghiveConfig,
    status: Optional[str] = None,
    visibility: "Visibility | str" = Visibility.PUBLIC,
) -> Optional[Activity]:
    """Record and fan out a ``create`` activity for a fediverse track share.

    Builds the ``Create(Audio)`` payload for the track's current
    ``federation_object_id`` (the caller must mint it first, and commit the
    session so remote fetches of ``{actor_url}/objects/{id}`` can resolve),
    stores it as a local ``create`` activity attached to the track — making
    the publication visible in the entity's activity feed — and delivers it
    through :func:`fan_out_activity`, which records one ``ActivityTarget``
    per inbox so the publication can later be retracted with a
    ``Delete(Tombstone)``.

    ``status`` is an optional one-off post text used as the object's
    ``content`` instead of the track's stored ``description``; it is never
    persisted on the track itself. When given, its ``@handle`` mentions are
    resolved through the ``process_mentions`` pipeline: mention-aware HTML
    replaces the rendered content, ``Mention`` tags are merged into the
    object's ``tag`` list, and ``activity_mentions`` rows are persisted so
    ``resolve_audience`` can reach remote mentioned actors.

    ``visibility`` selects the post's audience (``public`` by default):
    ``public`` and ``followers`` reach the owner's follower inboxes plus
    remote mentioned actors, ``mentioned`` reaches only the mentioned actors
    (a ``status`` carrying ``@handle``s is required for it to deliver
    anywhere), and ``private``/``local`` record the activity without
    federating it. The ``to``/``cc`` addressing derived from the visibility
    is applied to both the ``Create`` envelope and the embedded object.

    Returns ``None`` — recording nothing — when federation is disabled, the
    track is not public, the artist is missing, the owner has no actor
    credentials, or no ``federation_object_id`` is set. Raises
    ``HTTPException`` 422 for an invalid ``visibility`` value. Flushes
    without committing; the caller owns the transaction.
    """
    from ..federation.activities import create_audio_activity
    from ..federation.serializers import mirror_content_to_summary

    if not config.federation.enabled or not config.federation.instance_domain:
        return None
    if not track or track.visibility != Visibility.PUBLIC.value:
        return None
    if not artist or not owner or not owner.actor_url or not owner.private_key_pem:
        return None
    if not track.federation_object_id:
        return None

    try:
        activity_visibility = Visibility(visibility)
    except ValueError:
        raise HTTPException(422, detail=f"Invalid visibility: {visibility}")
    VisibilityRules.enforce_activity_visibility(activity_visibility, _entity_visibility(track))

    processed = await process_mentions(session, status, config) if status else None
    mention_actor_urls = [m.actor_url for m in processed.mentions if m.actor_url] if processed else []

    object_id = f"{owner.actor_url}/objects/{track.federation_object_id}"
    payload = create_audio_activity(
        actor_url=owner.actor_url,
        track=track,
        artist=artist,
        domain=config.federation.instance_domain,
        description=status,
        ap_object_id=object_id,
        visibility=activity_visibility,
        mention_actor_urls=mention_actor_urls,
    )
    if not payload:
        return None

    obj = payload.get("object")
    if processed is not None and isinstance(obj, dict):
        if processed.html:
            obj["content"] = processed.html
            mirror_content_to_summary(obj)
        if processed.tags:
            tags = obj.setdefault("tag", [])
            seen = {str(tag.get("name", "")).lower() for tag in tags if isinstance(tag, dict)}
            tags.extend(tag for tag in processed.tags if str(tag.get("name", "")).lower() not in seen)

    activity = Activity(
        entity_type="track",
        entity_id=str(track.id),
        activity_type="create",
        source_type="local",
        source_actor=owner.actor_url,
        source_id=object_id,
        local_object_id=str(track.federation_object_id),
        owner_user_id=owner.id,
        visibility=activity_visibility.value,
        content=obj.get("content") if isinstance(obj, dict) else None,
        content_source=status,
        content_type="text/markdown" if status else "text/plain",
        payload=payload,
    )
    session.add(activity)
    await session.flush()

    if processed is not None:
        for mention in processed.mentions:
            session.add(ActivityMention(activity_id=activity.id, **mention.as_dict()))
        await session.flush()
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
    *,
    object_id: Optional[str] = None,
) -> int:
    """Soft-delete a track's live publication activities.

    Used when the track leaves the fediverse (e.g. a public → non-public
    visibility transition) so stale ``create`` rows stop appearing in the
    feed and stop serving their stored payload from the object-dereference
    route. ``object_id`` narrows the retraction to the rows published with a
    specific ``federation_object_id``. No remote fan-out happens here — the
    caller is responsible for the track-level ``Delete(Tombstone)``.
    Returns the number of retracted rows. Flushes without committing; the
    caller owns the transaction.
    """
    stmt = select(Activity).where(
        Activity.entity_type == "track",
        Activity.entity_id == str(track.id),
        Activity.activity_type == "create",
        Activity.source_type == "local",
        Activity.deleted_at.is_(None),
    )
    if object_id:
        stmt = stmt.where(Activity.local_object_id == str(object_id))

    rows = (await session.execute(stmt)).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.deleted_at = now
    if rows:
        await session.flush()
    return len(rows)


async def sync_track_publications(
    session: AsyncSession,
    track: Track,
    *,
    config: SonghiveConfig,
) -> int:
    """
    Re-sync the stored ``Create(Audio)`` objects after a track edit.

    Finds the track's live local ``create`` activities and rebuilds each
    payload's embedded ``Audio`` object from the track's current metadata —
    title, artist, description, genres, duration, URLs — so remote
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
    from ..federation.activities import activity_audience
    from ..federation.serializers import set_audio_description, track_to_audio_object

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
            set_audio_description(rebuilt, activity.content_source, domain)
        # Re-apply the activity's current audience (visibility may have
        # changed since publication) and re-attach its Mention tags.
        mention_actor_urls = [m.actor_url for m in activity.mentions if m.actor_url]
        to, cc = activity_audience(activity.visibility, activity.source_actor, mention_actor_urls)
        rebuilt["to"] = to
        rebuilt["cc"] = cc
        payload["to"] = to
        payload["cc"] = cc
        if activity.mentions:
            tags = rebuilt.setdefault("tag", [])
            seen = {str(tag.get("name", "")).lower() for tag in tags if isinstance(tag, dict)}
            for mention in activity.mentions:
                if mention.actor_url and mention.handle.lower() not in seen:
                    tags.append({"type": "Mention", "href": mention.actor_url, "name": mention.handle})
                    seen.add(mention.handle.lower())
        rebuilt["updated"] = now
        payload["object"] = rebuilt
        flag_modified(activity, "payload")
        activity.content = rebuilt.get("content")
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
    """Retract a single activity.

    Local activities are soft-deleted (``deleted_at``) and a
    ``Delete(Tombstone)`` is enqueued for every inbox the activity was
    previously delivered to (``sent`` targets only), mirroring
    :func:`deletion.cascade_delete_entity` for a single row. Remote
    activities are hard-deleted without fan-out — the remote instance owns
    retraction. Already-retracted activities are a no-op. Flushes without
    committing; the caller owns the transaction.
    """
    from ..federation.activities import create_tombstone_delete_activity
    from .deletion import enqueue_activity_delivery, get_activity_unpublish_info

    if activity.deleted_at is not None:
        return

    if activity.source_type != "local":
        await session.delete(activity)
        await session.flush()
        return

    info = await get_activity_unpublish_info(session, activity)
    activity.deleted_at = datetime.now(timezone.utc)
    if info.inboxes:
        owner = await session.get(User, activity.owner_user_id) if activity.owner_user_id else None
        payload = create_tombstone_delete_activity(info.actor_url, info.source_id)
        enqueue_activity_delivery(info, owner, payload)
    await session.flush()
