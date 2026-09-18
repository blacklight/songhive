"""
Standalone status routes: ``POST /statuses`` creates a status attached to
the author's ``user`` entity — a ``Create(Note)`` activity that federates
when federation is enabled and the visibility federates, and stays local
otherwise.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.schema import SonghiveConfig
from ...models import Visibility
from ...models.audit_log import AuditTargetType
from ...models.user import User
from ...services import activities as activity_service
from ...services import audit
from ...services.federation import ensure_user_actor
from ...services.mentions import CONTENT_TYPE_MARKDOWN
from ...services.storage import StorageService
from .._common import client_ip
from ..deps import get_config, get_current_user, get_db, get_storage_service
from ..middleware.rate_limit import rate_limit_account
from ._common import AudioImportOptions
from .activities import ActivityResponse, _build_activity_response
from .files import apply_audio_import, plan_audio_import

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/statuses")


class StatusCreateRequest(BaseModel):
    """Payload for posting a standalone status."""

    status: Optional[str] = Field(None, max_length=10_000)
    content_type: str = CONTENT_TYPE_MARKDOWN
    visibility: Visibility = Visibility.PUBLIC
    language: Optional[str] = Field(None, max_length=35)
    media_ids: List[str] = Field(default_factory=list)
    track_ids: List[str] = Field(default_factory=list)
    audio_import: AudioImportOptions = Field(default_factory=AudioImportOptions)


@router.post(
    "/",
    response_model=ActivityResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_account)],
)
async def create_status(
    body: StatusCreateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    config: SonghiveConfig = Depends(get_config),
    storage: StorageService = Depends(get_storage_service),
):
    """Post a standalone status.

    ``status`` is the raw source text — Markdown when ``content_type`` is
    ``text/markdown`` (the default), escaped plain text otherwise. ``media_ids``
    attach previously uploaded files and ``track_ids`` attach hosted tracks;
    a status may carry attachments without text. ``audio_import`` controls
    whether attached ``audio/*`` files are also imported into the author's
    library as tracks — imported to their "Uploads" library by default, with
    optional MusicBrainz metadata fetch. The status is recorded as a
    ``create`` activity on the author's profile and federated to the
    ``visibility`` audience when federation is enabled.
    """
    ensure_user_actor(current_user, config)
    # Validated before the status fans out so a bad ``library_id`` fails the
    # request without leaving enqueued deliveries behind.
    import_plan = await plan_audio_import(db, current_user, body.media_ids, body.audio_import)
    activity = await activity_service.create_status(
        db,
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
        action="status.create",
        target_type=AuditTargetType.ACTIVITY,
        target_id=str(activity.id),
        details={"visibility": activity.visibility},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(activity, ["mentions"])
    profile_map = await activity_service.resolve_source_actor_profiles(db, [activity], config)
    summary_map = await activity_service.resolve_interaction_summaries(db, [activity], current_user, config)
    return _build_activity_response(
        activity,
        profile_map.get(str(activity.id), activity_service.ActorProfile()),
        summary_map.get(str(activity.id)),
    )
