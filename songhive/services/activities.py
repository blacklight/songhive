"""
Activity domain service.

This module provides the service layer for the multi-activity federation
model: entity resolution, local activity creation, and the parameter schema
used by the API layer.  Functions take an ``AsyncSession`` and follow the
codebase convention of flushing (not committing) so callers control the
transaction boundary.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Type

from fastapi import HTTPException
from pubby.content import set_object_content
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..config.schema import SonghiveConfig
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

__all__ = [
    "ACTIVITY_ENTITY_TYPES",
    "ACTIVITY_TYPES",
    "ActivityCreateParams",
    "VisibilityRules",
    "can_view_activity",
    "create_local_activity",
    "fan_out_activity",
    "fan_out_like_activity",
    "like_activity",
    "resolve_audience",
    "resolve_entity",
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
    pipeline's ``Mention`` tags are merged in.

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
        if processed.tags:
            tags = obj.setdefault("tag", [])
            seen = {str(tag.get("name", "")).lower() for tag in tags if isinstance(tag, dict)}
            tags.extend(tag for tag in processed.tags if str(tag.get("name", "")).lower() not in seen)
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
