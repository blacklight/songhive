"""
Activity interaction routes.
"""

import logging
import re
from datetime import datetime
from typing import Any, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models import Visibility
from ...models.activity import Activity
from ...models.audit_log import AuditTargetType
from ...models.user import User
from ...services import acl
from ...services import activities as activity_service
from ...services import audit, remote_content
from ...services.federation import ensure_user_actor
from ...services.mentions import CONTENT_TYPE_MARKDOWN
from ...services.storage import StorageService
from ...webmentions.service import webmention_display_excerpt
from .._common import client_ip
from ..deps import (
    get_config,
    get_current_user,
    get_current_user_optional,
    get_db,
    get_storage_service,
)
from ..middleware.rate_limit import rate_limit_account
from ._common import AudioImportOptions
from .files import apply_audio_import, plan_audio_import

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/activities")
entity_router = APIRouter()


class ActivityUpdate(BaseModel):
    """Activity partial update."""

    content: Optional[str] = None
    visibility: Optional[Visibility] = None
    content_type: Optional[str] = None
    language: Optional[str] = None
    media_ids: Optional[List[str]] = None
    track_ids: Optional[List[str]] = None
    audio_import: Optional[AudioImportOptions] = None


class ActivityReplyRequest(BaseModel):
    """Payload for posting a reply to an activity."""

    status: Optional[str] = Field(None, max_length=10_000)
    content_type: str = CONTENT_TYPE_MARKDOWN
    visibility: Optional[Visibility] = None
    language: Optional[str] = Field(None, max_length=35)
    media_ids: List[str] = Field(default_factory=list)
    track_ids: List[str] = Field(default_factory=list)
    audio_import: AudioImportOptions = Field(default_factory=AudioImportOptions)


class ActivityMentionResponse(BaseModel):
    """Serialized activity mention."""

    model_config = ConfigDict(from_attributes=True)

    handle: str
    actor_url: Optional[str] = None
    user_id: Optional[str] = None


class PreviewCardResponse(BaseModel):
    """Serialized link-preview card attached to an activity."""

    model_config = ConfigDict(from_attributes=True)

    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    site_name: Optional[str] = None
    type: str = "link"


class WebmentionResponse(BaseModel):
    """Serialized Webmention metadata attached to a ``webmention`` activity."""

    source: str
    target: str
    title: Optional[str] = None
    excerpt: Optional[str] = None
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    author_photo: Optional[str] = None
    published: Optional[datetime] = None
    mention_type: str = "mention"
    tags: List[str] = []


class ActivityRemoteObjectResponse(BaseModel):
    """Summary of the remote music object mirrored by an activity."""

    id: str
    name: Optional[str] = None
    resource_type: Optional[str] = None
    object_type: Optional[str] = None
    domain: Optional[str] = None
    image_url: Optional[str] = None
    url: Optional[str] = None
    duration: Optional[int] = None
    artist_name: Optional[str] = None
    album_name: Optional[str] = None


class ActivityResponse(BaseModel):
    """Serialized activity."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    entity_type: str
    entity_id: str
    activity_type: str
    source_type: str
    source_actor: str
    source_id: str
    local_object_id: Optional[str] = None
    object_url: Optional[str] = None
    object_type: Optional[str] = None
    owner_user_id: Optional[str] = None
    source_actor_avatar_url: Optional[str] = None
    source_actor_display_name: Optional[str] = None
    # The author's handle — local username or ``user@domain`` — resolved
    # from cached actor data so opaque actor ids (e.g. Mastodon
    # ``/ap/users/<id>``) still map to the real username.
    source_actor_handle: Optional[str] = None
    visibility: Visibility
    in_reply_to_activity_id: Optional[str] = None
    content: Optional[str] = None
    content_source: Optional[str] = None
    content_type: Optional[str] = None
    language: Optional[str] = None
    attachments: List[dict] = []
    published_at: datetime
    mentions: List[ActivityMentionResponse] = []
    like_count: int = 0
    boost_count: int = 0
    reply_count: int = 0
    quote_count: int = 0
    liked: bool = False
    boosted: bool = False
    can_interact: bool = True
    preview_card: Optional[PreviewCardResponse] = None
    webmention: Optional[WebmentionResponse] = None
    remote_object: Optional[ActivityRemoteObjectResponse] = None


class ActivityListResponse(BaseModel):
    """A page of activities."""

    activities: List[ActivityResponse]
    next_cursor: Optional[str] = None


class ActivityActorResponse(BaseModel):
    """A known account that liked or boosted an activity."""

    actor: str
    handle: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    username: Optional[str] = None
    profile_url: Optional[str] = None
    published_at: Optional[datetime] = None


class ActivityActorListResponse(BaseModel):
    """The known accounts that liked or boosted an activity."""

    actors: List[ActivityActorResponse]


@entity_router.get("/{entity_type}/{entity_id}/activities", response_model=ActivityListResponse)
async def list_entity_activities(
    entity_type: str,
    entity_id: str,
    activity_type: Optional[str] = None,
    source_type: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    List the activities attached to an entity, newest first.

    Anonymous requesters see only ``public`` activities on publicly
    accessible entities; authenticated users additionally see ``local`` and
    ``followers`` activities, activities they own, and ``mentioned``
    activities that name them. Pass the returned ``next_cursor`` back as
    ``cursor`` to fetch the next page.
    """
    if entity_type not in activity_service.ACTIVITY_ENTITY_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid entity_type")

    entity = await activity_service.resolve_entity(db, entity_type, entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")

    if not await acl.can_access(db, user, entity_type, entity_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this entity",
        )

    activities, next_cursor = await activity_service.list_activities(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        user=user,
        activity_type=activity_type,
        source_type=source_type,
        cursor=cursor,
        limit=limit,
    )
    profile_map = await activity_service.resolve_source_actor_profiles(db, activities, config)
    summary_map = await activity_service.resolve_interaction_summaries(db, activities, user, config)
    return ActivityListResponse(
        activities=[
            _build_activity_response(
                a,
                profile_map.get(str(a.id), activity_service.ActorProfile()),
                summary_map.get(str(a.id)),
            )
            for a in activities
        ],
        next_cursor=next_cursor,
    )


def _activity_attachments(activity: Activity) -> List[dict]:
    """Return the ActivityPub attachment docs of an activity's embedded object."""
    payload = activity.payload
    if not isinstance(payload, dict):
        return []
    obj = payload.get("object")
    if not isinstance(obj, dict):
        return []
    return [a for a in obj.get("attachment") or [] if isinstance(a, dict)]


def _activity_object_url(activity: Activity) -> Optional[str]:
    """
    Return the dereferenceable id of an activity's object.

    ``Create``-style payloads embed the object document — its ``id`` is the
    activity's own object id. ``Like``/``Announce`` payloads reference the
    reacted object as a bare id, which is exactly the link a reaction card
    should point at.
    """
    payload = activity.payload
    if not isinstance(payload, dict):
        return None
    obj = payload.get("object")
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        obj_id = obj.get("id")
        return obj_id if isinstance(obj_id, str) and obj_id else None
    return None


def _activity_webmention(activity: Activity) -> Optional[WebmentionResponse]:
    """Return the stored Webmention metadata of a ``webmention`` activity."""
    payload = activity.payload
    if not isinstance(payload, dict):
        return None
    mention = payload.get("webmention")
    if not isinstance(mention, dict):
        return None
    raw_metadata = mention.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_mf2 = metadata.get("mf2")
    mf2 = raw_mf2 if isinstance(raw_mf2, dict) else {}
    raw_categories = mf2.get("category")
    categories = raw_categories if isinstance(raw_categories, list) else []
    published = mention.get("published")
    return WebmentionResponse(
        source=mention.get("source") or "",
        target=mention.get("target") or "",
        title=mention.get("title"),
        excerpt=webmention_display_excerpt(mention.get("excerpt"), mention.get("content")),
        author_name=mention.get("author_name"),
        author_url=mention.get("author_url"),
        author_photo=mention.get("author_photo"),
        published=datetime.fromisoformat(published) if isinstance(published, str) else None,
        mention_type=mention.get("mention_type") or "mention",
        tags=[str(name) for name in categories if isinstance(name, str) and name],
    )


def _build_activity_response(
    activity: Activity,
    profile: Optional[activity_service.ActorProfile],
    summary: Optional[activity_service.InteractionSummary] = None,
) -> ActivityResponse:
    """Build an ``ActivityResponse`` with profile and interaction summary."""
    response = ActivityResponse.model_validate(activity)
    response.source_actor_avatar_url = profile.avatar_url if profile else None
    response.source_actor_display_name = profile.display_name if profile else None
    response.source_actor_handle = profile.handle if profile else None
    response.attachments = _activity_attachments(activity)
    if summary is not None:
        response.like_count = summary.like_count
        response.boost_count = summary.boost_count
        response.reply_count = summary.reply_count
        response.quote_count = summary.quote_count
        response.liked = summary.liked
        response.boosted = summary.boosted
    response.object_url = _activity_object_url(activity)
    response.object_type = activity_service._activity_object_type(activity)
    response.webmention = _activity_webmention(activity)
    remote_object = remote_content.activity_remote_object(activity)
    response.remote_object = ActivityRemoteObjectResponse(**remote_object) if remote_object is not None else None
    # Interactions target content activities — reacting to a reaction (or a
    # tombstone) is meaningless, so cards for those types render no action
    # bar; the embedded object's card carries its own.
    response.can_interact = activity.activity_type not in ("like", "announce", "delete")
    return response


async def _viewable_activity_response(
    activity: Optional[Activity],
    request: Request,
    db: AsyncSession,
    user: Optional[User],
) -> ActivityResponse:
    """Serialize ``activity`` after enforcing view permissions."""
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await activity_service.can_view_activity(db, user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    await db.refresh(activity, ["mentions"])
    config = get_config(request)
    profile_map = await activity_service.resolve_source_actor_profiles(db, [activity], config)
    summary_map = await activity_service.resolve_interaction_summaries(db, [activity], user, config)
    return _build_activity_response(
        activity,
        profile_map.get(str(activity.id), activity_service.ActorProfile()),
        summary_map.get(str(activity.id)),
    )


async def _resolve_activity_by_object_url(db: AsyncSession, url: str) -> Optional[Activity]:
    """
    Resolve an object URL or id to a known activity row.

    ``source_id`` is matched directly, which covers local object URLs and
    materialized remote objects alike; the ``/objects/{id}`` permalink form
    additionally matches ``local_object_id`` (and a bare ``source_id``).
    """
    conditions = [Activity.source_id == url]
    match = re.search(r"/objects/([^/?#]+)/?$", urlparse(url).path or "")
    if match:
        object_id = match.group(1)
        conditions += [
            Activity.local_object_id == object_id,
            Activity.source_id == object_id,
        ]
    return await db.scalar(select(Activity).where(or_(*conditions)).limit(1))


def _is_activity_author(activity: Activity, user: User) -> bool:
    """Return whether ``user`` authored ``activity``.

    Authors may edit or retract their own local activities — e.g. a track
    share published by a non-owner — even when they cannot manage the
    activity's entity.
    """
    return activity.owner_user_id is not None and str(activity.owner_user_id) == str(user.id)


@router.get("/lookup", response_model=ActivityResponse)
async def lookup_activity(
    request: Request,
    url: str = Query(..., max_length=2048),
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Resolve an object URL to a stored activity.

    Lets clients map a federated object id — a remote note materialized as
    a reply, a local ``{actor}/objects/{id}`` permalink — to the activity
    row the SPA can render, instead of treating opaque remote ids as
    human-facing URLs.
    """
    activity = await _resolve_activity_by_object_url(db, url)
    return await _viewable_activity_response(activity, request, db, user)


@router.get("/{activity_id}", response_model=ActivityResponse)
async def get_activity(
    activity_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    Fetch a single activity.

    Anonymous requesters may read ``public`` activities on publicly
    accessible entities; authenticated users get the wider visibilities
    ``can_view_activity`` grants, and the response's interaction summary
    reflects their own reactions.
    """
    activity = await db.get(Activity, activity_id)
    return await _viewable_activity_response(activity, request, db, user)


@router.patch("/{activity_id}", response_model=ActivityResponse)
async def update_activity(
    activity_id: str,
    body: ActivityUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """
    Update an activity's content, format, language, attachments and/or
    visibility.

    Only the fields present in the request body are changed; ``language``
    accepts ``null`` to clear it and ``media_ids``/``track_ids`` accept
    empty lists to remove all user-managed attachments (entity-owned
    attachments, like a shared track's own ``Audio`` doc, are preserved).
    File attachments must be owned by the requester and tracks must be
    accessible to them; each category is limited to four entries.

    Content edits fan an ``Update`` carrying the rebuilt object out to the
    inboxes that already received the activity; visibility edits cascade an
    ``Update`` (new audience) or a ``Delete(Tombstone)`` (non-federating
    visibility) through ``cascade_visibility_update``.
    """
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    # Reactions carry no editable content — the object is a bare reference
    # and the visibility is inherited from the reacted activity — and
    # Webmentions mirror remote content owned by the source site.
    if activity.activity_type in ("like", "announce", "webmention"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Reaction activities are not editable",
        )

    if not _is_activity_author(activity, current_user) and not await acl.can_manage(
        db, current_user, activity.entity_type, activity.entity_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to edit this activity",
        )

    config = get_config(request)
    old_visibility = activity.visibility
    fields = body.model_fields_set
    content_fields = {"content", "content_type", "language", "media_ids", "track_ids"}

    # ``audio_import`` imports the post's audio file attachments into the
    # editor's library; it applies to the edited ``media_ids`` when given,
    # else the activity's current file attachments. The plan is validated
    # up front so a bad ``library_id`` fails before the activity mutates.
    import_plan = None
    if body.audio_import is not None:
        if "media_ids" in fields:
            import_media_ids = body.media_ids or []
        else:
            obj = activity.payload.get("object") if isinstance(activity.payload, dict) else None
            import_media_ids, _ = activity_service._attachment_source_ids(obj) if isinstance(obj, dict) else ([], [])
        import_plan = await plan_audio_import(db, current_user, import_media_ids, body.audio_import)

    if fields & content_fields:
        update_kwargs: dict = {
            "config": config,
            "editor": current_user,
            "audience": body.visibility,
        }
        if "content" in fields:
            update_kwargs["content_source"] = body.content
        if "content_type" in fields:
            update_kwargs["content_type"] = body.content_type
        if "language" in fields:
            update_kwargs["language"] = body.language
        if "media_ids" in fields:
            update_kwargs["media_ids"] = body.media_ids
        if "track_ids" in fields:
            update_kwargs["track_ids"] = body.track_ids
        await activity_service.update_activity(db, activity, **update_kwargs)
    if body.visibility is not None:
        # cascade_visibility_update resolves the entity and enforces
        # containment internally (404 missing entity, 422 violation).
        await activity_service.VisibilityRules.cascade_visibility_update(db, activity, body.visibility)
    if fields & content_fields:
        # Fan out after the visibility edit so the Update carries the final
        # audience; it no-ops when the (new) visibility does not federate.
        try:
            await activity_service.fan_out_activity_update(db, activity, config)
        except Exception as e:
            # Fan-out is best-effort: a broker or resolution failure must not
            # fail the edit itself.
            logger.exception("Failed to fan out update for activity %s: %s: %s", activity_id, type(e), e)

    await apply_audio_import(db, storage, current_user, import_plan)

    details: dict = {"entity_type": activity.entity_type, "entity_id": activity.entity_id}
    if body.visibility is not None:
        details["visibility"] = {"old": old_visibility, "new": activity.visibility}
    if "content" in fields:
        details["content_changed"] = True
    if "content_type" in fields:
        details["content_type"] = activity.content_type
    if "language" in fields:
        details["language"] = activity.language
    if fields & {"media_ids", "track_ids"}:
        details["attachments_changed"] = True

    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="activity.update",
        target_type=AuditTargetType.ACTIVITY,
        target_id=str(activity.id),
        details=details,
        ip_address=client_ip(request),
    )

    await db.refresh(activity, ["mentions"])
    profile_map = await activity_service.resolve_source_actor_profiles(db, [activity], config)
    summary_map = await activity_service.resolve_interaction_summaries(db, [activity], current_user, config)
    await db.commit()
    return _build_activity_response(
        activity,
        profile_map.get(str(activity.id), activity_service.ActorProfile()),
        summary_map.get(str(activity.id)),
    )


@router.delete("/{activity_id}")
async def delete_activity(
    activity_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retract an activity.

    Local activities are soft-deleted and a ``Delete(Tombstone)`` is fanned
    out to every inbox the activity previously reached; remote activities
    are simply removed locally.
    """
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not _is_activity_author(activity, current_user) and not await acl.can_manage(
        db, current_user, activity.entity_type, activity.entity_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this activity",
        )

    await activity_service.retract_activity(db, activity)
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="activity.delete",
        target_type=AuditTargetType.ACTIVITY,
        target_id=str(activity.id),
        details={
            "entity_type": activity.entity_type,
            "entity_id": activity.entity_id,
            "visibility": activity.visibility,
        },
        ip_address=client_ip(request),
    )
    await db.commit()
    return {"status": "ok"}


@router.post("/{activity_id}/like", status_code=status.HTTP_201_CREATED)
async def like_activity(
    activity_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Like an activity."""
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await activity_service.can_view_activity(db, current_user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    config = get_config(request)
    ensure_user_actor(current_user, config)
    like = await activity_service.like_activity(db, activity=activity, author=current_user)
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="activity.like",
        target_type=AuditTargetType.ACTIVITY,
        target_id=str(activity.id),
        details={
            "entity_type": activity.entity_type,
            "entity_id": activity.entity_id,
            "visibility": activity.visibility,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    if config.federation.enabled and current_user.private_key_pem:
        try:
            await activity_service.fan_out_like_activity(
                db, like=like, target=activity, author=current_user, config=config
            )
            await db.commit()
        except Exception as e:
            # Fan-out is best-effort: a broker or resolution failure must not
            # fail the like itself.
            logger.exception("Failed to fan out like for activity %s: %s: %s", activity_id, type(e), e)

    return {"status": "ok", "activity_id": str(like.id)}


@router.post("/{activity_id}/boost", status_code=status.HTTP_201_CREATED)
async def boost_activity(
    activity_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Boost an activity as the current user (ActivityPub ``Announce``).

    The boost is stored as an ``announce`` activity attached to the same
    entity, federated to the boosted activity's audience plus — for remote
    targets — the author's inbox. Boosting a deleted activity returns 404
    and boosting it twice returns 400.
    """
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await activity_service.can_view_activity(db, current_user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    config = get_config(request)
    ensure_user_actor(current_user, config)
    boost = await activity_service.boost_activity(db, activity=activity, author=current_user)
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="activity.boost",
        target_type=AuditTargetType.ACTIVITY,
        target_id=str(activity.id),
        details={
            "entity_type": activity.entity_type,
            "entity_id": activity.entity_id,
            "visibility": activity.visibility,
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    if config.federation.enabled and current_user.private_key_pem:
        try:
            await activity_service.fan_out_boost_activity(
                db, boost=boost, target=activity, author=current_user, config=config
            )
            await db.commit()
        except Exception as e:
            # Fan-out is best-effort: a broker or resolution failure must not
            # fail the boost itself.
            logger.exception("Failed to fan out boost for activity %s: %s: %s", activity_id, type(e), e)

    return {"status": "ok", "activity_id": str(boost.id)}


async def _unreact(
    activity_id: str,
    interaction_type: str,
    audit_action: str,
    request: Request,
    current_user: User,
    db: AsyncSession,
) -> dict:
    """Shared ``DELETE /{id}/like`` + ``DELETE /{id}/boost`` handler."""
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await activity_service.can_view_activity(db, current_user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    config = get_config(request)
    ensure_user_actor(current_user, config)
    reaction = await activity_service.unreact_activity(
        db, activity=activity, author=current_user, interaction_type=interaction_type
    )
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action=audit_action,
        target_type=AuditTargetType.ACTIVITY,
        target_id=str(activity.id),
        details={
            "entity_type": activity.entity_type,
            "entity_id": activity.entity_id,
            "interaction_id": str(reaction.id),
        },
        ip_address=client_ip(request),
    )
    await db.commit()

    if config.federation.enabled and current_user.private_key_pem:
        try:
            await activity_service.fan_out_unreaction_activity(
                db, reaction=reaction, author=current_user, config=config
            )
            await db.commit()
        except Exception as e:
            # Fan-out is best-effort: a broker or resolution failure must not
            # fail the retraction itself.
            logger.exception(
                "Failed to fan out %s undo for activity %s: %s: %s",
                interaction_type,
                activity_id,
                type(e),
                e,
            )

    return {"status": "ok"}


@router.delete("/{activity_id}/like")
async def unlike_activity(
    activity_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retract the current user's like of an activity.

    The like is soft-deleted (the activity can be liked again) and, when
    the like was federated, an ``Undo(Like)`` is delivered to the inboxes
    it reached. Unliking an activity that was not liked returns 404.
    """
    return await _unreact(activity_id, "like", "activity.unlike", request, current_user, db)


@router.delete("/{activity_id}/boost")
async def unboost_activity(
    activity_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retract the current user's boost of an activity.

    The boost is soft-deleted (the activity can be boosted again) and, when
    the boost was federated, an ``Undo(Announce)`` is delivered to the
    inboxes it reached. Unboosting an activity that was not boosted
    returns 404.
    """
    return await _unreact(activity_id, "announce", "activity.unboost", request, current_user, db)


@router.post(
    "/{activity_id}/reply",
    response_model=ActivityResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def reply_activity(
    activity_id: str,
    body: ActivityReplyRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Post a reply to an activity as the current user.

    The reply is a ``Create(Note)`` whose object carries ``inReplyTo`` and
    inherits — unless a narrower ``visibility`` is requested — the
    replied-to activity's visibility. It is attached to the same entity,
    federated to its audience, and notified to the replied-to author. A
    reply to a deleted activity returns 404.
    """
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await activity_service.can_view_activity(db, current_user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    config = get_config(request)
    ensure_user_actor(current_user, config)
    import_plan = await plan_audio_import(db, current_user, body.media_ids, body.audio_import)
    reply = await activity_service.reply_to_activity(
        db,
        activity=activity,
        author=current_user,
        config=config,
        status_text=body.status,
        content_type=body.content_type,
        visibility=body.visibility,
        language=body.language,
        media_ids=body.media_ids,
        track_ids=body.track_ids,
    )
    await apply_audio_import(db, storage, current_user, import_plan)
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="activity.reply",
        target_type=AuditTargetType.ACTIVITY,
        target_id=str(activity.id),
        details={
            "entity_type": activity.entity_type,
            "entity_id": activity.entity_id,
            "reply_id": str(reply.id),
        },
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(reply, ["mentions"])

    profile_map = await activity_service.resolve_source_actor_profiles(db, [reply], config)
    summary_map = await activity_service.resolve_interaction_summaries(db, [reply], current_user, config)
    return _build_activity_response(
        reply,
        profile_map.get(str(reply.id), activity_service.ActorProfile()),
        summary_map.get(str(reply.id)),
    )


@router.post(
    "/{activity_id}/quote",
    response_model=ActivityResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def quote_activity(
    activity_id: str,
    body: ActivityReplyRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    """Post a quote of an activity as the current user.

    The quote is a ``Create(Note)`` whose object references the quoted
    post through the FEP-0449 ``quote``/Mastodon ``quoteUrl``/Misskey
    ``_misskey_quote`` fields and inherits — unless a narrower
    ``visibility`` is requested — the quoted activity's visibility. It is
    attached to the same entity, federated to its audience, and notified
    to the quoted author. Quoting a remote activity additionally sends a
    FEP-044f ``QuoteRequest`` to the remote author's inbox; quoting a
    local user's post self-issues the ``QuoteAuthorization``. A quote of
    a deleted activity returns 404.
    """
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")

    if not await activity_service.can_view_activity(db, current_user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    config = get_config(request)
    ensure_user_actor(current_user, config)
    import_plan = await plan_audio_import(db, current_user, body.media_ids, body.audio_import)
    quote = await activity_service.quote_activity(
        db,
        activity=activity,
        author=current_user,
        config=config,
        status_text=body.status,
        content_type=body.content_type,
        visibility=body.visibility,
        language=body.language,
        media_ids=body.media_ids,
        track_ids=body.track_ids,
    )
    await apply_audio_import(db, storage, current_user, import_plan)
    await audit.log_action(
        db,
        actor_id=current_user.id,
        action="activity.quote",
        target_type=AuditTargetType.ACTIVITY,
        target_id=str(activity.id),
        details={
            "entity_type": activity.entity_type,
            "entity_id": activity.entity_id,
            "quote_id": str(quote.id),
        },
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(quote, ["mentions"])

    profile_map = await activity_service.resolve_source_actor_profiles(db, [quote], config)
    summary_map = await activity_service.resolve_interaction_summaries(db, [quote], current_user, config)
    return _build_activity_response(
        quote,
        profile_map.get(str(quote.id), activity_service.ActorProfile()),
        summary_map.get(str(quote.id)),
    )


def _actor_handle(actor: activity_service.InteractionActor, actor_handles: Optional[dict] = None) -> str:
    """Derive a display handle: ``@username`` for locals, ``@name@host`` remote."""
    if actor.username:
        return f"@{actor.username}"
    cached = (actor_handles or {}).get(actor.actor)
    if cached:
        return f"@{cached}"
    return activity_service._remote_actor_handle(actor.actor)


async def _list_interactors(
    activity_id: str,
    interaction_type: str,
    db: AsyncSession,
    config: SonghiveConfig,
    user: Optional[User],
) -> "ActivityActorListResponse":
    """Shared ``/{id}/likes`` + ``/{id}/boosts`` handler."""
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    if not await activity_service.can_view_activity(db, user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    actors = await activity_service.list_activity_interactors(
        db, activity=activity, interaction_type=interaction_type, config=config
    )
    actor_handles = await remote_content.resolve_actor_handle_map(config, [a.actor for a in actors])
    return ActivityActorListResponse(
        actors=[
            ActivityActorResponse(
                actor=a.actor,
                handle=_actor_handle(a, actor_handles),
                display_name=a.display_name,
                avatar_url=a.avatar_url,
                username=a.username,
                profile_url=a.profile_url,
                published_at=a.published_at,
            )
            for a in actors
        ]
    )


@router.get("/{activity_id}/likes", response_model=ActivityActorListResponse)
async def list_activity_likes(
    activity_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """List the known accounts that liked an activity, newest first."""
    return await _list_interactors(activity_id, "like", db, get_config(request), user)


@router.get("/{activity_id}/boosts", response_model=ActivityActorListResponse)
async def list_activity_boosts(
    activity_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """List the known accounts that boosted an activity, newest first."""
    return await _list_interactors(activity_id, "announce", db, get_config(request), user)


class ReplyActivityListResponse(BaseModel):
    """The known replies in an activity's thread, local and federated."""

    activities: List[ActivityResponse]
    remote_replies: List[dict] = []


def _remote_reply_payload(interaction: Any, actor_handle: Optional[str] = None) -> dict:
    """Serialize a Pubby reply ``Interaction`` for the reply list."""
    meta = interaction.metadata or {}
    raw = meta.get("raw_object") if isinstance(meta, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    attachments = [a for a in raw.get("attachment") or [] if isinstance(a, dict)]
    published = interaction.published or raw.get("published")
    if isinstance(published, str):
        try:
            published = datetime.fromisoformat(published)
        except ValueError:
            published = None
    content_map = raw.get("contentMap")
    language = next(iter(content_map)) if isinstance(content_map, dict) and content_map else None
    return {
        "id": interaction.object_id or interaction.activity_id,
        "object_id": interaction.object_id or raw.get("id"),
        "in_reply_to": interaction.target_resource or raw.get("inReplyTo"),
        "source_actor": interaction.source_actor_id,
        "source_actor_handle": actor_handle,
        "source_actor_name": interaction.author_name or None,
        "source_actor_url": interaction.author_url or None,
        "source_actor_avatar_url": interaction.author_photo or None,
        "content": interaction.content or raw.get("content") or None,
        "content_type": None,
        "language": language,
        "attachments": attachments,
        "url": raw.get("url"),
        "published_at": published,
    }


@router.get("/{activity_id}/replies", response_model=ReplyActivityListResponse)
async def list_activity_replies(
    activity_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    List the known replies in an activity's thread, oldest first.

    Local replies are every visibility-filtered ``reply`` descendant —
    replies to replies included — serialized as full activity cards;
    federated replies come from Pubby's interaction storage as compact
    ``raw_object``-backed records carrying ``in_reply_to`` so clients can
    regroup them into threads.
    """
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    if not await activity_service.can_view_activity(db, user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    config = get_config(request)
    local, remote = await activity_service.list_activity_replies(db, activity=activity, user=user, config=config)
    profile_map = await activity_service.resolve_source_actor_profiles(db, local, config)
    summary_map = await activity_service.resolve_interaction_summaries(db, local, user, config)
    remote_handles = await remote_content.resolve_actor_handle_map(config, [i.source_actor_id for i in remote])
    return ReplyActivityListResponse(
        activities=[
            _build_activity_response(
                a,
                profile_map.get(str(a.id), activity_service.ActorProfile()),
                summary_map.get(str(a.id)),
            )
            for a in local
        ],
        remote_replies=[_remote_reply_payload(i, remote_handles.get(i.source_actor_id)) for i in remote],
    )


class QuoteActivityListResponse(BaseModel):
    """The known quotes of an activity, local and federated."""

    activities: List[ActivityResponse] = []
    remote_quotes: List[dict] = []


def _remote_quote_payload(interaction: Any, actor_handle: Optional[str] = None) -> dict:
    """
    Serialize a Pubby quote ``Interaction`` for the quote list.

    Same card fields as ``_remote_reply_payload``; ``quoted`` carries the
    quoted object id (the interaction's target) in place of the reply's
    ``in_reply_to`` parent pointer — quotes attach to the object they
    quote, not to a thread parent.
    """
    payload = _remote_reply_payload(interaction, actor_handle)
    payload.pop("in_reply_to", None)
    payload["quoted"] = interaction.target_resource or None
    return payload


@router.get("/{activity_id}/quotes", response_model=QuoteActivityListResponse)
async def list_activity_quotes(
    activity_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """
    List the known quotes of an activity, oldest first.

    Local quotes — locally authored ones and remote quotes materialized
    into ``Activity`` rows — are serialized as full activity cards;
    federated quotes that were never materialized come from Pubby's
    interaction storage as compact ``raw_object``-backed records carrying
    ``quoted`` (the quoted object id).
    """
    activity = await db.get(Activity, activity_id)
    if activity is None or activity.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    if not await activity_service.can_view_activity(db, user, activity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this activity",
        )

    config = get_config(request)
    local, remote = await activity_service.list_activity_quotes(db, activity=activity, user=user, config=config)
    profile_map = await activity_service.resolve_source_actor_profiles(db, local, config)
    summary_map = await activity_service.resolve_interaction_summaries(db, local, user, config)
    remote_handles = await remote_content.resolve_actor_handle_map(config, [i.source_actor_id for i in remote])
    return QuoteActivityListResponse(
        activities=[
            _build_activity_response(
                a,
                profile_map.get(str(a.id), activity_service.ActorProfile()),
                summary_map.get(str(a.id)),
            )
            for a in local
        ],
        remote_quotes=[_remote_quote_payload(i, remote_handles.get(i.source_actor_id)) for i in remote],
    )
