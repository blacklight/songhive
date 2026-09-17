"""
Cross-entity timeline route.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models.user import User
from ...services import activities as activity_service
from ..deps import get_config, get_current_user_optional, get_db
from .activities import ActivityListResponse, _build_activity_response

router = APIRouter(prefix="/timeline")


@router.get("", response_model=ActivityListResponse)
async def get_timeline(
    scope: Optional[str] = Query(None, description="Timeline scope (mine, instance or federated)"),
    mode: str = Query("posts", description="Activity feed mode (posts or all)"),
    include_boosts: bool = Query(True, description="Include boosts in posts mode"),
    include_replies: bool = Query(False, description="Include replies in posts mode"),
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    cursor: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
):
    """
    List the newest visible activities across the instance.

    ``scope=mine`` returns the caller's own activity timeline and requires
    authentication; ``scope=instance`` returns the cross-entity feed of
    local activities on entities the requester may access — anonymous
    requesters see the public subset; ``scope=federated`` returns the same
    feed including activities received from remote instances and
    webmentions. The default scope is ``mine`` for authenticated callers
    and ``instance`` otherwise. A ``following`` scope is
    intentionally not implemented: Songhive does not publish outgoing
    follows, so there is no remote subscription graph to feed it.
    """
    resolved_scope = scope or ("mine" if user is not None else "instance")
    if resolved_scope not in activity_service.TIMELINE_SCOPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid scope")
    if resolved_scope == "mine" and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    activities, next_cursor = await activity_service.list_timeline(
        db,
        user=user,
        scope=resolved_scope,
        mode=mode,
        include_boosts=include_boosts,
        include_replies=include_replies,
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
