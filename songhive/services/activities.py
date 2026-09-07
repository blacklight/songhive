"""
Activity domain service.

This module provides the service layer for the multi-activity federation
model: entity resolution, local activity creation, and the parameter schema
used by the API layer.  Functions take an ``AsyncSession`` and follow the
codebase convention of flushing (not committing) so callers control the
transaction boundary.
"""

import uuid
from typing import Any, Dict, List, Optional, Type

from fastapi import HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..models._enums import Visibility
from ..models.activity import ACTIVITY_ENTITY_TYPES, ACTIVITY_TYPES, Activity, ActivityMention
from ..models.album import Album
from ..models.artist import Artist
from ..models.library import Library
from ..models.playlist import Playlist
from ..models.track import Track
from ..models.user import User
from .acl import can_manage

__all__ = [
    "ACTIVITY_ENTITY_TYPES",
    "ACTIVITY_TYPES",
    "ActivityCreateParams",
    "create_local_activity",
    "resolve_entity",
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
) -> Activity:
    """Create a local activity attached to an entity.

    Raises ``HTTPException`` with status 404 when the entity does not exist,
    422 when the requested visibility exceeds the entity's visibility (or the
    ``activity_type``/``visibility`` values are invalid), and 403 when
    ``author`` may not manage the entity.

    ``mentions`` is a list of pre-resolved mention dicts (``handle``,
    optional ``actor_url`` and ``user_id``); mention extraction, resolution,
    and content rendering are layered on top of this service separately.
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

    if not Visibility.can_contain(activity_visibility, _entity_visibility(entity)):
        raise HTTPException(422, detail="Activity visibility exceeds entity visibility")

    if not await can_manage(session, author, entity_type, entity_id):
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
